"""Del concepto de un movimiento (texto del banco) a: tipo, comercio o contraparte y categoría.

El banco no da contraparte ni código de categoría: solo un texto libre (`remittance_information`).
Las formas conocidas de Santander (docs/bancos.md) se reconocen con expresiones regulares; la categoría sale de
palabras clave sobre el comercio. Las reglas por defecto son genéricas; cada usuario puede ampliarlas con un
fichero local (`categories.local.json`, fuera de git) que tiene prioridad.
Solo biblioteca estándar.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .text import squash

# Orden = prioridad: la primera categoría que encaje gana («uber eats» es restaurantes antes que transporte).
DEFAULT_RULES = {
    "efectivo": ["cajero", "cajeros", "reintegro", "atm", "disposicion efectivo", "retirada efectivo"],
    "comisiones": ["comision", "comisiones", "mantenimiento cuenta", "intereses", "cuota tarjeta"],
    "restaurantes": [
        "restaurante", "restaurantes", "cafeteria", "cafe", "bar", "taberna", "pizzeria", "burger", "burguer", "mcdonald", "mcdonalds",
        "kfc", "telepizza", "domino", "dominos", "glovo", "uber eats", "ubereats", "just eat", "justeat", "starbucks", "foster",
        "vips", "kebab", "sushi", "panaderia", "pasteleria", "heladeria", "cerveceria", "tapas", "pizza", "wok", "ginos",
    ],
    "ocio_suscripciones": [
        "netflix", "spotify", "hbo", "disney", "prime video", "amazon prime", "youtube", "apple com bill", "apple.com/bill",
        "itunes", "google play", "steam", "playstation", "xbox", "nintendo", "cine", "cines", "gimnasio", "gym", "crunch", "teatro",
        "entradas", "ticketmaster", "twitch", "patreon", "dazn", "filmin",
    ],
    "transporte": [
        "repsol", "cepsa", "bp", "shell", "galp", "gasolinera", "estacion de servicio", "renfe", "metro", "emt", "taxi", "cabify",
        "uber", "bolt", "blablacar", "parking", "peaje", "autobus", "alsa", "tren", "bicing", "bicimad", "cercanias", "tmb",
    ],
    "supermercado": [
        "mercadona", "lidl", "carrefour", "dia", "aldi", "alcampo", "eroski", "consum", "hipercor", "supercor", "froiz", "ahorramas",
        "gadis", "caprabo", "condis", "spar", "supermercado", "supermercados", "bonpreu", "masymas", "coviran", "family cash", "supeco",
    ],
    "salud": ["farmacia", "farmacias", "clinica", "dentista", "hospital", "optica", "medico", "sanitas", "adeslas", "fisio", "fisioterapia", "podologo", "psicologo"],
    "hogar_facturas": [
        "iberdrola", "endesa", "naturgy", "luz", "agua", "gas natural", "canal isabel", "movistar", "vodafone", "orange", "digi", "masmovil",
        "yoigo", "pepephone", "seguro", "mapfre", "linea directa", "alquiler", "comunidad", "hipoteca", "ikea", "leroy merlin", "bricomart",
        "internet", "fibra", "wifi",
    ],
    "viajes": ["booking", "airbnb", "ryanair", "vueling", "iberia", "hotel", "hoteles", "easyjet", "expedia", "trivago", "avianca", "aena", "hostal", "camping"],
    "compras": [
        "amazon", "zara", "pull and bear", "pull&bear", "bershka", "stradivarius", "h&m", "primark", "el corte ingles", "decathlon",
        "mediamarkt", "media markt", "fnac", "aliexpress", "zalando", "shein", "pccomponentes", "wallapop", "ebay", "tienda", "tiendas", "mango", "lefties", "worten",
    ],
}


def _compile(keywords):
    # Palabra completa sobre texto normalizado: «dia» no encaja con «media».
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(squash(k)) for k in keywords) + r")(?![a-z0-9])") if keywords else None


class Categorizer:
    """Las reglas propias del usuario se evalúan ANTES que las de por defecto (el fichero local manda)."""

    def __init__(self, extra: dict = None):
        self._rules = [(category, _compile(words)) for category, words in (extra or {}).items() if words]
        self._rules += [(category, _compile(words)) for category, words in DEFAULT_RULES.items()]

    @classmethod
    def from_file(cls, path) -> "Categorizer":
        path = Path(path)
        return cls(json.loads(path.read_text(encoding="utf-8")) if path.exists() else None)

    def category(self, *texts: str) -> str:
        """Categoría según el primer texto (comercio) y, si no encaja, los siguientes (concepto completo)."""
        for text in texts:
            normalized = squash(text or "")
            if not normalized:
                continue
            for category, pattern in self._rules:
                if pattern.search(normalized):
                    return category
        return "otros"


@dataclass(frozen=True)
class Parsed:
    kind: str                 # purchase | bizum | transfer | refund | income | fee | other
    merchant: object          # comercio o contraparte tal cual aparece (str) o None
    category: str


BIZUM_OUT = re.compile(r"^BIZUM\s+A\s+(?P<name>.+?)\s+DE\s+.+?\s+CONCEPTO", re.I)
BIZUM_OUT_SHORT = re.compile(r"^BIZUM\s+A\s+(?P<name>.+?)\s+CONCEPTO", re.I)
BIZUM_IN = re.compile(r"^BIZUM\s+DE\s+(?P<name>.+?)\s+CONCEPTO", re.I)
TRANSFER = re.compile(r"^TRANSF\w*\.?\s+(?:INMEDIATA\s+)?(?:(?:DE|A)\s+)?(?P<name>.+?)\s*(?:,|\s+CONCEPTO|$)", re.I)
PURCHASE_EN = re.compile(r"\bEN\s+(?P<m>[^,]+?)\s*,", re.I)
PURCHASE_FOREIGN = re.compile(r"^COMPRA\s+(?P<m>[^,]+?)\s*,", re.I)
REFUND = re.compile(r"^(ANUL|DEVOLUCI|DEVOL\b|RETROCESI)", re.I)
FEE = re.compile(r"^(COMISI|MANTENIMIENTO|INTERESES|CUOTA)", re.I)


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip(" .:*")


def parse(description: str, categorizer: Categorizer = None) -> Parsed:
    """Interpreta el texto de un movimiento (ya enmascarado). No lanza excepciones con textos raros."""
    categorizer = categorizer or Categorizer()
    text = " ".join(description.split())

    if re.match(r"^BIZUM\b", text, re.I):
        found = BIZUM_OUT.match(text) or BIZUM_OUT_SHORT.match(text) or BIZUM_IN.match(text)
        return Parsed("bizum", _clean(found.group("name")) if found else None, "bizum")

    if re.match(r"^TRANSF", text, re.I):
        found = TRANSFER.match(text)
        return Parsed("transfer", _clean(found.group("name")) if found else None, "transferencias")

    if FEE.match(text):
        return Parsed("fee", None, "comisiones")

    # Los datos de tarjeta y la comisión de cambio de divisa van al final («…, TARJETA XXXX , COMISION 1,20»): no describen
    # el comercio y, si se usaran para categorizar, toda compra en el extranjero acabaría en «comisiones».
    body = re.split(r"\s*,\s*TARJ", text, maxsplit=1, flags=re.I)[0]
    merchant = None
    if re.match(r"^(PAGO|COMPRA|ANUL|DEVOL|RETROCES)", text, re.I):
        found = PURCHASE_EN.search(text) or PURCHASE_FOREIGN.match(text)
        merchant = _clean(found.group("m")) if found else None

    if REFUND.match(text):
        return Parsed("refund", merchant, categorizer.category(merchant or "", body))
    if re.match(r"^INGRESO", text, re.I):
        return Parsed("income", merchant, "otros")
    if re.match(r"^(PAGO|COMPRA)\b", text, re.I):
        return Parsed("purchase", merchant, categorizer.category(merchant or "", body))
    # Recibos y cargos sin comercio reconocible: las primeras palabras sin números sirven de identificador
    # (así «RECIBO ALQUILER VIVIENDA 123» se agrupa siempre igual y puede detectarse como recurrente).
    words = [w for w in re.split(r"[\s,;]+", text) if w and not any(ch.isdigit() for ch in w)]
    label = _clean(" ".join(words[:4])) or None
    return Parsed("other", label, categorizer.category(body))
