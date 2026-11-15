"""Utilidades de texto compartidas: normalización para búsqueda y enmascarado de datos sensibles."""

import re
import unicodedata


def normalize(text: str) -> str:
    """Minúsculas y sin tildes (la ñ se conserva como n: 'ano'). Mismo criterio al guardar y al buscar."""
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def squash(text: str) -> str:
    """Normaliza y colapsa espacios."""
    return " ".join(normalize(text).split())


LONG_DIGITS = re.compile(r"\d{9,}")
STAR_DIGITS = re.compile(r"(?<=\*)(\d{4,8})(?!\d)")


def mask_sensitive(text: str) -> str:
    """Enmascara números de tarjeta y secuencias largas de dígitos, conservando solo los 4 últimos.

    - 9 o más dígitos seguidos (tarjeta de 16 dígitos, teléfonos, referencias): XXXXXXXXXXXX1234.
    - Tras un asterisco, de 4 a 8 dígitos (`TARJ. :*123456`): *XX3456.
    Es determinista: el mismo texto da siempre el mismo resultado (se usa para la clave de deduplicación).
    """
    text = LONG_DIGITS.sub(lambda m: "X" * (len(m.group(0)) - 4) + m.group(0)[-4:], text)
    return STAR_DIGITS.sub(lambda m: "X" * (len(m.group(1)) - 4) + m.group(1)[-4:] if len(m.group(1)) > 4 else m.group(1), text)
