"""Primer prompt a mano (M7): una pregunta del dataset -> consulta JSON, con tiempos.

Uso (en la Pi):  python3 ask.py q001            # una pregunta del dataset
                 python3 ask.py "texto libre"   # pregunta suelta (hoy = --today)
Opciones:        --model qwen2.5:1.5b  --today 2026-11-15  --no-format

Solo biblioteca estándar. Habla con Ollama en 127.0.0.1:11434 (nada sale de la Pi).
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
CONSULTAS = HERE.parent / "consultas"
sys.path.insert(0, str(CONSULTAS))
import validate as v  # noqa: E402  (reutiliza el validador del dataset)

OLLAMA = "http://127.0.0.1:11434/api/chat"

SYSTEM = """Eres un traductor: conviertes una pregunta sobre cuentas bancarias en una consulta JSON. No respondes a la pregunta ni calculas nada.
Hoy es {today}. Las fechas van en formato AAAA-MM-DD, inclusivas.
Reglas:
- intent: aggregate (sumar/contar), list_transactions (listar), last_transaction (el último), balance (saldo), compare_periods (comparar dos periodos), top_merchants (ranking de comercios), recurring (cargos periódicos, suscripciones), unknown (ambigua, ajena al banco o una orden de operar).
- direction: out = gastos, in = ingresos, any = ambos. Todos los campos aparecen siempre; lo que no aplica va a null.
- este mes = día 1 → hoy. El mes pasado = mes anterior completo. Los últimos N días = hoy-(N-1) → hoy.
- Sin periodo en la pregunta: from y to a null.
- Si es unknown, rellena clarification con una pregunta breve en español.
Categorías: supermercado, restaurantes, transporte, ocio_suscripciones, salud, compras, hogar_facturas, viajes, efectivo, comisiones, bizum, transferencias, otros."""

# Ejemplos propios, que NO están en el dataset de evaluación (no se puede evaluar con lo que se enseña).
SHOTS = [
    ("¿Cuánto gasté en gasolina en agosto?", {
        "intent": "aggregate", "metric": "sum", "group_by": None, "direction": "out", "category": "transporte",
        "text": None, "from": "2026-08-01", "to": "2026-08-31", "compare_from": None, "compare_to": None,
        "min_amount": None, "max_amount": None, "sort": None, "limit": None, "clarification": None}),
    ("¿Cuál es mi saldo?", {
        "intent": "balance", "metric": None, "group_by": None, "direction": "any", "category": None,
        "text": None, "from": None, "to": None, "compare_from": None, "compare_to": None,
        "min_amount": None, "max_amount": None, "sort": None, "limit": None, "clarification": None}),
]


def build_messages(question: str, today: str) -> list:
    messages = [{"role": "system", "content": SYSTEM.format(today=today)}]
    for text, query in SHOTS:
        messages.append({"role": "user", "content": text})
        messages.append({"role": "assistant", "content": json.dumps(query, ensure_ascii=False)})
    messages.append({"role": "user", "content": question})
    return messages


def schema_for_ollama() -> dict:
    schema = dict(v.SCHEMA)
    schema.pop("$schema", None)
    return schema


def compare(expected: dict, got: dict) -> list:
    """Campos que difieren entre la consulta esperada y la obtenida."""
    return [k for k in expected if expected[k] != got.get(k)]


def call(model: str, messages: list, use_format: bool) -> dict:
    body = {
        "model": model, "messages": messages, "stream": False, "keep_alive": "5m",
        "options": {"temperature": 0, "num_ctx": 2048, "num_predict": 256},
    }
    if use_format:
        body["format"] = schema_for_ollama()
    request = urllib.request.Request(OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def seconds(ns) -> float:
    return (ns or 0) / 1e9


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", help="id del dataset (q001…) o texto libre")
    parser.add_argument("--model", default="qwen2.5:1.5b")
    parser.add_argument("--today", default="2026-11-15")
    parser.add_argument("--no-format", action="store_true", help="no restringir la salida con el esquema")
    args = parser.parse_args(argv)

    expected, question, today = None, args.question, args.today
    for _, item in v.load(CONSULTAS / "questions.jsonl"):
        if item.get("id") == args.question:
            expected, question, today = item["expected"], item["question"], item["today"]
    print(f"Pregunta: {question}   (hoy = {today})")

    reply = call(args.model, build_messages(question, today), not args.no_format)
    content = reply["message"]["content"]
    print("Respuesta cruda del modelo:\n" + content)

    try:
        got = json.loads(content)
    except json.JSONDecodeError as error:
        print(f"\nJSON inválido: {error}")
        got = None
    if got is not None:
        problems = v.check(got, v.SCHEMA)
        if not problems:
            problems = v.semantic_errors(got)
        print("\nValidación:", "correcta" if not problems else problems)
        if expected is not None:
            diff = compare(expected, got)
            print("Frente a lo esperado:", "idéntica" if not diff else f"difiere en {diff}")
            for key in diff:
                print(f"  {key}: esperado {expected[key]!r}, obtenido {got.get(key)!r}")

    pe, ge = reply.get("prompt_eval_count", 0), reply.get("eval_count", 0)
    pt, gt = seconds(reply.get("prompt_eval_duration")), seconds(reply.get("eval_duration"))
    print(
        f"\nTiempos: total {seconds(reply.get('total_duration')):.1f} s"
        f" | carga del modelo {seconds(reply.get('load_duration')):.1f} s"
    )
    print(f"  prompt:   {pe} tokens en {pt:.1f} s" + (f" ({pe / pt:.1f} tok/s)" if pt else ""))
    print(f"  generado: {ge} tokens en {gt:.1f} s" + (f" ({ge / gt:.1f} tok/s)" if gt else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
