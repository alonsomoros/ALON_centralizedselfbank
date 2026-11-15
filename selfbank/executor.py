"""Ejecutor: de la consulta completa (los 15 campos) a las cifras, con SQL parametrizado de solo lectura.

Reglas de seguridad:
  - Los valores del usuario viajan SIEMPRE como parámetros (`?`), nunca dentro del texto SQL.
  - Los fragmentos de SQL (columnas, ordenación) salen de diccionarios cerrados, nunca de la pregunta.
  - Pensado para una conexión de solo lectura (db.connect(path, read_only=True)).

Convenciones de cifras:
  - Gasto (`out`) e ingreso (`in`) se suman por separado y en positivo; con `any` se devuelve el neto (ingresos − gastos).
  - Las devoluciones (`kind = refund`) son ingresos del banco: se cuentan como ingresos y no se restan del gasto.
  - Listados: importes con signo (negativo = gasto), por defecto los más recientes y como máximo 50 filas (tope 200).
Devuelve las formas que documenta `answers.py`.
"""

import difflib
import statistics
from datetime import date

from .text import squash


class ExecutorError(RuntimeError):
    pass


def eur(cents) -> float:
    return round((cents or 0) / 100, 2)


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


GROUPS = {
    "category": ("category", "category"),
    "merchant": ("COALESCE(merchant_norm, '')", "COALESCE(MAX(merchant), '(sin comercio identificado)')"),
    "month": ("strftime('%Y-%m', booking_date)", "strftime('%Y-%m', booking_date)"),
}
LIST_ORDER = {
    None: "booking_date DESC, COALESCE(entry_reference, '') DESC, id DESC",
    "date_desc": "booking_date DESC, COALESCE(entry_reference, '') DESC, id DESC",
    "amount_desc": "amount_cents DESC, booking_date DESC, id DESC",
    "amount_asc": "amount_cents ASC, booking_date DESC, id DESC",
}
CYCLES = [(7, 6, 8), (30, 27, 34), (90, 85, 95), (365, 350, 380)]  # (días del ciclo, mínimo, máximo)
SIGNED = "CASE direction WHEN 'out' THEN -amount_cents ELSE amount_cents END"
NET = "SUM(CASE direction WHEN 'in' THEN amount_cents ELSE -amount_cents END)"


class Executor:
    DEFAULT_LIST = 50
    MAX_LIST = 200
    DEFAULT_TOP = 5

    def __init__(self, conn):
        self.conn = conn

    # ---- punto de entrada ----
    def run(self, q: dict) -> dict:
        handler = getattr(self, "_" + q["intent"], None)
        if handler is None:
            raise ExecutorError(f"intent no ejecutable: {q['intent']}")
        return handler(q)

    # ---- filtros ----
    def _text_patterns(self, text: str) -> list:
        patterns = []
        for term in (squash(t) for t in text.split("|")):
            if term:
                patterns.append(self._resolve_term(term))
        return patterns

    def _exists(self, pattern: str) -> bool:
        return self.conn.execute("SELECT 1 FROM transactions WHERE description_norm LIKE ? ESCAPE '\\' LIMIT 1", (pattern,)).fetchone() is not None

    def _resolve_term(self, term: str) -> str:
        """Plurales sencillos y, si no hay coincidencia, el comercio más parecido («burguer king» ~ «burger king»)."""
        variants = [term]
        if term.endswith("es") and len(term) > 4:
            variants.append(term[:-2])
        if term.endswith("s") and len(term) > 3:
            variants.append(term[:-1])
        for candidate in variants:
            if self._exists(_like(candidate)):
                return _like(candidate)
        merchants = [row[0] for row in self.conn.execute("SELECT DISTINCT merchant_norm FROM transactions WHERE merchant_norm IS NOT NULL")]
        close = difflib.get_close_matches(term, merchants, n=1, cutoff=0.78)
        return _like(close[0]) if close else _like(term)

    def _where(self, q: dict, compare: bool = False, extra: tuple = ()):
        clauses, params = list(extra), []
        if q.get("direction") in ("out", "in"):
            clauses.append("direction = ?")
            params.append(q["direction"])
        lo, hi = (q.get("compare_from"), q.get("compare_to")) if compare else (q.get("from"), q.get("to"))
        if lo:
            clauses.append("booking_date BETWEEN ? AND ?")
            params += [lo, hi]
        if q.get("category"):
            clauses.append("category = ?")
            params.append(q["category"])
        if q.get("min_amount") is not None:
            clauses.append("amount_cents >= ?")
            params.append(int(round(q["min_amount"] * 100)))
        if q.get("max_amount") is not None:
            clauses.append("amount_cents <= ?")
            params.append(int(round(q["max_amount"] * 100)))
        if q.get("text"):
            patterns = self._text_patterns(q["text"])
            if patterns:
                clauses.append("(" + " OR ".join("description_norm LIKE ? ESCAPE '\\'" for _ in patterns) + ")")
                params += patterns
        return (" AND ".join(clauses) or "1 = 1"), params

    @staticmethod
    def _sum_expr(q: dict) -> str:
        return "SUM(amount_cents)" if q.get("direction") in ("out", "in") else NET

    # ---- intents ----
    def _total(self, q: dict, compare: bool = False) -> tuple:
        where, params = self._where(q, compare)
        row = self.conn.execute(f"SELECT COALESCE({self._sum_expr(q)}, 0), COUNT(*) FROM transactions WHERE {where}", params).fetchone()
        return row[0], row[1]

    def _aggregate(self, q: dict) -> dict:
        if not q.get("group_by"):
            total, count = self._total(q)
            return {"value": eur(total), "count": count}
        if q["group_by"] not in GROUPS:
            raise ExecutorError("group_by no soportado")
        key, label = GROUPS[q["group_by"]]
        where, params = self._where(q)
        is_count = q["metric"] == "count"
        measure = "n" if is_count else "total"
        sort = q.get("sort")
        if sort in ("amount_desc", "amount_asc"):
            order = f"{measure} {'DESC' if sort == 'amount_desc' else 'ASC'}, g"
        elif q["group_by"] == "month":
            order = "g ASC"
        else:
            order = f"{measure} DESC, g"
        sql = (f"SELECT {key} AS g, {label} AS label, {self._sum_expr(q)} AS total, COUNT(*) AS n "
               f"FROM transactions WHERE {where} GROUP BY g ORDER BY {order}")
        if q.get("limit"):
            sql += " LIMIT ?"
            params = params + [min(int(q["limit"]), self.MAX_LIST)]
        rows = []
        for g, shown, total, n in self.conn.execute(sql, params):
            rows.append({"group": g if q["group_by"] == "category" else shown, "amount": n if is_count else eur(total), "count": n})
        return {"rows": rows}

    def _transactions(self, q: dict, limit: int) -> list:
        where, params = self._where(q)
        sql = (f"SELECT booking_date, description, {SIGNED} FROM transactions WHERE {where} "
               f"ORDER BY {LIST_ORDER[q.get('sort')]} LIMIT ?")
        return [{"date": d, "description": text, "amount": eur(cents)} for d, text, cents in self.conn.execute(sql, params + [limit])]

    def _list_transactions(self, q: dict) -> dict:
        limit = min(int(q["limit"]), self.MAX_LIST) if q.get("limit") else self.DEFAULT_LIST
        return {"rows": self._transactions(q, limit)}

    def _last_transaction(self, q: dict) -> dict:
        return {"rows": self._transactions(dict(q, sort="date_desc"), 1)}

    def _balance(self, q: dict) -> dict:
        for kind in ("CLBD", "OPBD"):
            row = self.conn.execute("SELECT SUM(amount_cents), COUNT(*) FROM balances WHERE balance_type = ?", (kind,)).fetchone()
            if row[1]:
                return {"value": eur(row[0])}
        raise ExecutorError("No hay saldo guardado todavía")

    def _compare_periods(self, q: dict) -> dict:
        pick = (lambda totals: float(totals[1])) if q.get("metric") == "count" else (lambda totals: eur(totals[0]))
        return {"current": pick(self._total(q)), "reference": pick(self._total(q, compare=True))}

    def _top_merchants(self, q: dict) -> dict:
        extra = ["merchant_norm IS NOT NULL"]
        if q.get("direction") != "in" and q.get("category") not in ("bizum", "transferencias"):
            extra.append("kind NOT IN ('bizum', 'transfer')")  # las personas no son comercios (se piden con category=bizum)
        where, params = self._where(q, extra=tuple(extra))
        limit = min(int(q["limit"]), self.MAX_LIST) if q.get("limit") else self.DEFAULT_TOP
        sql = (f"SELECT merchant_norm, MAX(merchant), SUM(amount_cents) AS total, COUNT(*) AS n FROM transactions WHERE {where} "
               f"GROUP BY merchant_norm ORDER BY total DESC, n DESC, merchant_norm LIMIT ?")
        return {"rows": [{"merchant": shown, "amount": eur(total), "count": n} for _, shown, total, n in self.conn.execute(sql, params + [limit])]}

    def _recurring(self, q: dict) -> dict:
        through = self.conn.execute("SELECT MAX(booking_date) FROM transactions").fetchone()[0]
        if not through:
            return {"rows": []}
        where, params = self._where(q, extra=("merchant_norm IS NOT NULL", "kind NOT IN ('bizum', 'transfer')"))
        series = {}
        for norm, shown, day, cents in self.conn.execute(
                f"SELECT merchant_norm, merchant, booking_date, amount_cents FROM transactions WHERE {where} ORDER BY merchant_norm, booking_date", params):
            entry = series.setdefault(norm, {"name": shown, "dates": [], "amounts": []})
            entry["dates"].append(date.fromisoformat(day))
            entry["amounts"].append(cents)
        end = date.fromisoformat(through)
        rows = []
        for entry in series.values():
            found = _recurring_cycle(entry["dates"], entry["amounts"], end)
            if found:
                rows.append({"merchant": entry["name"], "amount": eur(found[1]), "every_days": found[0]})
        rows.sort(key=lambda r: (-r["amount"], r["merchant"]))
        return {"rows": rows}


def _recurring_cycle(dates: list, amounts: list, end: date):
    """(días del ciclo, importe mediano en céntimos) si los cargos son periódicos y siguen activos; si no, None.

    Condiciones: al menos 3 cargos, intervalos que caen en un ciclo conocido (semanal, mensual, trimestral o anual),
    importes estables (±15 %, o ±1 € en importes pequeños) y último cargo reciente (≤ 1,6 ciclos hasta el último dato).
    """
    if len(dates) < 3:
        return None
    gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
    if len(gaps) < 2:
        return None
    middle = statistics.median(gaps)
    for cycle, low, high in CYCLES:
        if low <= middle <= high:
            fits = sum(1 for g in gaps if low <= g <= high) / len(gaps)
            median_amount = statistics.median(amounts)
            tolerance = max(0.15 * median_amount, 100)
            stable = sum(1 for a in amounts if abs(a - median_amount) <= tolerance) / len(amounts)
            active = (end - dates[-1]).days <= 1.6 * cycle
            if fits >= 0.6 and stable >= 0.6 and active:
                return cycle, int(median_amount)
    return None
