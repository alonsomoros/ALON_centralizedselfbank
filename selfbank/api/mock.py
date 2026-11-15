"""Servidor simulado: cumple el contrato de la API, usa el router y las reglas REALES para interpretar la
pregunta, pero inventa las cifras (deterministas por pregunta) y simula la espera del modelo.

Sirve para desarrollar la pantalla del chat sin banco, sin base de datos y sin Ollama.

  python -m selfbank.api.mock --port 8788 --token dev-token --model-delay 3 --cors-origin http://localhost:5173

Preguntas que provocan situaciones concretas (para probar la pantalla):
  __busy__    -> 503 model_busy (con Retry-After)
  __error__   -> 500 internal
Cualquier pregunta que las reglas no resuelvan por sí solas pasa por el «modelo» simulado y tarda --model-delay.
Solo biblioteca estándar.
"""

import argparse
import hashlib
import json
import os
import random
import secrets
import sys
import threading
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone

from selfbank import answers, describe, pipeline, router
from selfbank.api.server import ApiError, make_server, read_token_file  # noqa: F401
from selfbank.suggestions import SUGGESTIONS, as_list

VERSION = "0.1.0"
MERCHANTS = ["SUPERMERCADO DEMO", "CAFETERIA EJEMPLO", "TIENDA ONLINE DEMO", "GASOLINERA EJEMPLO",
             "SERVICIO STREAMING DEMO", "FARMACIA EJEMPLO", "RESTAURANTE DEMO", "LIBRERIA EJEMPLO"]
CATEGORIES = ["supermercado", "restaurantes", "transporte", "ocio_suscripciones", "salud", "compras", "hogar_facturas"]

class MockEngine:
    def __init__(self, today: date = None, model_delay: float = 3.0, keep: int = 500):
        self.today = today
        self.model_delay = model_delay
        self.keep = keep
        self.answers = OrderedDict()   # id -> pregunta (para validar el feedback)
        self.feedback_log = []
        self._lock = threading.Lock()        # una consulta al modelo a la vez, como el servicio real
        self._state = threading.Lock()

    # ---- contrato ----
    def status(self) -> dict:
        today = self.today or date.today()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        return {
            "ok": True, "version": VERSION, "mode": "mock",
            "model": {"name": "qwen2.5:3b (simulado)", "loaded": True},
            "data": {"last_sync": (now - timedelta(hours=3)).isoformat(), "data_through": (today - timedelta(days=1)).isoformat(), "accounts": 1},
            "consent": {"valid_until": (today + timedelta(days=151)).isoformat(), "days_left": 151},
        }

    def suggestions(self) -> list:
        return as_list()

    def feedback(self, answer_id: str, verdict: str, correction) -> bool:
        with self._state:
            if answer_id not in self.answers:
                return False
            self.feedback_log.append({"answer_id": answer_id, "verdict": verdict, "correction": correction})
            return True

    def ask(self, question: str) -> dict:
        if question == "__busy__":
            raise ApiError(503, "model_busy", "El modelo está ocupado; vuelve a intentarlo en unos segundos.", {"Retry-After": "5"})
        if question == "__error__":
            raise ApiError(500, "internal", "Error interno simulado.")
        today = self.today or date.today()

        fast = router.fast_intent(question)
        if fast is not None:
            source, model_query = "rules", fast
        else:
            source = "model"
            with self._lock:
                time.sleep(self.model_delay)
            # «respuesta del modelo»: un agregado genérico y, como haría el real, las palabras que el router no conoce como `text`
            model_query = {"intent": "aggregate", "metric": "sum"}
            unknown_words = router.unresolved(question)
            if unknown_words:
                model_query["text"] = " ".join(unknown_words)
        query, reason = pipeline.to_query(question, model_query, today)

        result = None if query["intent"] == "unknown" else fake_result(query, question, today)
        answer = answers.render(query, result)
        status = "answered"
        if query["intent"] == "unknown":
            status = "needs_clarification" if reason == "ambigua" else "unsupported"

        answer_id = "ans_" + secrets.token_hex(6)
        with self._state:
            self.answers[answer_id] = question
            while len(self.answers) > self.keep:
                self.answers.popitem(last=False)
        return {
            "id": answer_id, "question": question, "status": status, "reason": reason, "source": source,
            "interpretation": {**describe.describe(query), "query": query},
            "answer": answer, "timing_ms": 0, "mock": True,
        }


# ---- cifras inventadas (deterministas por pregunta; nunca datos reales) ----------------------------

def month_keys(end: date, n: int) -> list:
    """Los n meses que terminan en el de `end`, del más reciente al más antiguo: ['2026-11', '2026-10', ...]."""
    year, month, keys = end.year, end.month, []
    for _ in range(n):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return keys


def fake_result(query: dict, question: str, today: date) -> dict:
    seed = hashlib.sha256((question + json.dumps(query, sort_keys=True)).encode()).hexdigest()
    rng = random.Random(int(seed, 16))
    intent, direction = query["intent"], query.get("direction", "any")
    sign = 1 if direction == "in" else -1

    def amount():
        value = round(rng.uniform(2, 180), 2)
        lo, hi = query.get("min_amount"), query.get("max_amount")
        if lo is not None:
            value = max(value, lo + round(rng.uniform(0, 30), 2))
        if hi is not None:
            value = min(value, hi)
        return value

    def merchants(n):
        pool = [query["text"].upper()] if query.get("text") else MERCHANTS
        return [pool[i % len(pool)] for i in range(n)]

    start = date.fromisoformat(query["from"]) if query.get("from") else today - timedelta(days=60)
    end = date.fromisoformat(query["to"]) if query.get("to") else today
    span = max((end - start).days, 0)

    def transactions(n):
        rows = []
        for name in merchants(n):
            when = end - timedelta(days=rng.randint(0, span))
            value = amount()
            rows.append({"date": when.isoformat(), "description": name,
                         "amount": round((sign if direction != "any" else rng.choice([1, -1])) * value, 2)})
        rows.sort(key=lambda r: r["date"], reverse=True)
        if query.get("sort") in ("amount_desc", "amount_asc"):
            rows.sort(key=lambda r: abs(r["amount"]), reverse=query["sort"] == "amount_desc")
        return rows

    if intent == "balance":
        return {"value": round(rng.uniform(500, 6000), 2)}
    if intent == "aggregate":
        if query.get("group_by"):
            group = query["group_by"]
            if group == "category":
                keys = rng.sample(CATEGORIES, 5)
            elif group == "merchant":
                keys = rng.sample(MERCHANTS, 5)
            else:
                keys = month_keys(end, 6)
            rows = [{"group": key, "amount": rng.randint(1, 30) if query["metric"] == "count" else round(rng.uniform(40, 900), 2),
                     "count": rng.randint(1, 30)} for key in keys]
            rows.sort(key=lambda r: r["amount"], reverse=True)
            return {"rows": rows[: query["limit"]] if query.get("limit") else rows}
        return {"value": round(rng.uniform(20, 2500), 2), "count": rng.randint(1, 40)}
    if intent == "list_transactions":
        return {"rows": transactions(query["limit"] if query.get("limit") else rng.randint(3, 8))}
    if intent == "last_transaction":
        return {"rows": transactions(1)}
    if intent == "compare_periods":
        top = 40 if query.get("metric") == "count" else 2500
        return {"current": round(rng.uniform(10, top), 2), "reference": round(rng.uniform(10, top), 2)}
    if intent == "top_merchants":
        n = query["limit"] if query.get("limit") else 5
        rows = [{"merchant": name, "amount": round(rng.uniform(30, 800), 2), "count": rng.randint(1, 20)} for name in rng.sample(MERCHANTS, min(n, len(MERCHANTS)))]
        rows.sort(key=lambda r: r["amount"], reverse=True)
        return {"rows": rows}
    if intent == "recurring":
        return {"rows": [{"merchant": name, "amount": round(rng.uniform(4, 60), 2), "every_days": rng.choice([7, 30, 30, 365])}
                         for name in rng.sample(MERCHANTS, 4)]}
    raise ValueError(intent)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Servidor simulado de la API de selfbank")
    parser.add_argument("--host", default="127.0.0.1", help="por defecto solo local; no abrir a la red sin motivo")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--token", default=os.environ.get("SELFBANK_TOKEN", "dev-token"),
                        help="visible en `ps`; para uso real prefiere --token-file o la variable SELFBANK_TOKEN")
    parser.add_argument("--token-file", help="fichero (permisos 600) que contiene el token")
    parser.add_argument("--model-delay", type=float, default=3.0, help="segundos que tarda el «modelo» simulado")
    parser.add_argument("--cors-origin", action="append", default=[], help="origen permitido (repetible), p. ej. http://localhost:5173")
    args = parser.parse_args(argv)

    if args.token_file:
        args.token = read_token_file(args.token_file)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"AVISO: escuchando en {args.host}: cualquiera en esa red podrá intentar conectar. Solo el token lo protege.", file=sys.stderr)
    if args.token == "dev-token":
        print("AVISO: token de desarrollo «dev-token». Solo para el servidor simulado en local.", file=sys.stderr)
    server = make_server(MockEngine(model_delay=args.model_delay), args.token, args.host, args.port, args.cors_origin)
    print(f"Servidor SIMULADO en http://{args.host}:{args.port}  (cifras inventadas; Ctrl+C para parar)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nParado.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
