"""Preguntas sugeridas para los botones del chat. Todas deben resolverse por la ruta rápida (sin modelo)."""

SUGGESTIONS = [
    ("spend_month", "Gasto este mes", "¿Cuánto llevo gastado este mes?"),
    ("income_month", "Ingresos este mes", "¿Cuánto me han ingresado este mes?"),
    ("by_category", "Gasto por categoría", "Dime el gasto por categorías de los últimos 30 días"),
    ("last_bizum", "Último Bizum", "¿Cuál fue mi último Bizum?"),
    ("balance", "Saldo", "¿Cuál es mi saldo?"),
    ("subscriptions", "Suscripciones", "¿Qué suscripciones pago?"),
    ("top_places", "Dónde gasto más", "¿Dónde gasto más dinero?"),
    ("compare", "Este mes frente al anterior", "¿Gasté más este mes que el mes pasado?"),
]


def as_list() -> list:
    return [{"id": i, "label": label, "question": question} for i, label, question in SUGGESTIONS]
