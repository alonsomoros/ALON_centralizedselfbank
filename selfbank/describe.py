"""Cómo se muestra al usuario lo que el sistema ha entendido, y cómo se formatean fechas e importes.

Recibe la consulta completa (los 15 campos, ya con fechas) y devuelve un título y unas etiquetas
que la pantalla muestra junto a la respuesta: «Gasto total · restaurantes · 1 oct – 31 oct 2026».
Solo biblioteca estándar.
"""

from datetime import date

MONTHS_ABBR = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

CATEGORY_LABELS = {
    "supermercado": "supermercado", "restaurantes": "restaurantes", "transporte": "transporte",
    "ocio_suscripciones": "ocio y suscripciones", "salud": "salud", "compras": "compras",
    "hogar_facturas": "hogar y facturas", "viajes": "viajes", "efectivo": "efectivo",
    "comisiones": "comisiones", "bizum": "Bizum", "transferencias": "transferencias", "otros": "otros",
}
GROUP_LABELS = {"category": "categoría", "merchant": "comercio", "month": "mes"}


# ---- formato -------------------------------------------------------------------------------------

def fmt_date(value) -> str:
    d = value if isinstance(value, date) else date.fromisoformat(value)
    return f"{d.day} {MONTHS_ABBR[d.month - 1]} {d.year}"


def fmt_range(start: str, end: str) -> str:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if a == b:
        return fmt_date(a)
    if a.year == b.year:
        return f"{a.day} {MONTHS_ABBR[a.month - 1]} – {b.day} {MONTHS_ABBR[b.month - 1]} {b.year}"
    return f"{fmt_date(a)} – {fmt_date(b)}"


def money(value: float) -> str:
    """1234.5 -> '1.234,50 €'."""
    text = f"{abs(value):,.2f}".replace(",", "\0").replace(".", ",").replace("\0", ".")
    return f"{'-' if value < 0 else ''}{text} €"


def amount_label(value: float) -> str:
    """Importe de un filtro: 50 -> '50 €', 12.5 -> '12,50 €'."""
    if float(value).is_integer():
        return f"{int(value):,}".replace(",", ".") + " €"
    return money(value)


def count_label(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


# ---- etiquetas -------------------------------------------------------------------------------------

def filter_chips(q: dict) -> list:
    chips = []
    if q.get("category"):
        chips.append(CATEGORY_LABELS[q["category"]])
    if q.get("text"):
        chips.append(f"«{q['text']}»")
    lo, hi = q.get("min_amount"), q.get("max_amount")
    if lo is not None and hi is not None:
        chips.append(f"entre {amount_label(lo)} y {amount_label(hi)}")
    elif lo is not None:
        chips.append(f"más de {amount_label(lo)}")
    elif hi is not None:
        chips.append(f"menos de {amount_label(hi)}")
    return chips


def order_chip(q: dict):
    sort, limit = q.get("sort"), q.get("limit")
    if sort == "amount_desc" and limit:
        return "el mayor" if limit == 1 else f"los {limit} mayores"
    if sort == "amount_asc" and limit:
        return "el menor" if limit == 1 else f"los {limit} menores"
    if sort == "date_desc" and limit:
        return "el más reciente" if limit == 1 else f"los {limit} más recientes"
    if limit:
        return f"máx. {limit}" if limit != 1 else "solo el primero"
    return None


def period_text(q: dict) -> str:
    """'del 1 oct – 31 oct 2026', 'el 15 nov 2026' o 'en todo el histórico'."""
    if not q.get("from"):
        return "en todo el histórico"
    if q["from"] == q["to"]:
        return f"el {fmt_date(q['from'])}"
    return "del " + fmt_range(q["from"], q["to"]).replace(" – ", " al ")


def title(q: dict) -> str:
    intent, direction, group = q["intent"], q.get("direction", "any"), q.get("group_by")
    word = {"out": "Gasto", "in": "Ingresos", "any": "Importe"}[direction]
    if intent == "aggregate":
        if q["metric"] == "count":
            base = {"out": "Número de pagos", "in": "Número de ingresos", "any": "Número de movimientos"}[direction]
        else:
            base = {"out": "Gasto total", "in": "Ingresos totales", "any": "Importe total"}[direction]
        return f"{base} por {GROUP_LABELS[group]}" if group else base
    if intent == "list_transactions":
        return {"out": "Pagos", "in": "Ingresos", "any": "Movimientos"}[direction]
    if intent == "last_transaction":
        return {"out": "Último pago", "in": "Último ingreso", "any": "Último movimiento"}[direction]
    if intent == "balance":
        return "Saldo actual"
    if intent == "compare_periods":
        what = "nº de movimientos" if q.get("metric") == "count" else word.lower()
        return f"Comparación de {what}"
    if intent == "top_merchants":
        return "Ranking de comercios por " + ("ingresos" if direction == "in" else "gasto")
    if intent == "recurring":
        return "Ingresos periódicos" if direction == "in" else "Cargos periódicos"
    return "Sin interpretar"


def describe(q: dict) -> dict:
    """{'title', 'chips', 'text'} para mostrar junto a la respuesta."""
    if q["intent"] == "unknown":
        return {"title": title(q), "chips": [], "text": title(q)}
    chips = filter_chips(q)
    if q.get("from"):
        chips.append(fmt_range(q["from"], q["to"]))
    if q.get("compare_from"):
        chips.append(f"frente a {fmt_range(q['compare_from'], q['compare_to'])}")
    order = order_chip(q)
    if order and q["intent"] != "last_transaction":
        chips.append(order)
    return {"title": title(q), "chips": chips, "text": " · ".join([title(q)] + chips)}
