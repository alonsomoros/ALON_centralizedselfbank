"""Reglas deterministas sobre el texto de la pregunta (periodo, sentido, importes, límite, categoría).

Principio: lo que tiene un vocabulario cerrado en español lo resuelve el código, no el modelo.
`apply` sobrescribe esos campos de la consulta del modelo con lo que dice la pregunta; si la pregunta
no dice nada, los descarta (un modelo pequeño tiende a inventarlos). Solo biblioteca estándar.
"""

import re
import unicodedata

import contract

NUMWORDS = {"un": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10}
NUM = r"(\d+(?:[.,]\d+)*|" + "|".join(NUMWORDS) + r")"
TIME_UNITS = r"(?:dias?|semanas?|meses|mes|anos?)"


def normalize(text: str) -> str:
    """Minúsculas y sin tildes (la ñ se conserva como n: 'ano')."""
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def parse_number(raw: str):
    raw = raw.strip()
    if raw in NUMWORDS:
        return NUMWORDS[raw]
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", raw):  # 1.200 / 1.200,50
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", ".")
    value = float(raw)
    return int(value) if value.is_integer() else value


# ---- periodos ---------------------------------------------------------------------------------

PERIOD_PATTERNS = [
    (r"\bhoy\b", "hoy"),
    (r"\bayer\b", "ayer"),
    (r"\beste mes\b", "este_mes"),
    (r"\bmes (?:pasado|anterior)\b", "mes_pasado"),
    (r"\besta semana\b", "esta_semana"),
    (r"\bsemana (?:pasada|anterior)\b", "semana_pasada"),
    (r"\beste trimestre\b", "este_trimestre"),
    (r"\beste ano\b|\bllevo de ano\b|\blo que va de ano\b", "este_ano"),
    (r"\bano (?:pasado|anterior)\b", "ano_pasado"),
    (r"\bultimo mes\b|\bultimos 30 dias\b", "ultimos_30_dias"),
    (r"\bultima semana\b|\bultimos 7 dias\b", "ultimos_7_dias"),
    (r"\bultimos 15 dias\b", "ultimos_15_dias"),
    (r"\bultimos 90 dias\b|\bultimos 3 meses\b", "ultimos_90_dias"),
    (r"\bultimo ano\b|\bultimos 12 meses\b|\bultimos 365 dias\b", "ultimos_365_dias"),
] + [(rf"\b{month}\b", month) for month in contract.MONTHS]


def periods(question: str) -> list:
    """Etiquetas de periodo que aparecen en la pregunta, en orden de aparición."""
    text = normalize(question)
    found = []
    for pattern, tag in PERIOD_PATTERNS:
        for match in re.finditer(pattern, text):
            found.append((match.start(), match.end(), tag))
    found.sort()
    result, last_end = [], -1
    for start, end, tag in found:
        if start >= last_end:  # descarta solapes
            result.append(tag)
            last_end = end
    return result


# ---- sentido del movimiento ---------------------------------------------------------------------

OUT_CUES = re.compile(
    r"\bgast\w*|\bcompr\w*|\bpag(?:o|os|ue|ues|ado|ar|aron|ando)\b|\bcargo\w*|\bsac(?:ar|ado|ue|o)\b"
    r"|\bme (?:han |ha )?cobr\w*|\bme cobr\w*"
)
IN_CUES = re.compile(r"\bingres\w*|\brecib\w*|\bnomina\w*|\babon(?:o|os|ado|ados)\b|\bme han (?:pagado|abonado)\b")


def direction(question: str):
    text = normalize(question)
    is_out, is_in = bool(OUT_CUES.search(text)), bool(IN_CUES.search(text))
    if is_out and not is_in:
        return "out"
    if is_in and not is_out:
        return "in"
    return None  # sin pista o contradictoria


# ---- importes -----------------------------------------------------------------------------------

MONEY = r"(\d+(?:[.,]\d+)*)\s*(?:€|(?:euros?|eur)\b)"
BETWEEN = re.compile(rf"\bentre (\d+(?:[.,]\d+)*)\s*(?:€|(?:euros?|eur)\b)?\s*y\s*{MONEY}")
MIN_RE = re.compile(rf"(?:\bmas de|\bmayor(?:es)? (?:de|que)|\bsuperior(?:es)? a|\bpor encima de|\bal menos)\s*{MONEY}")
MAX_RE = re.compile(rf"(?:\bmenos de|\bmenor(?:es)? (?:de|que)|\binferior(?:es)? a|\bpor debajo de|\bhasta|\bcomo maximo)\s*{MONEY}")


def amounts(question: str) -> dict:
    text = normalize(question)
    between = BETWEEN.search(text)
    if between:
        return {"min_amount": parse_number(between.group(1)), "max_amount": parse_number(between.group(2))}
    result = {}
    low, high = MIN_RE.search(text), MAX_RE.search(text)
    if low:
        result["min_amount"] = parse_number(low.group(1))
    if high:
        result["max_amount"] = parse_number(high.group(1))
    return result


# ---- orden y límite -------------------------------------------------------------------------------

LAST_N = re.compile(rf"\bultim[oa]s {NUM} (?!{TIME_UNITS}\b)\w+")
TOP_N = re.compile(rf"\b{NUM} (?:mayores|mas caros|mas altos|gastos mas (?:altos|grandes))")
TOP_ONE = re.compile(r"\b(?:el|la|mi|mis) (?:mayor|mas caro|mas alto|gasto mas alto)\b")
BOTTOM_ONE = re.compile(r"\b(?:el|la|mi) (?:menor|mas barato|mas bajo)\b")
WHICH_IS_MOST = re.compile(r"\bcual es (?:el|la)\b[^?]*\bmas\b")


def ordering(question: str, intent: str, group_by) -> dict:
    text = normalize(question)
    last = LAST_N.search(text)
    if last:
        return {"sort": "date_desc", "limit": parse_number(last.group(1))}
    top = TOP_N.search(text)
    if top:
        return {"sort": "amount_desc", "limit": parse_number(top.group(1))}
    if TOP_ONE.search(text):
        return {"sort": "amount_desc", "limit": 1}
    if BOTTOM_ONE.search(text):
        return {"sort": "amount_asc", "limit": 1}
    if WHICH_IS_MOST.search(text):
        if intent == "aggregate" and group_by:
            return {"sort": "amount_desc", "limit": 1}
        return {"limit": 1}
    return {}


# ---- texto que en realidad es una categoría --------------------------------------------------------

TEXT_AS_CATEGORY = {
    "restaurantes": "restaurantes", "restaurante": "restaurantes", "cenas": "restaurantes", "bares": "restaurantes",
    "supermercado": "supermercado", "supermercados": "supermercado",
    "ocio": "ocio_suscripciones", "suscripciones": "ocio_suscripciones",
    "transporte": "transporte", "gasolina": "transporte",
    "bizum": "bizum", "bizums": "bizum", "cajero": "efectivo", "efectivo": "efectivo", "comisiones": "comisiones",
    "compras": "compras", "salud": "salud", "viajes": "viajes",
}


def clean_text(q: dict) -> dict:
    text = q.get("text")
    if text is None:
        return q
    result = dict(q)
    if not re.search(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]", text):  # ">50", "10"…
        result.pop("text")
    elif normalize(text).strip() in TEXT_AS_CATEGORY:
        result.pop("text")
        result.setdefault("category", TEXT_AS_CATEGORY[normalize(text).strip()])
    return result


# ---- aplicar --------------------------------------------------------------------------------------

STRUCTURAL = ["direction", "period", "compare_period", "min_amount", "max_amount", "sort", "limit"]


def apply(question: str, q: dict) -> dict:
    """Devuelve la consulta con los campos de vocabulario cerrado fijados por la pregunta."""
    intent = q.get("intent")
    if intent in ("unknown", "balance"):
        return dict(q)
    result = {k: x for k, x in clean_text(q).items() if k not in STRUCTURAL}

    tags = periods(question)
    if intent == "compare_periods":
        if len(tags) >= 2:
            result["period"], result["compare_period"] = tags[0], tags[1]
    elif tags:
        result["period"] = tags[0]

    sense = direction(question)
    if sense:
        result["direction"] = sense
    result.update(amounts(question))
    result.update(ordering(question, intent, result.get("group_by")))
    if intent == "aggregate" and not result.get("group_by"):
        result.pop("sort", None)
        result.pop("limit", None)
    return result
