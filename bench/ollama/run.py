"""Runner de evaluación (M8+M9): pasa el dataset por un modelo y mide precisión, latencia y recursos.

Uso (en la Pi):
  python3 -B run.py --model qwen2.5:1.5b --split dev
  python3 -B run.py --model qwen3:4b --split dev --no-think
Opciones: --split dev|test|all  --repeats 3  --no-format  --no-think  --limit N (para probar)

Solo biblioteca estándar. Guarda el detalle en bench/ollama/out/ (ignorado por git).
"""

import argparse
import json
import math
import subprocess
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

import ask
import contract
import rules
import split
import validate as v

OUT = Path(__file__).parent / "out"


# ---- funciones puras (con tests) ----------------------------------------------

def percentile(values: list, p: float):
    """Percentil por rango más cercano (p entre 0 y 100)."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def score(item: dict, content: str) -> dict:
    """Puntúa la respuesta cruda del modelo frente a lo esperado."""
    result = {"json_ok": False, "schema_ok": False, "intent_ok": False, "model_intent_ok": False,
              "exact": False, "reason_ok": None, "diff": []}
    try:
        got = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return result
    if not isinstance(got, dict):
        return result
    result["json_ok"] = True
    expected = item["expected"]
    # intent_ok es el del sistema (tras las reglas); model_intent_ok, el que dijo el modelo.
    result["model_intent_ok"] = result["intent_ok"] = got.get("intent") == expected["intent"]
    if v.check(got, v.SCHEMA):
        return result
    today = date.fromisoformat(item["today"])
    final, errors = ask.interpret(item["question"], got, today)
    result["schema_ok"] = not errors
    if errors:  # una salida inválida equivale a «no te he entendido»: el sistema responde como un unknown
        final = contract.expand({"intent": "unknown", "reason": "ambigua"}, today)
    result["intent_ok"] = final["intent"] == expected["intent"]
    want = contract.expand(expected, today)
    if expected["intent"] == "unknown":
        result["reason_ok"] = got.get("reason") == expected["reason"]
    # La aclaración depende solo de `reason`, que se puntúa aparte.
    final.pop("clarification"), want.pop("clarification")
    for query in (final, want):  # «Burger King» y «burger king», «nómina» y «nomina»: la misma búsqueda
        if isinstance(query.get("text"), str):
            query["text"] = rules.normalize(query["text"]).strip()
    result["diff"] = ask.compare(want, final)
    result["exact"] = not result["diff"]
    return result


def summarize(rows: list) -> dict:
    """rows: una fila por llamada (sin la de calentamiento)."""
    n = len(rows)
    if not n:
        return {"n": 0}
    pct = lambda key: round(100 * sum(1 for r in rows if r[key]) / n, 1)  # noqa: E731
    totals = [r["total_s"] for r in rows]
    speeds = [r["gen_tokens"] / r["gen_s"] for r in rows if r["gen_s"] > 0]
    reasons = [r["reason_ok"] for r in rows if r["reason_ok"] is not None]
    temps = [r["temp_c"] for r in rows if r.get("temp_c") is not None]
    avail = [r["mem_available_mib"] for r in rows if r.get("mem_available_mib") is not None]
    rss = [r["ollama_rss_mib"] for r in rows if r.get("ollama_rss_mib") is not None]
    return {
        "n": n,
        "json_ok_pct": pct("json_ok"), "schema_ok_pct": pct("schema_ok"),
        "intent_pct": pct("intent_ok"), "model_intent_pct": pct("model_intent_ok"), "exact_pct": pct("exact"),
        "reason_pct": round(100 * sum(reasons) / len(reasons), 1) if reasons else None,
        "total_p50_s": round(percentile(totals, 50), 2), "total_p95_s": round(percentile(totals, 95), 2),
        "total_max_s": round(max(totals), 2),
        "gen_tokens_mean": round(sum(r["gen_tokens"] for r in rows) / n, 1),
        "gen_tok_s_mean": round(sum(speeds) / len(speeds), 1) if speeds else None,
        "temp_max_c": max(temps) if temps else None,
        "throttled_any": any(r.get("throttled") not in (None, "0x0") for r in rows),
        "mem_available_min_mib": min(avail) if avail else None,
        "ollama_rss_max_mib": max(rss) if rss else None,
    }


def passes(summary: dict) -> dict:
    """Criterio de paso de la Fase 0 (docs/FASE0.md)."""
    checks = {
        "intent >= 90 %": summary["intent_pct"] >= 90,
        "p95 caliente < 8 s": summary["total_p95_s"] < 8,
        "sin throttling": not summary["throttled_any"],
        "RAM libre > 1 GiB": summary["mem_available_min_mib"] is None or summary["mem_available_min_mib"] > 1024,
    }
    checks["PASA"] = all(checks.values())
    return checks


# ---- medición en la Pi (mejor esfuerzo: fuera de la Pi devuelve None) -----------

def sh(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def sample() -> dict:
    temp = sh("vcgencmd", "measure_temp").replace("temp=", "").replace("'C", "")
    throttled = sh("vcgencmd", "get_throttled").replace("throttled=", "")
    mem = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                mem = int(line.split()[1]) // 1024
    except OSError:
        pass
    rss = [int(x) for x in sh("ps", "-C", "ollama", "-o", "rss=").split() if x.isdigit()]
    return {
        "temp_c": float(temp) if temp else None, "throttled": throttled or None,
        "mem_available_mib": mem, "ollama_rss_mib": sum(rss) // 1024 if rss else None,
    }


def unload(model: str):
    body = json.dumps({"model": model, "keep_alive": 0}).encode()
    request = urllib.request.Request("http://127.0.0.1:11434/api/generate", body, {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=30).read()
    except OSError:
        pass


def wait_cool(target_c: float, timeout_s: float = 600):
    """Espera (con tope) a que la temperatura baje de target_c. 0 = no esperar."""
    waited = 0
    while target_c and waited < timeout_s:
        temp = sample()["temp_c"]
        if temp is None or temp <= target_c:
            return
        print(f"  esperando a que la Pi baje de {target_c} °C (ahora {temp} °C)…")
        time.sleep(15)
        waited += 15


def run_one(model, item, use_format, think) -> dict:
    started = time.perf_counter()
    reply = ask.call(model, ask.build_messages(item["question"], item["today"]), use_format, think, keep_alive="30m")
    wall = time.perf_counter() - started
    row = score(item, reply["message"]["content"])
    row.update({
        "id": item["id"], "content": reply["message"]["content"], "wall_s": round(wall, 3),
        "total_s": ask.seconds(reply.get("total_duration")), "load_s": ask.seconds(reply.get("load_duration")),
        "prompt_tokens": reply.get("prompt_eval_count", 0), "prompt_s": ask.seconds(reply.get("prompt_eval_duration")),
        "gen_tokens": reply.get("eval_count", 0), "gen_s": ask.seconds(reply.get("eval_duration")),
    })
    row.update(sample())
    return row


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--split", choices=["dev", "test", "holdout", "fresh", "blind", "all"], default="dev")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--no-format", action="store_true")
    parser.add_argument("--no-think", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--pause", type=float, default=0, help="segundos de pausa entre llamadas (simula uso real)")
    parser.add_argument("--cooldown", type=float, default=0, help="espera a que la Pi baje de esta temperatura antes de empezar")
    args = parser.parse_args(argv)

    items = [item for _, item in v.load(ask.CONSULTAS / "questions.jsonl")]
    parts = split.assign(items)
    wanted = {"blind": ("test", "holdout"), "all": ("dev", "test", "holdout", "fresh")}.get(args.split, (args.split,))
    chosen = [i for i in items if parts[i["id"]] in wanted]
    if args.limit:
        chosen = chosen[: args.limit]
    think = False if args.no_think else None
    use_format = not args.no_format

    print(f"Modelo {args.model} | conjunto {args.split} ({len(chosen)} preguntas x {args.repeats}) | format={use_format} think={think}")
    wait_cool(args.cooldown)
    before = sample()
    print(f"Antes: {before}")
    cold = run_one(args.model, chosen[0], use_format, think)  # calentamiento: carga el modelo y la caché del prefijo
    print(f"Arranque en frío: {cold['total_s']:.1f} s (carga {cold['load_s']:.1f} s, prompt {cold['prompt_s']:.1f} s)")

    rows = []
    for repeat in range(args.repeats):
        for item in chosen:
            row = run_one(args.model, item, use_format, think)
            row["repeat"] = repeat
            rows.append(row)
            time.sleep(args.pause)
        print(f"  repetición {repeat + 1}/{args.repeats} hecha")
    unload(args.model)

    summary = summarize(rows)
    OUT.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = args.model.replace(":", "_").replace("/", "_")
    path = OUT / f"{tag}-{args.split}-{stamp}.json"
    path.write_text(json.dumps({"model": args.model, "split": args.split, "cold": cold, "summary": summary, "rows": rows},
                               ensure_ascii=False, indent=1), encoding="utf-8")

    print("\nRESUMEN")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print("\nCRITERIO DE PASO")
    for key, value in passes(summary).items():
        print(f"  {'OK ' if value else 'NO '} {key}")

    first = [r for r in rows if r["repeat"] == 0 and not r["exact"]]
    print(f"\nFallos exactos (1.ª repetición): {len(first)} de {len(chosen)}")
    for r in first:
        why = "JSON inválido" if not r["json_ok"] else ("esquema/coherencia" if not r["schema_ok"] else f"difiere en {r['diff']}")
        print(f"  {r['id']}: {why}  | intent {'OK' if r['intent_ok'] else 'MAL'}")
        print(f"      {r['content'][:160]}")
    unstable = {i["id"] for i in chosen if len({r["content"] for r in rows if r["id"] == i["id"]}) > 1}
    print(f"Preguntas con respuestas distintas entre repeticiones: {sorted(unstable) or 'ninguna'}")
    print(f"\nDetalle guardado en {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
