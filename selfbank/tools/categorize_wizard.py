"""Asistente interactivo para clasificar los comercios que han quedado en «otros».

  python -m selfbank.tools.categorize_wizard --db ~/.selfbank/selfbank.db [--limit 40]

Muestra (EN TU TERMINAL: no la copies a chats) los comercios sin clasificar con más movimientos; eliges una categoría
con un número y la palabra clave se guarda en ~/.selfbank/categories.local.json (permisos 600, fuera de git). Al terminar
reclasifica la base y muestra cuánto ha mejorado la cobertura. Se puede repetir cuando quieras.
"""

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

from selfbank import contract, db, ingest
from selfbank.categorize import Categorizer
from selfbank.text import squash

MENU = ["supermercado", "restaurantes", "transporte", "ocio_suscripciones", "salud", "compras", "hogar_facturas", "viajes", "efectivo", "comisiones"]


def keyword_for(merchant_norm: str) -> str:
    """Palabra clave general de un comercio: sin números de sucursal y con un máximo de 3 palabras."""
    words = [w for w in merchant_norm.split() if not any(ch.isdigit() for ch in w)]
    return " ".join(words[:3]) or merchant_norm


def candidates(conn, limit: int) -> list:
    """[(palabra clave, nº de movimientos)] de los gastos en «otros», agrupando sucursales del mismo comercio."""
    counts = Counter()
    for merchant_norm, n in conn.execute(
            "SELECT merchant_norm, COUNT(*) FROM transactions WHERE category = 'otros' AND direction = 'out' "
            "AND merchant_norm IS NOT NULL AND kind IN ('purchase', 'other') GROUP BY merchant_norm"):
        counts[keyword_for(merchant_norm)] += n
    return counts.most_common(limit)


def coverage(conn) -> tuple:
    row = conn.execute("SELECT SUM(category != 'otros'), COUNT(*) FROM transactions WHERE direction = 'out' AND kind IN ('purchase', 'other')").fetchone()
    return (row[0] or 0), row[1]


def save_rules(path: Path, rules: dict) -> None:
    """Escritura atómica con permisos 600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".categories-")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(rules, handle, ensure_ascii=False, indent=2, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def run(conn, path: Path, limit: int, ask=input, say=print) -> dict:
    rules = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    before = coverage(conn)
    say(f"Cobertura actual de categorías en gastos de comercio: {before[0]}/{before[1]} = {100 * before[0] / max(before[1], 1):.0f} %")
    options = [m for m in MENU if m in contract.load_schema(contract.MODEL_SCHEMA_PATH)["properties"]["category"]["enum"]]
    say("\nCategorías: " + "  ".join(f"[{i}] {name}" for i, name in enumerate(options, start=1)) + "\n  [Intro] saltar   [q] terminar\n")
    added = 0
    for keyword, count in candidates(conn, limit):
        answer = ask(f"{count:3} movimientos · «{keyword}» → ").strip().lower()
        if answer == "q":
            break
        if not answer.isdigit() or not (1 <= int(answer) <= len(options)):
            continue
        category = options[int(answer) - 1]
        known = rules.setdefault(category, [])
        if squash(keyword) not in [squash(k) for k in known]:
            known.append(keyword)
            added += 1
    if added:
        save_rules(path, rules)
    stats = ingest.recategorize(conn, Categorizer.from_file(path))
    after = coverage(conn)
    say(f"\nPalabras clave añadidas: {added} · reclasificados: {stats['changed']}")
    say(f"Cobertura ahora: {after[0]}/{after[1]} = {100 * after[0] / max(after[1], 1):.0f} %")
    return {"added": added, "changed": stats["changed"], "before": before, "after": after}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.path.expanduser("~/.selfbank/selfbank.db"))
    parser.add_argument("--categories", default=os.path.expanduser("~/.selfbank/categories.local.json"))
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args(argv)
    conn = db.connect(args.db)
    try:
        run(conn, Path(args.categories), args.limit)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
