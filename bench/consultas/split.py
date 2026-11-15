"""Reparto determinista del dataset en desarrollo (ajustar el prompt) y prueba (solo medir).

Se reparte por intent, alternando, para que ambos conjuntos tengan variedad y los `unknown`
estén en los dos. No hay fichero aparte: el reparto sale de las propias preguntas.
"""

from collections import defaultdict

TEST_POSITIONS = {1, 3}  # posiciones 0..4 dentro de cada intent; 2 de cada 5 van a prueba


def assign(items: list) -> dict:
    """items: [{id, expected:{intent,...}, split?}]. Devuelve {id: 'dev'|'test'|'holdout'}.

    Las preguntas con `split` explícito (p. ej. 'holdout') conservan ese reparto y no alteran
    el de las demás.
    """
    explicit = {i["id"]: i["split"] for i in items if "split" in i}
    groups = defaultdict(list)
    for item in sorted((i for i in items if "split" not in i), key=lambda i: i["id"]):
        groups[item["expected"]["intent"]].append(item["id"])
    result = {}
    for ids in groups.values():
        for position, item_id in enumerate(ids):
            result[item_id] = "test" if position % 5 in TEST_POSITIONS else "dev"
    result.update(explicit)
    return result
