"""Ruta rápida: clasifica la pregunta solo con reglas, sin modelo, cuando está segura.

Devuelve una consulta en la forma del modelo (solo `intent` y poco más) o None si hay duda.
Principio de seguridad: si la pregunta contiene alguna palabra que el router no conoce (un comercio
en minúsculas, un concepto raro…), no adivina: devuelve None y decide el modelo. Así la ruta rápida
pierde cobertura, pero no da una cifra equivocada por no haber entendido a qué se refería.
Solo biblioteca estándar.
"""

import re

import contract
import rules
from rules import normalize

FUNCTION_WORDS = set("""
el la los las un una unos unas de del al a en por para con sin sobre entre desde hasta que quien quienes cual cuales
cuanto cuanta cuantos cuantas como cuando donde y o u e ni mi mis tu tus su sus me te se lo le les nos es son fue fueron
ha han he hemos hay tengo tienes tiene tienen tuve hice hizo hicimos fui ido voy va van sido soy eres llevo lleva llevas
llevamos quiero puedo puedes dame dime algun alguna algunos algunas otro otra otros otras mismo misma cada todo toda todos
todas poco mucho muy mas menos tan tanto tambien solo ya aun ahora siempre nunca antes despues durante total totales
dinero euros euro eur importe importes cantidad cuenta cuentas tarjeta banco vez veces dia dias semana semanas mes meses ano
anos trimestre trimestres hoy ayer pasado pasada anterior ultimo ultima ultimos ultimas primero mayor mayores menor menores
caro caros cara caras alto altos barato baratos bajo bajos grande grandes fijo fijos fija fijas periodico periodicos
activo activos domiciliado domiciliados domiciliada concepto conceptos categoria categorias comercio comercios sitio sitios
tienda tiendas lugar lugares contacto contactos persona personas tipo tipos orden este esta estos estas ese esa
""".split())

KNOWN_STEMS = re.compile(
    r"gast\w*|compr\w*|pag(?:o|os|ue|ues|ado|ados|ada|adas|ar|aron|amos|as|an)|ingres\w*|recib\w*|cobr\w*|movimiento\w*"
    r"|transferenc\w*|sac(?:ar|ado|o|ue|amos)|ped(?:i|ido|idos|imos|ir)|cargo\w*|su(?:bs|s)cripci\w*|domicili\w*|saldo|ensen\w*"
    r"|muestr\w*|list\w*|busc\w*|mostr\w*|compar\w*|desglos\w*|evolucion|mensual\w*|bizum\w*|cajero\w*|efectivo|comision\w*"
    r"|supermercado\w*|restaurante\w*|cenas?|ocio|gasolin\w*|transporte|salud|viaje\w*|hogar|factura\w*|luz|agua|farmaci\w*"
    r"|medic\w*|dentista\w*|clinica\w*|hospital\w*|alquiler|hipoteca|wifi|internet"
)

BALANCE = re.compile(r"\bsaldo\b|\bcuanto (?:dinero )?tengo\b|\bdinero (?:que )?tengo\b|\bcuanto hay en (?:mi )?cuenta\b")
RECURRING = re.compile(
    r"\bsu(?:bs|s)cripci(?:on|ones)\b|\bcargos? periodicos?\b|\brecibos? domiciliad\w+|\bpagos? fijos?\b|\bdomiciliacion(?:es)?\b|\brecibos? activos?\b"
)
COMPARE = re.compile(r"\bcompar\w+|\bfrente a\b|\bcon respecto a\b|\bmas\b.*\bque\b|\bmenos\b.*\bque\b")
TOP_MERCHANTS = re.compile(
    r"\bdonde\b.*\bmas\b|\b(?:sitios?|comercios?|tiendas?|lugares)\b.*\bmas\b|\bmas\b.*\b(?:sitios?|comercios?|tiendas?)\b"
    r"|\bde quien\b.*\bmas\b|\ba que (?:tienda|comercio|sitio)\b"
)
LIST_VERB = re.compile(
    r"^(?:ensename|muestrame|muestra|dime|busca|lista|listame|ver|que (?:pagos|compras|movimientos|gastos|ingresos)"
    r"|cuales son (?:mis|los) (?:pagos|compras|movimientos))\b"
)
AGGREGATE = re.compile(r"\bcuant[oa]s?\b|\btotal(?:es)?\b|\bllevo (?:gastado|pagado|ingresado)\b|\bhe (?:gastado|ingresado)\b")


def tokens(question: str) -> list:
    return re.findall(r"[a-zñ]+|\d+", normalize(question))


def unresolved(question: str) -> list:
    """Palabras que el router no conoce (posibles comercios o conceptos que habría que buscar)."""
    out = []
    for token in tokens(question):
        if token.isdigit() or token in FUNCTION_WORDS or token in contract.MONTHS or KNOWN_STEMS.fullmatch(token):
            continue
        out.append(token)
    return out


def fast_intent(question: str):
    """Consulta mínima en forma del modelo, o None si hay que preguntar al modelo."""
    forced = rules.forced_unknown(question)
    if forced:
        return forced
    if unresolved(question):
        return None
    text = normalize(question)
    tags = rules.periods(question)
    if rules.OUT_CUES.search(text) and rules.IN_CUES.search(text):
        return None  # gasto e ingreso a la vez: puede ser «ambos» o una pregunta compuesta; que decida el modelo

    if BALANCE.search(text):
        return None if tags else {"intent": "balance"}
    if RECURRING.search(text):
        return {"intent": "recurring"}
    if rules.LAST_ONE.search(text):
        return {"intent": "last_transaction"}
    if len(tags) >= 2 and COMPARE.search(text):
        return {"intent": "compare_periods"}
    if rules.TOP_SORT.search(text):
        return {"intent": "list_transactions"}
    if rules.group_by_cue(question):
        return {"intent": "aggregate"}
    if TOP_MERCHANTS.search(text):
        return {"intent": "top_merchants"}
    asks_total = re.search(r"\bcuant\w*|\btotal\b", text)
    if rules.amounts(question) and not asks_total:
        return {"intent": "list_transactions"}
    if rules.LAST_N.search(text) or LIST_VERB.match(text.lstrip("¿¡ ")):
        return {"intent": "list_transactions"}
    if AGGREGATE.search(text):
        return {"intent": "aggregate", "metric": "count" if rules.COUNT_CUE.search(text) else "sum"}
    return None
