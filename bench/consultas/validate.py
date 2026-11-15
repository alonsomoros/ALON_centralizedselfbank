"""Validador del dataset pregunta -> consulta (M6). Solo biblioteca estándar.

Uso:  python validate.py [questions.jsonl]

Comprueba, para cada línea del dataset:
  1. que `expected` (consulta del modelo, v2) cumple model.schema.json,
  2. coherencia por intent (contract.model_errors),
  3. que al expandirla (contract.expand) resulta una consulta completa que cumple
     resolved.schema.json (ambos en selfbank/) y las reglas semánticas del ejecutor.
"""

import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
import _path  # noqa: E402,F401  (raíz del repo en sys.path)
from selfbank import contract  # noqa: E402

SCHEMA = contract.load_schema(contract.MODEL_SCHEMA_PATH)
RESOLVED_SCHEMA = contract.load_schema(contract.RESOLVED_SCHEMA_PATH)

from selfbank.schema import check  # noqa: E402,F401  (mismo comprobador que usa la API)


# ---- reglas semánticas ------------------------------------------------------

FILTERS = ["category", "text", "from", "to", "compare_from", "compare_to", "min_amount", "max_amount", "sort", "limit", "metric", "group_by"]


def semantic_errors(q: dict) -> list:
    errors = []
    intent = q["intent"]
    for a, b in (("from", "to"), ("compare_from", "compare_to")):
        if (q[a] is None) != (q[b] is None):
            errors.append(f"{a} y {b} deben ir juntos")
        elif q[a] is not None:
            try:
                if date.fromisoformat(q[a]) > date.fromisoformat(q[b]):
                    errors.append(f"{a} es posterior a {b}")
            except ValueError:
                errors.append(f"fecha inválida en {a}/{b}")
    if q["min_amount"] is not None and q["max_amount"] is not None and q["min_amount"] > q["max_amount"]:
        errors.append("min_amount mayor que max_amount")

    if intent in ("aggregate", "compare_periods") and q["metric"] is None:
        errors.append(f"{intent} exige metric")
    if intent not in ("aggregate", "compare_periods") and q["metric"] is not None:
        errors.append(f"{intent} no admite metric")
    if intent == "top_merchants" and q["group_by"] != "merchant":
        errors.append("top_merchants exige group_by merchant")
    if intent not in ("aggregate", "top_merchants") and q["group_by"] is not None:
        errors.append(f"{intent} no admite group_by")
    if intent == "compare_periods" and (q["from"] is None or q["compare_from"] is None):
        errors.append("compare_periods exige ambos periodos")
    if intent != "compare_periods" and q["compare_from"] is not None:
        errors.append(f"{intent} no admite compare_from/compare_to")
    if intent == "unknown":
        if not q["clarification"]:
            errors.append("unknown exige clarification")
        extra = [k for k in FILTERS if q[k] is not None]
        if extra or q["direction"] != "any":
            errors.append(f"unknown debe tener el resto a null y direction any (sobran: {extra})")
    elif q["clarification"] is not None:
        errors.append("clarification solo se rellena en unknown")
    if intent == "balance":
        extra = [k for k in FILTERS if q[k] is not None]
        if extra or q["direction"] != "any":
            errors.append(f"balance debe tener el resto a null y direction any (sobran: {extra})")
    return errors


def item_errors(item: dict) -> list:
    errors = []
    for key in ("id", "today", "question", "expected"):
        if key not in item:
            errors.append(f"falta {key}")
    if errors:
        return errors
    try:
        today = date.fromisoformat(item["today"])
    except ValueError:
        return ["today no es una fecha ISO"]
    if not item["question"].strip():
        errors.append("question vacía")
    if item.get("split", "holdout") not in ("holdout", "fresh"):
        errors.append("split solo admite 'holdout' o 'fresh' (dev/test se reparten solos)")
    errors += check(item["expected"], SCHEMA)
    if errors:
        return errors
    errors += contract.model_errors(item["expected"])
    if errors:
        return errors
    full = contract.expand(item["expected"], today)
    errors += check(full, RESOLVED_SCHEMA)
    if not errors:
        errors += semantic_errors(full)
    return errors


def load(path: Path) -> list:
    items = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                items.append((number, json.loads(line)))
            except json.JSONDecodeError as error:
                items.append((number, {"__error__": f"JSON inválido: {error}"}))
    return items


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]) if argv else HERE / "questions.jsonl"
    items = load(path)
    problems = 0
    ids, questions = Counter(), Counter()
    for number, item in items:
        errors = [item["__error__"]] if "__error__" in item else item_errors(item)
        ids[item.get("id")] += 1
        questions[str(item.get("question", "")).strip().lower()] += 1
        for error in errors:
            problems += 1
            print(f"línea {number} ({item.get('id')}): {error}")
    for value, count in ids.items():
        if count > 1:
            problems += 1
            print(f"id repetido: {value}")
    for value, count in questions.items():
        if count > 1:
            problems += 1
            print(f"pregunta repetida: {value}")
    intents = Counter(item.get("expected", {}).get("intent") for _, item in items)
    print(f"\n{len(items)} preguntas, {problems} problemas. Por intent: {dict(intents)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
