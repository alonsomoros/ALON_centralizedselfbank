"""Pruebas del validador y del dataset de preguntas."""

import copy
import unittest
from pathlib import Path

import validate as v

RESOLVED = {
    "intent": "balance", "metric": None, "group_by": None, "direction": "any", "category": None,
    "text": None, "from": None, "to": None, "compare_from": None, "compare_to": None,
    "min_amount": None, "max_amount": None, "sort": None, "limit": None, "clarification": None,
}


def resolved(**changes):
    result = copy.deepcopy(RESOLVED)
    result.update(changes)
    return result


class SemanticTest(unittest.TestCase):
    """Reglas sobre la consulta ya expandida (lo que recibe el ejecutor)."""

    def test_consulta_valida(self):
        self.assertEqual(v.semantic_errors(resolved()), [])

    def test_aggregate_sin_metric(self):
        self.assertTrue(v.semantic_errors(resolved(intent="aggregate", direction="out")))

    def test_top_merchants_exige_group_by(self):
        self.assertTrue(v.semantic_errors(resolved(intent="top_merchants", direction="out")))

    def test_fechas_incompletas(self):
        self.assertTrue(v.semantic_errors(resolved(intent="list_transactions", **{"from": "2026-01-01"})))

    def test_fechas_invertidas(self):
        self.assertTrue(v.semantic_errors(resolved(intent="list_transactions", **{"from": "2026-02-01", "to": "2026-01-01"})))

    def test_unknown_necesita_aclaracion(self):
        self.assertTrue(v.semantic_errors(resolved(intent="unknown")))

    def test_clarification_fuera_de_unknown(self):
        self.assertTrue(v.semantic_errors(resolved(clarification="hola")))

    def test_compare_exige_ambos_periodos(self):
        self.assertTrue(v.semantic_errors(resolved(intent="compare_periods", metric="sum", direction="out")))

    def test_balance_con_filtros_se_rechaza(self):
        self.assertTrue(v.semantic_errors(resolved(category="bizum")))


class SchemaTest(unittest.TestCase):
    def test_modelo_minimo_valido(self):
        self.assertEqual(v.check({"intent": "balance"}, v.SCHEMA), [])

    def test_modelo_sin_intent(self):
        self.assertTrue(v.check({"metric": "sum"}, v.SCHEMA))

    def test_campo_extra_se_rechaza(self):
        self.assertTrue(v.check({"intent": "balance", "extra": 1}, v.SCHEMA))

    def test_null_no_se_admite_en_el_modelo(self):
        self.assertTrue(v.check({"intent": "balance", "category": None}, v.SCHEMA))

    def test_categoria_inventada(self):
        self.assertTrue(v.check({"intent": "aggregate", "category": "gasolina"}, v.SCHEMA))

    def test_periodo_inventado(self):
        self.assertTrue(v.check({"intent": "aggregate", "period": "quincena"}, v.SCHEMA))

    def test_limit_booleano_no_es_entero(self):
        self.assertTrue(v.check({"intent": "list_transactions", "limit": True}, v.SCHEMA))

    def test_resolved_completo_valido(self):
        self.assertEqual(v.check(RESOLVED, v.RESOLVED_SCHEMA), [])

    def test_resolved_fecha_mal_formada(self):
        self.assertTrue(v.check(resolved(**{"from": "15/11/2026", "to": "2026-11-15"}), v.RESOLVED_SCHEMA))

    def test_resolved_falta_campo(self):
        broken = dict(RESOLVED)
        del broken["limit"]
        self.assertTrue(v.check(broken, v.RESOLVED_SCHEMA))


class ItemTest(unittest.TestCase):
    def item(self, expected, today="2026-11-15"):
        return {"id": "x", "today": today, "question": "q", "expected": expected}

    def test_valido(self):
        self.assertEqual(v.item_errors(self.item({"intent": "aggregate", "metric": "sum", "period": "este_mes"})), [])

    def test_incoherente(self):
        self.assertTrue(v.item_errors(self.item({"intent": "aggregate"})))

    def test_fecha_de_hoy_invalida(self):
        self.assertTrue(v.item_errors(self.item({"intent": "balance"}, today="15-11-2026")))


class DatasetTest(unittest.TestCase):
    path = Path(v.HERE) / "questions.jsonl"

    def test_dataset_completo_es_valido(self):
        problems = []
        for number, item in v.load(self.path):
            for error in v.item_errors(item):
                problems.append(f"línea {number}: {error}")
        self.assertEqual(problems, [])

    def test_ids_y_preguntas_unicos(self):
        items = [item for _, item in v.load(self.path)]
        self.assertEqual(len({i["id"] for i in items}), len(items))
        self.assertEqual(len({i["question"].lower() for i in items}), len(items))

    def test_al_menos_40_preguntas(self):
        self.assertGreaterEqual(len(v.load(self.path)), 40)


if __name__ == "__main__":
    unittest.main()
