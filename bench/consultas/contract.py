"""Contrato v2: lo que emite el modelo (compacto) -> consulta completa para el ejecutor.

El modelo solo escribe los campos con valor y etiquetas de periodo; este módulo:
  - resuelve las etiquetas a fechas (el modelo nunca calcula fechas),
  - rellena los valores por defecto y los mensajes fijos de `unknown`,
  - corrige categorías inequívocas por palabras clave (refine),
  - valida la coherencia semántica de la consulta del modelo.
Solo biblioteca estándar.
"""

import calendar
import copy
import re
from datetime import date, timedelta

MONTHS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
LAST_DAYS = [7, 15, 30, 90, 365]
PERIODS = (
    ["hoy", "ayer", "este_mes", "mes_pasado", "esta_semana", "semana_pasada", "este_trimestre", "este_ano", "ano_pasado"]
    + [f"ultimos_{n}_dias" for n in LAST_DAYS]
    + MONTHS
)

MESSAGES = {
    "ambigua": "No tengo claro qué quieres saber. ¿Puedes concretar el periodo o el tipo de movimiento?",
    "fuera_de_alcance": "Solo puedo responder preguntas concretas sobre tus movimientos, por ejemplo cuánto gastaste este mes o cuál es tu saldo.",
    "operacion": "Solo puedo consultar tus cuentas, no hacer operaciones.",
}

RESOLVED_KEYS = [
    "intent", "metric", "group_by", "direction", "category", "text", "from", "to",
    "compare_from", "compare_to", "min_amount", "max_amount", "sort", "limit", "clarification",
]
MODEL_FIELDS = [
    "metric", "group_by", "direction", "category", "text", "period", "compare_period",
    "min_amount", "max_amount", "sort", "limit", "reason",
]

# Categorías inequívocas por palabra clave (se aplican solo si el modelo no puso categoría).
KEYWORDS = [
    (re.compile(r"\bbizums?\b", re.I), "bizum"),
    (re.compile(r"\bcajeros?\b|\bsacar (?:dinero|efectivo)\b", re.I), "efectivo"),
    (re.compile(r"\bcomisi(?:ón|on|ones)\b", re.I), "comisiones"),
]
REFINABLE = {"aggregate", "list_transactions", "last_transaction", "top_merchants", "recurring"}


def month_bounds(year: int, month: int):
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def resolve_period(tag: str, today: date):
    """(from, to) de una etiqueta de periodo, según convenciones.md."""
    if tag == "hoy":
        return today, today
    if tag == "ayer":
        return today - timedelta(days=1), today - timedelta(days=1)
    if tag == "este_mes":
        return today.replace(day=1), today
    if tag == "mes_pasado":
        last = today.replace(day=1) - timedelta(days=1)
        return month_bounds(last.year, last.month)
    if tag == "esta_semana":
        return today - timedelta(days=today.weekday()), today
    if tag == "semana_pasada":
        monday = today - timedelta(days=today.weekday() + 7)
        return monday, monday + timedelta(days=6)
    if tag == "este_trimestre":
        return date(today.year, 3 * ((today.month - 1) // 3) + 1, 1), today
    if tag == "este_ano":
        return date(today.year, 1, 1), today
    if tag == "ano_pasado":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    found = re.fullmatch(r"ultimos_(\d+)_dias", tag)
    if found and int(found.group(1)) in LAST_DAYS:
        return today - timedelta(days=int(found.group(1)) - 1), today
    if tag in MONTHS:
        month = MONTHS.index(tag) + 1
        return month_bounds(today.year if month <= today.month else today.year - 1, month)
    raise ValueError(f"periodo desconocido: {tag}")


def model_errors(q: dict) -> list:
    """Coherencia de la consulta del modelo (la forma ya se valida con schema.json)."""
    errors = []
    intent = q.get("intent")
    present = {k for k in MODEL_FIELDS if q.get(k) is not None}

    if intent == "unknown":
        if "reason" not in present:
            errors.append("unknown exige reason")
        extra = sorted(present - {"reason"})
        if extra:
            errors.append(f"unknown solo admite reason (sobran: {extra})")
        return errors
    if "reason" in present:
        errors.append("reason solo se rellena en unknown")
    if intent == "balance":
        extra = sorted(present)
        if extra:
            errors.append(f"balance no admite campos (sobran: {extra})")
        return errors

    if intent == "aggregate":
        if "metric" not in present:
            errors.append("aggregate exige metric")
    elif intent != "compare_periods" and "metric" in present:
        errors.append(f"{intent} no admite metric")
    if intent == "compare_periods":
        if "period" not in present or "compare_period" not in present:
            errors.append("compare_periods exige period y compare_period")
    elif "compare_period" in present:
        errors.append(f"{intent} no admite compare_period")
    if "group_by" in present:
        if intent == "top_merchants":
            if q["group_by"] != "merchant":
                errors.append("top_merchants solo admite group_by merchant")
        elif intent != "aggregate":
            errors.append(f"{intent} no admite group_by")
    lo, hi = q.get("min_amount"), q.get("max_amount")
    if lo is not None and hi is not None and lo > hi:
        errors.append("min_amount mayor que max_amount")
    return errors


def sanitize(q: dict) -> dict:
    """Quita los campos que no corresponden al intent (lector tolerante).

    Un modelo pequeño mezcla campos de otros intents; se ignoran en vez de rechazar toda la
    consulta. No inventa ni corrige valores: los campos obligatorios que falten siguen siendo error.
    """
    result = copy.deepcopy(q)
    intent = result.get("intent")
    if intent == "unknown":
        keep = {"intent", "reason"}
    elif intent == "balance":
        keep = {"intent"}
    else:
        keep = {"intent", "direction", "category", "text", "period", "min_amount", "max_amount", "sort", "limit"}
        if intent in ("aggregate", "compare_periods"):
            keep.add("metric")
        if intent in ("aggregate", "top_merchants"):
            keep.add("group_by")
        if intent == "compare_periods":
            keep.add("compare_period")
    return {k: x for k, x in result.items() if k in keep}


def refine(question: str, q: dict) -> dict:
    """Devuelve una copia con la categoría deducida por palabras clave, si el modelo no la puso."""
    result = copy.deepcopy(q)
    if result.get("intent") in REFINABLE and result.get("category") is None:
        for pattern, category in KEYWORDS:
            if pattern.search(question):
                result["category"] = category
                break
    return result


def expand(q: dict, today: date) -> dict:
    """Consulta del modelo -> consulta completa (los 15 campos) que consume el ejecutor."""
    full = {key: None for key in RESOLVED_KEYS}
    full["direction"] = "any"
    for key in ("intent", "metric", "group_by", "direction", "category", "text", "min_amount", "max_amount", "sort", "limit"):
        if q.get(key) is not None:
            full[key] = q[key]
    if q.get("period") is not None:
        full["from"], full["to"] = (d.isoformat() for d in resolve_period(q["period"], today))
    if q.get("compare_period") is not None:
        full["compare_from"], full["compare_to"] = (d.isoformat() for d in resolve_period(q["compare_period"], today))
    if full["intent"] == "top_merchants":
        full["group_by"] = "merchant"
    if full["intent"] in ("top_merchants", "recurring") and q.get("direction") is None:
        full["direction"] = "out"  # un ranking de comercios y los cargos periódicos son gastos
    if full["intent"] == "compare_periods" and full["metric"] is None:
        full["metric"] = "sum"  # comparar importes es el caso habitual
    if full["intent"] == "unknown":
        full["clarification"] = MESSAGES[q["reason"]]
    return full
