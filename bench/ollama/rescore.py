"""Vuelve a puntuar respuestas ya guardadas por run.py con la tubería actual (sin llamar al modelo).

Uso:  python3 -B rescore.py out/qwen2.5_3b-dev-XXXX.json [otro.json ...]
Sirve para ver el efecto de cambios en reglas, validación o puntuación sin gastar CPU ni calentar la Pi.
Las latencias y recursos se conservan tal como se midieron.
"""

import json
import sys
from pathlib import Path

import ask
import run
import validate as v


def main(paths) -> int:
    items = {item["id"]: item for _, item in v.load(ask.CONSULTAS / "questions.jsonl")}
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = []
        for row in data["rows"]:
            new = dict(row)
            new.update(run.score(items[row["id"]], row["content"]))
            rows.append(new)
        before, after = data["summary"], run.summarize(rows)
        print(f"\n{data['model']} ({data['split']}), {after['n']} respuestas")
        for key in ("schema_ok_pct", "intent_pct", "exact_pct", "reason_pct"):
            print(f"  {key}: {before.get(key)} -> {after.get(key)}")
        print(f"  p95: {after['total_p95_s']} s (medido, no cambia)")
        for r in rows:
            if not r["exact"] and r.get("repeat", 0) == 0:
                why = "esquema/coherencia" if not r["schema_ok"] else f"difiere en {r['diff']}"
                print(f"  {r['id']}: {why} | {r['content'][:110]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
