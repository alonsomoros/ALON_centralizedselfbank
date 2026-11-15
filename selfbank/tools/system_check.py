"""Pruebas de sistema y completitud con los datos REALES importados. Imprime solo OK/FALLO, contadores y porcentajes.

  python -m selfbank.tools.system_check --db ~/.selfbank/selfbank.db --raw-dir ~/.selfbank/raw/prod [--llm]

Opciones que SÍ muestran datos reales (para mirarlas tú en tu terminal; no las pegues en chats ni issues):
  --show-monthly N     gasto e ingreso de cada uno de los últimos N meses, para compararlos con la app del banco
  --uncategorized N    los N comercios con más movimientos que han quedado en «otros», para ampliar categories.local.json

Secciones: 1 integridad de la importación · 2 calidad de la extracción · 3 las preguntas del dataset de punta a punta ·
4 contraste del SQL con un cálculo independiente · 5 seguridad de los datos.
"""

import argparse
import json
import os
import random
import re
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from collections import Counter
from datetime import date
from pathlib import Path

from selfbank import contract, db, ingest
from selfbank.categorize import Categorizer
from selfbank.engine import Engine
from selfbank.executor import Executor
from selfbank.llm import OllamaClient
from selfbank.tools.ingest_cli import latest_files
from selfbank.tools.oracle import Oracle, compare_totals

DATASET = Path(__file__).resolve().parents[2] / "bench" / "consultas" / "questions.jsonl"
FAILURES = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'OK   ' if ok else 'FALLO'}] {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILURES.append(label)
    return ok


def pct(part, whole) -> str:
    return f"{100 * part / whole:.0f} %" if whole else "n/a"


def percentile(values, p):
    ordered = sorted(values)
    return ordered[max(0, -(-p * len(ordered) // 100) - 1)] if ordered else None


def load_raw(raw_dir: Path) -> list:
    rows = []
    for files in latest_files(raw_dir).values():
        if "transactions" in files:
            for page in json.loads(files["transactions"].read_text(encoding="utf-8")):
                rows += page.get("transactions", [])
    return rows


def section_integrity(conn, raw_rows, categorizer):
    print("\n1. Integridad de la importación")
    booked = [r for r in raw_rows if (r.get("status") or "BOOK") == "BOOK"]
    check("todos los movimientos en bruto son BOOK", len(booked) == len(raw_rows), f"{len(raw_rows)} leídos")
    in_db = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    check("mismo nº de movimientos en bruto y en la base", in_db == len(booked), f"bruto {len(booked)} · base {in_db}")
    sums = {"out": 0, "in": 0}
    for r in booked:
        sums["out" if r["credit_debit_indicator"] == "DBIT" else "in"] += ingest.parse_cents(r["transaction_amount"]["amount"])
    for direction, label in (("out", "gastos"), ("in", "ingresos")):
        total = conn.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM transactions WHERE direction = ?", (direction,)).fetchone()[0]
        check(f"la suma de {label} coincide con el bruto (al céntimo)", total == sums[direction])
    keys = Counter()
    for r in booked:
        row = ingest.normalize_transaction(r, "k", categorizer)
        keys[ingest.dedup_hash("k", row["booking_date"], row["value_date"], row["amount_cents"], row["direction"], row["description"])] += 1
    repeated = sum(n for n in keys.values() if n > 1)
    print(f"  [info ] movimientos idénticos el mismo día (se conservan todos): {repeated} en {sum(1 for n in keys.values() if n > 1)} grupos")
    memory = db.connect(":memory:")
    try:
        pages = [{"transactions": raw_rows}]
        ingest.ingest_pages(memory, pages, "k", categorizer)
        again = ingest.ingest_pages(memory, pages, "k", categorizer)
        check("reimportar lo mismo no añade nada (idempotente)", again["inserted"] == 0)
        check("importar desde cero da el mismo nº que la base real", memory.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == in_db)
    finally:
        memory.close()
    low, high = conn.execute("SELECT MIN(booking_date), MAX(booking_date) FROM transactions").fetchone()
    check("fechas válidas", bool(low) and bool(date.fromisoformat(low)) and bool(date.fromisoformat(high)), f"{low} → {high}")
    check("importes siempre positivos y sentido válido", conn.execute("SELECT COUNT(*) FROM transactions WHERE amount_cents < 0 OR direction NOT IN ('in','out')").fetchone()[0] == 0)


def section_extraction(conn):
    print("\n2. Calidad de la extracción (comercio y categoría)")
    kinds = Counter(k for (k,) in conn.execute("SELECT kind FROM transactions"))
    print("  [info ] tipos de movimiento:", ", ".join(f"{k}={n}" for k, n in kinds.most_common()))
    purchases = kinds.get("purchase", 0)
    with_merchant = conn.execute("SELECT COUNT(*) FROM transactions WHERE kind = 'purchase' AND merchant IS NOT NULL").fetchone()[0]
    check("compras con comercio extraído ≥ 95 %", purchases == 0 or with_merchant / purchases >= 0.95, f"{with_merchant}/{purchases} = {pct(with_merchant, purchases)}")
    categorized = conn.execute("SELECT COUNT(*) FROM transactions WHERE kind = 'purchase' AND category != 'otros'").fetchone()[0]
    print(f"  [info ] compras con categoría distinta de «otros»: {categorized}/{purchases} = {pct(categorized, purchases)} (se amplía con categories.local.json)")
    for kind in ("bizum", "transfer"):
        total = kinds.get(kind, 0)
        named = conn.execute("SELECT COUNT(*) FROM transactions WHERE kind = ? AND merchant IS NOT NULL", (kind,)).fetchone()[0]
        check(f"{kind}: contraparte extraída ≥ 90 %", total == 0 or named / total >= 0.9, f"{named}/{total} = {pct(named, total)}")
    distribution = Counter(c for (c,) in conn.execute("SELECT category FROM transactions WHERE direction = 'out'"))
    print("  [info ] gastos por categoría (nº de movimientos):", ", ".join(f"{k}={n}" for k, n in distribution.most_common()))


def section_dataset(engine_factory, label, llm=None):
    print(f"\n3. Las preguntas del dataset de punta a punta ({label})")
    if not DATASET.exists():
        print("  [info ] no hay dataset en esta máquina")
        return
    items = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    status, source_times, errors, empty, exact = Counter(), {"rules": [], "model": []}, [], 0, 0
    for item in items:
        today = date.fromisoformat(item["today"])
        engine = engine_factory(today)
        started = time.perf_counter()
        try:
            got = engine.ask(item["question"])
        except Exception as error:  # cualquier excepción es un fallo de completitud
            errors.append((item["id"], type(error).__name__))
            continue
        source_times[got["source"]].append(time.perf_counter() - started)
        status[got["status"]] += 1
        empty += got["status"] == "answered" and got["answer"]["kind"] == "none"
        want = contract.expand(item["expected"], today)
        have = dict(got["interpretation"]["query"])
        for query in (want, have):
            query.pop("clarification", None)
            if isinstance(query.get("text"), str):
                query["text"] = query["text"].strip().lower()
        exact += have == want
    check("ninguna pregunta provoca una excepción", not errors, ", ".join(f"{i}:{e}" for i, e in errors))
    if llm is not None:
        check("el modelo contestó a todas las preguntas que le tocaban", llm.failures == 0, f"{llm.failures} fallos; último: {llm.last_error}" if llm.failures else "")
    print(f"  [info ] estados: {dict(status)}")
    print(f"  [info ] «answered» sin datos que mostrar (p. ej. un comercio que no existe en tus datos): {empty}")
    print(f"  [info ] interpretación idéntica a la esperada: {exact}/{len(items)} = {pct(exact, len(items))} (las reglas se afinaron con estas preguntas: cota optimista)")
    for source, times in source_times.items():
        if times:
            p50, p95 = statistics.median(times), percentile(times, 95)
            print(f"  [info ] fuente {source}: {len(times)} preguntas · mediana {p50 * 1000:.0f} ms · p95 {p95 * 1000:.0f} ms")
    rules_times = source_times["rules"]
    check("las preguntas por reglas responden en < 500 ms (p95)", not rules_times or percentile(rules_times, 95) < 0.5)


def section_oracle(conn, raw_rows, categorizer):
    print("\n4. Contraste del SQL con un cálculo independiente en Python (300 consultas aleatorias)")
    oracle = Oracle(raw_rows, categorizer)
    same, compared = compare_totals(Executor(conn), oracle, date.today(), 300, 2026, ["mercadona", "netflix", "glovo", "zara", "bizum", "nomina"])
    check("el SQL y el cálculo independiente coinciden en todas", same == compared and compared > 0, f"{same}/{compared}")


def section_security(conn):
    print("\n5. Seguridad de los datos")
    patterns = {"tarjeta de 9+ dígitos": r"\d{9,}", "dígitos tras asterisco": r"\*\d{5,}"}
    leaks = Counter()
    for column in ("description", "description_norm", "merchant", "merchant_norm"):
        for (value,) in conn.execute(f"SELECT {column} FROM transactions WHERE {column} IS NOT NULL"):
            for name, pattern in patterns.items():
                if re.search(pattern, value):
                    leaks[name] += 1
    check("la base no contiene números de tarjeta ni secuencias largas de dígitos", not leaks, ", ".join(f"{k}={v}" for k, v in leaks.items()))
    allowed = set(contract.RESOLVED_KEYS)
    bad = 0
    for (payload,) in conn.execute("SELECT query_json FROM history"):
        try:
            bad += set(json.loads(payload)) != allowed
        except ValueError:
            bad += 1
    count = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    check("el historial guarda solo la consulta interpretada (15 campos), nunca resultados", bad == 0, f"{count} preguntas revisadas")
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    if path and os.name == "posix":
        check("el fichero de la base tiene permisos 600", os.stat(path).st_mode & 0o777 == 0o600, oct(os.stat(path).st_mode & 0o777))
    ro = db.connect(path, read_only=True) if path else None
    if ro:
        try:
            ro.execute("DELETE FROM transactions")
            check("la conexión de consulta es de solo lectura", False)
        except Exception:
            check("la conexión de consulta es de solo lectura", True)
        finally:
            ro.close()


def show_monthly(conn, months: int):
    print(f"\nGasto e ingreso por mes, últimos {months} (DATOS REALES: para comparar con la app del banco)")
    rows = conn.execute(
        "SELECT strftime('%Y-%m', booking_date) m, SUM(CASE direction WHEN 'out' THEN amount_cents ELSE 0 END), "
        "SUM(CASE direction WHEN 'in' THEN amount_cents ELSE 0 END), COUNT(*) FROM transactions GROUP BY m ORDER BY m DESC LIMIT ?", (months,)).fetchall()
    for month, out, inn, n in rows:
        print(f"  {month}  gastos {out / 100:>10,.2f} €   ingresos {inn / 100:>10,.2f} €   ({n} movimientos)")


def show_uncategorized(conn, n: int):
    print(f"\nComercios con más movimientos en «otros», top {n} (DATOS REALES: añade palabras clave a ~/.selfbank/categories.local.json)")
    for merchant, count in conn.execute(
            "SELECT COALESCE(merchant, '(sin comercio)'), COUNT(*) c FROM transactions WHERE category = 'otros' AND direction = 'out' "
            "GROUP BY merchant_norm ORDER BY c DESC LIMIT ?", (n,)):
        print(f"  {count:4}  {merchant}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.path.expanduser("~/.selfbank/selfbank.db"))
    parser.add_argument("--raw-dir", default=os.path.expanduser("~/.selfbank/raw/prod"))
    parser.add_argument("--categories", default=os.path.expanduser("~/.selfbank/categories.local.json"))
    parser.add_argument("--llm", action="store_true", help="incluye el modelo local en la sección 3 (más lento)")
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--show-monthly", type=int, metavar="N")
    parser.add_argument("--uncategorized", type=int, metavar="N")
    args = parser.parse_args(argv)

    FAILURES.clear()
    categorizer = Categorizer.from_file(args.categories)
    raw_rows = load_raw(Path(args.raw_dir))
    conn = db.connect(args.db, read_only=True)
    # El motor registra cada pregunta en `history`: se prueba sobre una copia temporal para no ensuciar la base real.
    work = tempfile.mkdtemp(prefix=".check-", dir=str(Path(args.db).resolve().parent))
    copy = os.path.join(work, "copy.db")
    try:
        target = sqlite3.connect(copy)
        conn.backup(target)
        target.close()
        print(f"Base: {args.db}")
        section_integrity(conn, raw_rows, categorizer)
        section_extraction(conn)
        llm = OllamaClient(model=args.model) if args.llm else None
        if llm:
            print("\n(calentando el modelo: el arranque en frío tarda ~1 minuto)")
            llm.warm_up(date.today())
        section_dataset(lambda today: Engine(copy, llm=llm, clock=lambda: today), "con modelo" if llm else "solo reglas, sin modelo", llm)
        section_oracle(conn, raw_rows, categorizer)
        section_security(conn)
        if args.show_monthly:
            show_monthly(conn, args.show_monthly)
        if args.uncategorized:
            show_uncategorized(conn, args.uncategorized)
    finally:
        conn.close()
        shutil.rmtree(work, ignore_errors=True)
    print("\n" + ("TODO CORRECTO" if not FAILURES else f"HAY {len(FAILURES)} FALLO(S): " + "; ".join(FAILURES)))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
