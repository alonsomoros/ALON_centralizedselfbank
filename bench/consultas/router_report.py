"""Cuánto de las preguntas del dataset resuelve la ruta rápida (sin modelo) y con qué acierto.

Uso:  python router_report.py
Ojo: las reglas se afinaron mirando estas preguntas; es una cota optimista, no una garantía.
"""

import sys
from datetime import date

import contract
import router
import rules
import validate as v


def final_query(question: str, fast: dict, today: date):
    cleaned = contract.sanitize(rules.apply(question, contract.sanitize(fast)))
    if contract.model_errors(cleaned):
        return None
    return contract.expand(contract.refine(question, cleaned), today)


def comparable(query: dict) -> dict:
    query = dict(query)
    query.pop("clarification", None)
    if isinstance(query.get("text"), str):
        query["text"] = rules.normalize(query["text"]).strip()
    return query


def evaluate(items: list) -> dict:
    covered, correct, wrong, uncovered = [], [], [], []
    for item in items:
        today = date.fromisoformat(item["today"])
        fast = router.fast_intent(item["question"])
        if fast is None:
            uncovered.append(item)
            continue
        covered.append(item)
        final = final_query(item["question"], fast, today)
        want = contract.expand(item["expected"], today)
        if final is not None and comparable(final) == comparable(want):
            correct.append(item)
        else:
            wrong.append((item, final))
    return {"total": len(items), "covered": covered, "correct": correct, "wrong": wrong, "uncovered": uncovered}


def main() -> int:
    items = [item for _, item in v.load(v.HERE / "questions.jsonl")]
    r = evaluate(items)
    n = r["total"]
    print(f"Preguntas: {n}")
    print(f"  Resueltas por reglas, sin modelo: {len(r['covered'])} ({100 * len(r['covered']) / n:.0f} %)")
    if r["covered"]:
        print(f"  Acierto de esas: {len(r['correct'])}/{len(r['covered'])} ({100 * len(r['correct']) / len(r['covered']):.0f} %)")
    print(f"  Pasan al modelo: {len(r['uncovered'])}")
    for item, final in r["wrong"]:
        print(f"  FALLO {item['id']}: {item['question']}  -> {final and {k: x for k, x in final.items() if x is not None}}")
    print("\nPasan al modelo (palabras que el router no conoce):")
    for item in r["uncovered"]:
        print(f"  {item['id']}: {item['question']}  {router.unresolved(item['question']) or '(sin intent claro)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
