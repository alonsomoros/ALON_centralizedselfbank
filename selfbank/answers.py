"""Plantillas de respuesta: de (consulta, resultado del ejecutor) a texto y datos para la pantalla.

El modelo no redacta nada: el texto sale de estas plantillas, con las cifras que calcula SQL.

Formas de `result` (las produce el ejecutor; el simulado inventa las mismas):
  aggregate sin group_by   {"value": 1234.56, "count": 14}  (value = importe; count = nº de movimientos)
  aggregate con group_by   {"rows": [{"group": "restaurantes", "amount": 300.0, "count": 5}, ...]}
  list_transactions        {"rows": [{"date": "2026-11-03", "description": "...", "amount": -12.0}, ...]}
  last_transaction         {"rows": [ ...0 o 1 fila como la anterior... ]}
  balance                  {"value": 1234.56}
  compare_periods          {"current": 1.0, "reference": 2.0}
  top_merchants            {"rows": [{"merchant": "...", "amount": 120.0, "count": 4}, ...]}
  recurring                {"rows": [{"merchant": "...", "amount": 9.99, "every_days": 30}, ...]}
Devuelve {"text", "kind", "data"}; kind ∈ scalar | table | none. Solo biblioteca estándar.
"""

from . import describe as d

COLUMN_LABELS = {
    "date": "Fecha", "description": "Concepto", "amount": "Importe", "count": "Nº", "merchant": "Comercio",
    "every_days": "Cada (días)", "period": "Periodo", "category": "Categoría", "month": "Mes",
}


def _column(key, type_, label=None):
    return {"key": key, "label": label or COLUMN_LABELS.get(key, key), "type": type_}


def _tail(q: dict, with_period: bool = True) -> str:
    chips = d.filter_chips(q)
    text = f" ({', '.join(chips)})" if chips else ""
    if with_period:
        text += f" {d.period_text(q)}"
    return text


def _noun(q: dict, plural: bool = True) -> str:
    direction = q.get("direction", "any")
    singular, many = {"out": ("pago", "pagos"), "in": ("ingreso", "ingresos"), "any": ("movimiento", "movimientos")}[direction]
    return many if plural else singular


def _group_label(q: dict, value) -> str:
    if q.get("group_by") == "month" and isinstance(value, str) and len(value) == 7:
        year, month = value.split("-")
        return f"{d.MONTHS_ABBR[int(month) - 1]} {year}"
    if q.get("group_by") == "category":
        return d.CATEGORY_LABELS.get(value, value)
    return str(value)


def _none(text: str) -> dict:
    return {"text": text, "kind": "none", "data": None}


def _scalar(text: str, value, unit: str) -> dict:
    return {"text": text, "kind": "scalar", "data": {"value": value, "unit": unit}}


def _table(text: str, columns: list, rows: list) -> dict:
    return {"text": text, "kind": "table", "data": {"columns": columns, "rows": rows}}


def _aggregate(q, r):
    if q.get("group_by"):
        rows = r["rows"]
        is_count = q["metric"] == "count"
        if not rows:
            return _none(f"No hay movimientos{_tail(q)}.")
        first = max(rows, key=lambda row: row["amount"])
        shown = _group_label(q, first["group"])
        amount = f"{first['amount']:g}" if is_count else d.money(first["amount"])
        if q.get("limit") == 1:
            text = f"El mayor{_tail(q)}: {shown} con {amount}."
        else:
            text = f"{d.title(q)}{_tail(q)}: el mayor es {shown} con {amount}."
        columns = [_column(q["group_by"], "text", d.GROUP_LABELS[q["group_by"]].capitalize()), _column("amount", "count" if is_count else "money")]
        return _table(text, columns, [[_group_label(q, row["group"]), row["amount"]] for row in rows])
    if q["metric"] == "count":
        n = int(r["count"])
        noun = _noun(q, plural=n != 1)
        return _scalar(f"Hay {n} {noun}{_tail(q)}.", n, "count")
    verb = {"out": "Has gastado", "in": "Has ingresado", "any": "El importe total es"}[q.get("direction", "any")]
    return _scalar(f"{verb} {d.money(r['value'])}{_tail(q)}.", r["value"], "EUR")


def _transactions_table(rows):
    return [_column("date", "date"), _column("description", "text"), _column("amount", "money")], \
           [[row["date"], row["description"], row["amount"]] for row in rows]


def _list(q, r):
    rows = r["rows"]
    if not rows:
        return _none(f"No hay {_noun(q)}{_tail(q)}.")
    columns, data = _transactions_table(rows)
    return _table(f"Estos son tus {len(rows)} {_noun(q, len(rows) != 1)}{_tail(q)}.", columns, data)


def _last(q, r):
    rows = r["rows"]
    if not rows:
        return _none(f"No encuentro ningún {_noun(q, False)}{_tail(q)}.")
    row = rows[0]
    columns, data = _transactions_table(rows[:1])
    chips = d.filter_chips(q)
    filt = f" ({', '.join(chips)})" if chips else ""
    when = f" ({d.period_text(q)})" if q.get("from") else ""
    text = f"Tu último {_noun(q, False)}{filt}{when} fue de {d.money(abs(row['amount']))} el {d.fmt_date(row['date'])}."
    return _table(text, columns, data)


def _balance(q, r):
    return _scalar(f"Tu saldo actual es {d.money(r['value'])}.", r["value"], "EUR")


def _compare(q, r):
    is_count = q.get("metric") == "count"
    fmt = (lambda v: f"{v:g}") if is_count else d.money
    current, reference = r["current"], r["reference"]
    diff = current - reference
    tail = "igual" if diff == 0 else f"{fmt(abs(diff))} {'más' if diff > 0 else 'menos'}"
    a, b = d.fmt_range(q["from"], q["to"]), d.fmt_range(q["compare_from"], q["compare_to"])
    text = f"{a}: {fmt(current)}. {b}: {fmt(reference)}. Diferencia: {tail}."
    columns = [_column("period", "text"), _column("amount", "count" if is_count else "money")]
    return _table(text, columns, [[a, current], [b, reference]])


def _top(q, r):
    rows = r["rows"]
    if not rows:
        return _none(f"No hay movimientos{_tail(q)}.")
    columns = [_column("merchant", "text"), _column("amount", "money"), _column("count", "count")]
    text = f"Principal{_tail(q)}: {rows[0]['merchant']} ({d.money(rows[0]['amount'])})."
    return _table(text, columns, [[row["merchant"], row["amount"], row["count"]] for row in rows])


def _recurring(q, r):
    rows = r["rows"]
    if not rows:
        return _none("No detecto cargos periódicos.")
    total = sum(row["amount"] for row in rows)
    columns = [_column("merchant", "text"), _column("amount", "money"), _column("every_days", "count")]
    text = f"Detecto {len(rows)} {'cargo periódico' if len(rows) == 1 else 'cargos periódicos'}, que suman unos {d.money(total)} por ciclo."
    return _table(text, columns, [[row["merchant"], row["amount"], row["every_days"]] for row in rows])


HANDLERS = {
    "aggregate": _aggregate, "list_transactions": _list, "last_transaction": _last, "balance": _balance,
    "compare_periods": _compare, "top_merchants": _top, "recurring": _recurring,
}


def render(q: dict, result: dict = None) -> dict:
    if q["intent"] == "unknown":
        return _none(q["clarification"])
    return HANDLERS[q["intent"]](q, result)
