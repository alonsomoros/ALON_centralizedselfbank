"""Tubería de interpretación, compartida por el motor real, el simulado y el banco de pruebas.

consulta del modelo (o de la ruta rápida)
  -> sanitize (quita campos que no corresponden al intent)
  -> reglas (periodo, sentido, importes, límites y texto fijados por la pregunta)
  -> sanitize -> validación -> palabras clave de categoría -> expansión a la consulta completa (con fechas).
Una salida incoherente equivale a «no te he entendido» (unknown ambigua).
"""

from . import contract, rules

UNKNOWN_AMBIGUOUS = {"intent": "unknown", "reason": "ambigua"}


def interpret(question: str, raw: dict, today) -> tuple:
    """(consulta completa | None, errores). None con errores si la consulta del modelo no es coherente."""
    cleaned = contract.sanitize(rules.apply(question, contract.sanitize(raw)))
    errors = contract.model_errors(cleaned)
    if errors:
        return None, errors
    return contract.expand(contract.refine(question, cleaned), today), []


def to_query(question: str, raw, today) -> tuple:
    """(consulta completa, reason) sin errores posibles: lo incoherente o ausente pasa a unknown ambigua."""
    query, errors = interpret(question, raw, today) if raw else (None, ["sin consulta"])
    if query is None:
        query = contract.expand(UNKNOWN_AMBIGUOUS, today)
        return query, "ambigua"
    return query, _reason_of(query)


def _reason_of(query: dict):
    """Motivo de un unknown a partir del mensaje fijo; None si no es unknown."""
    if query["intent"] != "unknown":
        return None
    for reason, text in contract.MESSAGES.items():
        if query["clarification"] == text:
            return reason
    return "ambigua"
