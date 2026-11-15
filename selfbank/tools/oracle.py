"""Cálculo independiente en Python puro, para contrastar el SQL (no comparte código de consulta con el ejecutor).

Parte de los movimientos en bruto, los normaliza con la misma importación y filtra y suma con bucles de Python.
Sirve en los tests (datos sintéticos) y en la prueba de sistema (datos reales, solo imprime coincidencias).
"""

import random

from selfbank import contract, ingest
from selfbank.categorize import Categorizer
from selfbank.text import squash

CATEGORIES = ["supermercado", "restaurantes", "transporte", "ocio_suscripciones", "bizum", "compras", "efectivo", "hogar_facturas", "transferencias"]
PERIODS = ["este_mes", "mes_pasado", "este_ano", "ultimos_30_dias", "ultimos_90_dias", "ultimos_365_dias", "octubre", "septiembre", "ayer", "hoy", "ano_pasado"]


class Oracle:
    def __init__(self, raw_rows: list, categorizer: Categorizer = None):
        categorizer = categorizer or Categorizer()
        self.rows = []
        for raw in raw_rows:
            if (raw.get("status") or "BOOK") != "BOOK":
                continue
            try:
                self.rows.append(ingest.normalize_transaction(raw, "c", categorizer))
            except ingest.IngestError:
                continue

    def select(self, q: dict) -> list:
        out = []
        for r in self.rows:
            if q["direction"] in ("out", "in") and r["direction"] != q["direction"]:
                continue
            if q["from"] and not (q["from"] <= r["booking_date"] <= q["to"]):
                continue
            if q["category"] and r["category"] != q["category"]:
                continue
            if q["text"] and not any(squash(t) in r["description_norm"] for t in q["text"].split("|")):
                continue
            if q["min_amount"] is not None and r["amount_cents"] < round(q["min_amount"] * 100):
                continue
            if q["max_amount"] is not None and r["amount_cents"] > round(q["max_amount"] * 100):
                continue
            out.append(r)
        return out

    @staticmethod
    def total(rows: list, direction: str) -> int:
        if direction in ("out", "in"):
            return sum(r["amount_cents"] for r in rows)
        return sum(r["amount_cents"] if r["direction"] == "in" else -r["amount_cents"] for r in rows)


def random_model(rng: random.Random, texts: list = None) -> dict:
    """Consulta de agregado aleatoria (forma del modelo) para el contraste."""
    model = {"intent": "aggregate", "metric": rng.choice(["sum", "count"]), "direction": rng.choice(["out", "in", "any"])}
    if rng.random() < 0.5:
        model["category"] = rng.choice(CATEGORIES)
    if rng.random() < 0.5:
        model["period"] = rng.choice(PERIODS)
    if texts and rng.random() < 0.2:
        model["text"] = rng.choice(texts)
    if rng.random() < 0.2:
        model["min_amount"] = rng.choice([5, 20, 50])
    if rng.random() < 0.15:
        model["max_amount"] = rng.choice([30, 100, 700])
    return model


def compare_totals(executor, oracle: Oracle, today, n: int = 300, seed: int = 2026, texts: list = None) -> tuple:
    """(coincidencias, comparadas). Compara recuento e importe del ejecutor contra el oráculo."""
    rng = random.Random(seed)
    same = compared = 0
    for _ in range(n):
        q = contract.expand(contract.sanitize(random_model(rng, texts)), today)
        if q["min_amount"] is not None and q["max_amount"] is not None and q["min_amount"] > q["max_amount"]:
            continue
        rows = oracle.select(q)
        got = executor.run(q)
        compared += 1
        if got["count"] == len(rows) and abs(got["value"] - oracle.total(rows, q["direction"]) / 100) < 0.005:
            same += 1
    return same, compared
