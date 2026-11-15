"""Prompt del traductor pregunta -> consulta JSON (compacta, ver model.schema.json).

El modelo solo traduce: no responde ni calcula. Los ejemplos tienen redacción distinta a la del dataset de
evaluación (no se puede evaluar con lo que se enseña).
"""

import json

from . import contract

SYSTEM = """Eres un traductor: conviertes una pregunta sobre cuentas bancarias en una consulta JSON. No respondes a la pregunta ni calculas nada.
Escribe SOLO los campos que apliquen. No inventes ninguno: si la pregunta no lo dice, no lo pongas. Las fechas no las calculas: usas una etiqueta en "period".
Hoy es {today}.

intent: aggregate (sumar o contar), list_transactions (listar movimientos), last_transaction (el último), balance (saldo), compare_periods (comparar dos periodos), top_merchants (ranking de comercios o sitios), recurring (cargos periódicos, suscripciones), unknown (no es sobre movimientos, es ambigua, pide análisis o consejos, o pide operar).
direction: out si habla de gastar, pagar o comprar; in si habla de ingresar o cobrar. Si no queda claro, omítelo.
period y compare_period (el de referencia, solo en compare_periods): hoy, ayer, este_mes, mes_pasado, esta_semana, semana_pasada, este_trimestre, trimestre_pasado, este_ano, ano_pasado, ultimos_7_dias, ultimos_15_dias, ultimos_30_dias, ultimos_90_dias, ultimos_365_dias, o un mes (enero…diciembre). Sin periodo en la pregunta, no pongas period.
text: el comercio o concepto que nombra el usuario (Mercadona, Netflix). Si nombra un comercio, usa text y NO pongas category.
category: solo si la pregunta nombra un tipo de gasto claro: supermercado, restaurantes, transporte, ocio_suscripciones, salud, compras, hogar_facturas, viajes, efectivo, comisiones, bizum, transferencias, otros.
group_by: solo si piden un desglose (por categorías, por mes). limit: solo si piden un número o "el más…" (limit 1). sort: amount_desc para mayor o más caro, date_desc para últimos o recientes.
unknown lleva reason: ambigua (falta contexto), fuera_de_alcance (no va de dinero, o pide análisis o consejos), operacion (pide transferir, pagar, bloquear…)."""

# Ejemplos propios, con redacción distinta a la del dataset de evaluación.
SHOTS = [
    ("¿Cuánto gasté en gasolina en agosto?",
     {"intent": "aggregate", "metric": "sum", "direction": "out", "category": "transporte", "period": "agosto"}),
    ("¿Cuál es mi saldo?", {"intent": "balance"}),
    ("Dame la receta de la tortilla", {"intent": "unknown", "reason": "fuera_de_alcance"}),
    ("¿Cuánto llevo gastado en Zara este año?",
     {"intent": "aggregate", "metric": "sum", "direction": "out", "text": "Zara", "period": "este_ano"}),
    ("¿Cuáles son mis tres mayores gastos de la semana pasada?",
     {"intent": "list_transactions", "direction": "out", "period": "semana_pasada", "sort": "amount_desc", "limit": 3}),
    ("¿He gastado más hoy que ayer?",
     {"intent": "compare_periods", "metric": "sum", "direction": "out", "period": "hoy", "compare_period": "ayer"}),
    ("¿Qué pagos fijos tengo cada mes?", {"intent": "recurring", "direction": "out"}),
    ("¿Cuántos movimientos tuve la semana pasada?",
     {"intent": "aggregate", "metric": "count", "period": "semana_pasada"}),
    ("Bloquea mi tarjeta", {"intent": "unknown", "reason": "operacion"}),
    ("¿Y el año pasado?", {"intent": "unknown", "reason": "ambigua"}),
    ("¿Cuál fue mi último pago en Spotify?",
     {"intent": "last_transaction", "direction": "out", "text": "Spotify"}),
    ("¿De quién he recibido más dinero este año?",
     {"intent": "top_merchants", "direction": "in", "period": "este_ano"}),
]


def build_messages(question: str, today: str) -> list:
    messages = [{"role": "system", "content": SYSTEM.format(today=today)}]
    for text, query in SHOTS:
        messages.append({"role": "user", "content": text})
        messages.append({"role": "assistant", "content": json.dumps(query, ensure_ascii=False, separators=(",", ":"))})
    messages.append({"role": "user", "content": question})
    return messages


def schema_for_ollama() -> dict:
    """El esquema del modelo, sin la metaclave `$schema`, para el parámetro `format` de Ollama."""
    schema = dict(contract.load_schema(contract.MODEL_SCHEMA_PATH))
    schema.pop("$schema", None)
    return schema
