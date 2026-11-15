"""Pruebas del validador y del dataset de preguntas."""

import copy
import json
import unittest
from datetime import date
from pathlib import Path

import validate as v

TODAY = date(2026, 11, 15)  # domingo
BASE = {
    "intent": "balance", "metric": None, "group_by": None, "direction": "any", "category": None,
    "text": None, "from": None, "to": None, "compare_from": None, "compare_to": None,
    "min_amount": None, "max_amount": None, "sort": None, "limit": None, "clarification": None,
}


def query(**changes):
    result = copy.deepcopy(BASE)
    result.update(changes)
    return result


class ResolveTagTest(unittest.TestCase):
    def check(self, tag, start, end):
        self.assertEqual(tuple(d.isoformat() for d in v.resolve_tag(tag, TODAY)), (start, end))

    def test_este_mes(self): self.check("este_mes", "2026-11-01", "2026-11-15")
    def test_mes_pasado(self): self.check("mes_pasado", "2026-10-01", "2026-10-31")
    def test_esta_semana(self): self.check("esta_semana", "2026-11-09", "2026-11-15")
    def test_semana_pasada(self): self.check("semana_pasada", "2026-11-02", "2026-11-08")
    def test_este_trimestre(self): self.check("este_trimestre", "2026-10-01", "2026-11-15")
    def test_trimestre_primer_dia(self):
        self.assertEqual(
            tuple(d.isoformat() for d in v.resolve_tag("este_trimestre", date(2026, 3, 31))), ("2026-01-01", "2026-03-31")
        )
    def test_ultimos_365_dias(self): self.check("ultimos_dias:365", "2025-11-16", "2026-11-15")
    def test_este_ano(self): self.check("este_ano", "2026-01-01", "2026-11-15")
    def test_ano_pasado(self): self.check("ano_pasado", "2025-01-01", "2025-12-31")
    def test_ultimos_30_dias(self): self.check("ultimos_dias:30", "2026-10-17", "2026-11-15")
    def test_ultimo_1_dia_es_hoy(self): self.check("ultimos_dias:1", "2026-11-15", "2026-11-15")
    def test_mes_pasado_en_enero_cruza_de_ano(self):
        self.assertEqual(
            tuple(d.isoformat() for d in v.resolve_tag("mes_pasado", date(2027, 1, 10))), ("2026-12-01", "2026-12-31")
        )
    def test_mes_nombre_pasado(self): self.check("mes_nombre:10", "2026-10-01", "2026-10-31")
    def test_mes_nombre_futuro_es_del_ano_anterior(self): self.check("mes_nombre:12", "2025-12-01", "2025-12-31")
    def test_etiqueta_desconocida(self):
        with self.assertRaises(ValueError):
            v.resolve_tag("quincena", TODAY)


class SemanticTest(unittest.TestCase):
    def test_consulta_valida(self):
        self.assertEqual(v.semantic_errors(query()), [])

    def test_aggregate_sin_metric(self):
        self.assertTrue(v.semantic_errors(query(intent="aggregate", direction="out")))

    def test_top_merchants_exige_group_by(self):
        self.assertTrue(v.semantic_errors(query(intent="top_merchants", direction="out")))

    def test_fechas_incompletas(self):
        self.assertTrue(v.semantic_errors(query(intent="list_transactions", **{"from": "2026-01-01"})))

    def test_fechas_invertidas(self):
        self.assertTrue(v.semantic_errors(query(intent="list_transactions", **{"from": "2026-02-01", "to": "2026-01-01"})))

    def test_unknown_necesita_aclaracion(self):
        self.assertTrue(v.semantic_errors(query(intent="unknown")))

    def test_clarification_fuera_de_unknown(self):
        self.assertTrue(v.semantic_errors(query(clarification="hola")))

    def test_compare_exige_ambos_periodos(self):
        self.assertTrue(v.semantic_errors(query(intent="compare_periods", metric="sum", direction="out")))

    def test_balance_con_filtros_se_rechaza(self):
        self.assertTrue(v.semantic_errors(query(category="bizum")))


class SchemaTest(unittest.TestCase):
    def test_campo_extra_se_rechaza(self):
        self.assertTrue(v.check({**BASE, "extra": 1}, v.SCHEMA))

    def test_falta_campo(self):
        broken = dict(BASE)
        del broken["limit"]
        self.assertTrue(v.check(broken, v.SCHEMA))

    def test_categoria_inventada(self):
        self.assertTrue(v.check({**BASE, "category": "gasolina"}, v.SCHEMA))

    def test_fecha_mal_formada(self):
        self.assertTrue(v.check({**BASE, "from": "15/11/2026", "to": "2026-11-15"}, v.SCHEMA))

    def test_limit_booleano_no_es_entero(self):
        self.assertTrue(v.check({**BASE, "limit": True}, v.SCHEMA))

    def test_base_valida(self):
        self.assertEqual(v.check(BASE, v.SCHEMA), [])


class ItemTest(unittest.TestCase):
    def test_etiqueta_incoherente_con_fechas(self):
        item = {
            "id": "x", "today": "2026-11-15", "question": "q", "tags": {"period": "este_mes"},
            "expected": query(intent="list_transactions", **{"from": "2026-11-02", "to": "2026-11-15"}),
        }
        self.assertTrue(any("etiqueta" in e for e in v.item_errors(item)))


class DatasetTest(unittest.TestCase):
    def test_dataset_completo_es_valido(self):
        path = Path(v.HERE) / "questions.jsonl"
        problems = []
        for number, item in v.load(path):
            for error in v.item_errors(item):
                problems.append(f"línea {number}: {error}")
        self.assertEqual(problems, [])

    def test_ids_y_preguntas_unicos(self):
        items = [item for _, item in v.load(Path(v.HERE) / "questions.jsonl")]
        self.assertEqual(len({i["id"] for i in items}), len(items))
        self.assertEqual(len({i["question"].lower() for i in items}), len(items))


if __name__ == "__main__":
    unittest.main()
