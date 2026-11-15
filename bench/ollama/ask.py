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
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
CONSULTAS = HERE.parent / "consultas"
sys.path.insert(0, str(CONSULTAS))
import contract  # noqa: E402
import validate as v  # noqa: E402  (reutiliza el validador del dataset)

OLLAMA = "http://127.0.0.1:11434/api/chat"

SYSTEM = """Eres un traductor: conviertes una pregunta sobre cuentas bancarias en una consulta JSON. No respondes a la pregunta ni calculas nada.
Escribe SOLO los campos que apliquen; no pongas campos vacíos ni null. Las fechas no las calculas: usa una etiqueta en "period".
Hoy es {today}.
intent: aggregate (sumar o contar), list_transactions (listar movimientos), last_transaction (el último), balance (saldo), compare_periods (comparar dos periodos), top_merchants (ranking de comercios), recurring (cargos periódicos, suscripciones), unknown (ambigua, ajena al banco, análisis o consejos, o una orden de operar).
direction: out si dice gastar, pagar o comprar; in si dice ingresar o cobrar. Omítelo si no aplica.
period y compare_period (el de referencia): hoy, ayer, este_mes, mes_pasado, esta_semana, semana_pasada, este_trimestre, este_ano, ano_pasado, ultimos_7_dias, ultimos_15_dias, ultimos_30_dias, ultimos_90_dias, ultimos_365_dias, o el nombre de un mes (enero…diciembre). Sin periodo en la pregunta, omítelo.
group_by solo si piden un desglose (por categorías, por mes). text = comercio o concepto que nombra el usuario.
category: supermercado, restaurantes, transporte, ocio_suscripciones, salud, compras, hogar_facturas, viajes, efectivo, comisiones, bizum, transferencias, otros.
unknown necesita reason: ambigua (falta contexto), fuera_de_alcance (no es sobre movimientos o pide análisis), operacion (pide transferir, pagar, bloquear…)."""

# Ejemplos propios, que NO están en el dataset de evaluación (no se puede evaluar con lo que se enseña).
SHOTS = [
    ("¿Cuánto gasté en gasolina en agosto?",
     {"intent": "aggregate", "metric": "sum", "direction": "out", "category": "transporte", "period": "agosto"}),
    ("¿Cuál es mi saldo?", {"intent": "balance"}),
    ("Dame la receta de la tortilla", {"intent": "unknown", "reason": "fuera_de_alcance"}),
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


def call(model: str, messages: list, use_format: bool, think=None, keep_alive: str = "5m") -> dict:
    body = {
        "model": model, "messages": messages, "stream": False, "keep_alive": keep_alive,
        "options": {"temperature": 0, "num_ctx": 2048, "num_predict": 256},
    }
    if think is not None:  # False desactiva el razonamiento de modelos tipo Qwen3
        body["think"] = think
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
    parser.add_argument("--no-think", action="store_true", help="desactiva el razonamiento (qwen3, qwen3.5…)")
    args = parser.parse_args(argv)

    expected, question, today = None, args.question, args.today
    for _, item in v.load(CONSULTAS / "questions.jsonl"):
        if item.get("id") == args.question:
            expected, question, today = item["expected"], item["question"], item["today"]
    print(f"Pregunta: {question}   (hoy = {today})")

    reply = call(args.model, build_messages(question, today), not args.no_format, False if args.no_think else None)
    content = reply["message"]["content"]
    print("Respuesta cruda del modelo:\n" + content)

    try:
        got = json.loads(content)
    except json.JSONDecodeError as error:
        print(f"\nJSON inválido: {error}")
        got = None
    if got is not None:
        problems = v.check(got, v.SCHEMA) or contract.model_errors(got)
        print("\nValidación:", "correcta" if not problems else problems)
        if not problems:
            today_date = date.fromisoformat(today)
            final = contract.expand(contract.refine(question, got), today_date)
            print("Consulta completa (tras palabras clave y fechas):")
            print(json.dumps({k: x for k, x in final.items() if x is not None}, ensure_ascii=False))
            if expected is not None:
                want = contract.expand(expected, today_date)
                diff = compare(want, final)
                print("Frente a lo esperado:", "idéntica" if not diff else f"difiere en {diff}")
                for key in diff:
                    print(f"  {key}: esperado {want[key]!r}, obtenido {final.get(key)!r}")

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
