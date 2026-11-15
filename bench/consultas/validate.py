"""Validador del dataset pregunta -> consulta (M6). Solo biblioteca estándar.

Uso:  python validate.py [questions.jsonl]

Comprueba, para cada línea del dataset:
  1. que `expected` cumple schema.json,
  2. reglas semánticas por intent (ver convenciones.md),
  3. que las fechas coinciden con las que resulta de aplicar las convenciones
     a la etiqueta `tags` (así se detectan errores de aritmética de fechas).
"""

import calendar
import json
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
SCHEMA = json.loads((HERE / "schema.json").read_text(encoding="utf-8"))

TYPES = {
    "string": str, "number": (int, float), "integer": int,
    "null": type(None), "object": dict, "array": list, "boolean": bool,
}


def type_ok(value, name):
    if name in ("number", "integer") and isinstance(value, bool):
        return False
    return isinstance(value, TYPES[name])


def check(value, schema, path="$"):
    """Subconjunto de JSON Schema: type, enum, pattern, minimum, required, properties, additionalProperties."""
    errors = []
    declared = schema.get("type")
    if declared is not None:
        names = [declared] if isinstance(declared, str) else declared
        if not any(type_ok(value, n) for n in names):
            return [f"{path}: tipo {type(value).__name__} no permitido {names}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} no está en {schema['enum']}")
    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        errors.append(f"{path}: {value!r} no cumple {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{path}: {value} < mínimo {schema['minimum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        errors += [f"{path}: falta {key}" for key in schema.get("required", []) if key not in value]
        if schema.get("additionalProperties") is False:
            errors += [f"{path}: campo no permitido {key}" for key in value if key not in props]
        for key, item in value.items():
            if key in props:
                errors += check(item, props[key], f"{path}.{key}")
    return errors


# ---- convenciones de fechas -------------------------------------------------

def month_bounds(year: int, month: int):
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def resolve_tag(tag: str, today: date):
    """Devuelve (from, to) según convenciones.md."""
    name, _, arg = tag.partition(":")
    if name == "hoy":
        return today, today
    if name == "ayer":
        return today - timedelta(days=1), today - timedelta(days=1)
    if name == "este_mes":
        return today.replace(day=1), today
    if name == "mes_pasado":
        first = today.replace(day=1) - timedelta(days=1)
        return month_bounds(first.year, first.month)
    if name == "esta_semana":
        return today - timedelta(days=today.weekday()), today
    if name == "semana_pasada":
        monday = today - timedelta(days=today.weekday() + 7)
        return monday, monday + timedelta(days=6)
    if name == "este_ano":
        return date(today.year, 1, 1), today
    if name == "ano_pasado":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if name == "ultimos_dias":
        return today - timedelta(days=int(arg) - 1), today
    if name == "mes_nombre":
        month = int(arg)
        year = today.year if month <= today.month else today.year - 1
        return month_bounds(year, month)
    raise ValueError(f"etiqueta desconocida: {tag}")


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
    schema_errs = check(item["expected"], SCHEMA)
    errors += schema_errs
    if schema_errs:
        return errors
    expected = item["expected"]
    errors += semantic_errors(expected)

    tags = item.get("tags", {})
    for key, (a, b) in {"period": ("from", "to"), "compare": ("compare_from", "compare_to")}.items():
        if key in tags:
            want = tuple(d.isoformat() for d in resolve_tag(tags[key], today))
            got = (expected[a], expected[b])
            if want != got:
                errors.append(f"{a}/{b} esperados {want} según la etiqueta {tags[key]!r}, pero hay {got}")
    for key in tags:
        if key not in ("period", "compare"):
            errors.append(f"etiqueta no permitida: {key}")
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
