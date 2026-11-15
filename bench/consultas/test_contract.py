"""Pruebas del contrato v2: periodos, coherencia, expansión y palabras clave."""

import unittest
from datetime import date

import contract as c

TODAY = date(2026, 11, 15)  # domingo


def iso(tag, today=TODAY):
    return tuple(d.isoformat() for d in c.resolve_period(tag, today))


class PeriodTest(unittest.TestCase):
    def test_hoy_y_ayer(self):
        self.assertEqual(iso("hoy"), ("2026-11-15", "2026-11-15"))
        self.assertEqual(iso("ayer"), ("2026-11-14", "2026-11-14"))
    def test_este_mes(self): self.assertEqual(iso("este_mes"), ("2026-11-01", "2026-11-15"))
    def test_mes_pasado(self): self.assertEqual(iso("mes_pasado"), ("2026-10-01", "2026-10-31"))
    def test_mes_pasado_cruza_de_ano(self): self.assertEqual(iso("mes_pasado", date(2027, 1, 10)), ("2026-12-01", "2026-12-31"))
    def test_esta_semana(self): self.assertEqual(iso("esta_semana"), ("2026-11-09", "2026-11-15"))
    def test_semana_pasada(self): self.assertEqual(iso("semana_pasada"), ("2026-11-02", "2026-11-08"))
    def test_este_trimestre(self): self.assertEqual(iso("este_trimestre"), ("2026-10-01", "2026-11-15"))
    def test_trimestre_ultimo_dia(self): self.assertEqual(iso("este_trimestre", date(2026, 3, 31)), ("2026-01-01", "2026-03-31"))
    def test_este_ano(self): self.assertEqual(iso("este_ano"), ("2026-01-01", "2026-11-15"))
    def test_ano_pasado(self): self.assertEqual(iso("ano_pasado"), ("2025-01-01", "2025-12-31"))
    def test_ultimos_30_dias(self): self.assertEqual(iso("ultimos_30_dias"), ("2026-10-17", "2026-11-15"))
    def test_ultimos_7_dias(self): self.assertEqual(iso("ultimos_7_dias"), ("2026-11-09", "2026-11-15"))
    def test_ultimos_365_dias(self): self.assertEqual(iso("ultimos_365_dias"), ("2025-11-16", "2026-11-15"))
    def test_mes_por_nombre_pasado(self): self.assertEqual(iso("octubre"), ("2026-10-01", "2026-10-31"))
    def test_mes_por_nombre_mes_actual(self): self.assertEqual(iso("noviembre"), ("2026-11-01", "2026-11-30"))
    def test_mes_por_nombre_futuro_es_del_ano_anterior(self): self.assertEqual(iso("diciembre"), ("2025-12-01", "2025-12-31"))
    def test_febrero_bisiesto(self): self.assertEqual(iso("febrero", date(2028, 3, 1)), ("2028-02-01", "2028-02-29"))

    def test_desconocido(self):
        for tag in ("quincena", "ultimos_10_dias", "ultimos_dias:30", ""):
            with self.assertRaises(ValueError, msg=tag):
                c.resolve_period(tag, TODAY)

    def test_todas_las_etiquetas_del_esquema_resuelven(self):
        import json
        from pathlib import Path
        schema = json.loads((Path(c.__file__).parent / "schema.json").read_text(encoding="utf-8"))
        for field in ("period", "compare_period"):
            self.assertEqual(sorted(schema["properties"][field]["enum"]), sorted(c.PERIODS))
        for tag in c.PERIODS:
            c.resolve_period(tag, TODAY)


class ModelErrorsTest(unittest.TestCase):
    def test_validas(self):
        for q in (
            {"intent": "balance"},
            {"intent": "aggregate", "metric": "sum"},
            {"intent": "compare_periods", "metric": "sum", "period": "este_mes", "compare_period": "mes_pasado"},
            {"intent": "top_merchants", "group_by": "merchant"},
            {"intent": "top_merchants"},
            {"intent": "unknown", "reason": "ambigua"},
            {"intent": "list_transactions", "min_amount": 1, "max_amount": 5},
        ):
            self.assertEqual(c.model_errors(q), [], q)

    def test_invalidas(self):
        for q in (
            {"intent": "aggregate"},                                   # sin metric
            {"intent": "list_transactions", "metric": "sum"},          # metric de más
            {"intent": "compare_periods", "metric": "sum", "period": "este_mes"},
            {"intent": "aggregate", "metric": "sum", "compare_period": "mes_pasado"},
            {"intent": "top_merchants", "group_by": "category"},
            {"intent": "list_transactions", "group_by": "month"},
            {"intent": "unknown"},                                     # sin reason
            {"intent": "unknown", "reason": "ambigua", "category": "bizum"},
            {"intent": "balance", "category": "bizum"},
            {"intent": "aggregate", "metric": "sum", "reason": "ambigua"},
            {"intent": "list_transactions", "min_amount": 9, "max_amount": 5},
        ):
            self.assertTrue(c.model_errors(q), q)


class ExpandTest(unittest.TestCase):
    def test_siempre_15_campos(self):
        self.assertEqual(list(c.expand({"intent": "balance"}, TODAY)), c.RESOLVED_KEYS)

    def test_valores_por_defecto(self):
        full = c.expand({"intent": "balance"}, TODAY)
        self.assertEqual(full["direction"], "any")
        self.assertTrue(all(full[k] is None for k in c.RESOLVED_KEYS if k not in ("intent", "direction")))

    def test_periodo_y_comparacion(self):
        full = c.expand(
            {"intent": "compare_periods", "metric": "sum", "direction": "out", "period": "este_mes", "compare_period": "mes_pasado"},
            TODAY,
        )
        self.assertEqual((full["from"], full["to"]), ("2026-11-01", "2026-11-15"))
        self.assertEqual((full["compare_from"], full["compare_to"]), ("2026-10-01", "2026-10-31"))

    def test_top_merchants_pone_group_by(self):
        self.assertEqual(c.expand({"intent": "top_merchants"}, TODAY)["group_by"], "merchant")

    def test_compare_periods_sin_metric_es_sum(self):
        q = {"intent": "compare_periods", "period": "este_mes", "compare_period": "mes_pasado"}
        self.assertEqual(c.model_errors(q), [])
        self.assertEqual(c.expand(q, TODAY)["metric"], "sum")

    def test_compare_periods_respeta_count(self):
        q = {"intent": "compare_periods", "metric": "count", "period": "este_mes", "compare_period": "mes_pasado"}
        self.assertEqual(c.expand(q, TODAY)["metric"], "count")

    def test_aggregate_sigue_exigiendo_metric(self):
        self.assertTrue(c.model_errors({"intent": "aggregate"}))

    def test_unknown_usa_mensaje_fijo(self):
        for reason, text in c.MESSAGES.items():
            self.assertEqual(c.expand({"intent": "unknown", "reason": reason}, TODAY)["clarification"], text)

    def test_no_modifica_la_entrada(self):
        q = {"intent": "aggregate", "metric": "sum", "period": "este_mes"}
        c.expand(q, TODAY)
        self.assertEqual(q, {"intent": "aggregate", "metric": "sum", "period": "este_mes"})


class SanitizeTest(unittest.TestCase):
    def test_quita_reason_fuera_de_unknown(self):
        self.assertEqual(c.sanitize({"intent": "list_transactions", "reason": "ambigua", "limit": 5}),
                         {"intent": "list_transactions", "limit": 5})

    def test_quita_compare_period_si_no_compara(self):
        q = {"intent": "aggregate", "metric": "sum", "period": "este_mes", "compare_period": "mes_pasado"}
        self.assertEqual(c.sanitize(q), {"intent": "aggregate", "metric": "sum", "period": "este_mes"})

    def test_conserva_compare_period_si_compara(self):
        q = {"intent": "compare_periods", "period": "este_mes", "compare_period": "mes_pasado"}
        self.assertEqual(c.sanitize(q), q)

    def test_quita_metric_y_group_by_sobrantes(self):
        self.assertEqual(c.sanitize({"intent": "list_transactions", "metric": "sum", "group_by": "month"}),
                         {"intent": "list_transactions"})

    def test_unknown_y_balance_se_quedan_minimos(self):
        self.assertEqual(c.sanitize({"intent": "unknown", "reason": "ambigua", "period": "hoy", "category": "bizum"}),
                         {"intent": "unknown", "reason": "ambigua"})
        self.assertEqual(c.sanitize({"intent": "balance", "category": "bizum"}), {"intent": "balance"})

    def test_no_inventa_ni_arregla_obligatorios(self):
        self.assertTrue(c.model_errors(c.sanitize({"intent": "aggregate", "period": "hoy"})))  # sigue sin metric
        self.assertTrue(c.model_errors(c.sanitize({"intent": "unknown", "period": "hoy"})))    # sigue sin reason

    def test_no_modifica_la_entrada(self):
        q = {"intent": "balance", "category": "bizum"}
        c.sanitize(q)
        self.assertEqual(q, {"intent": "balance", "category": "bizum"})

    def test_lo_sanitizado_nunca_incumple_las_reglas_de_sobrantes(self):
        for intent in ("aggregate", "list_transactions", "last_transaction", "balance", "compare_periods",
                       "top_merchants", "recurring", "unknown"):
            full = {"intent": intent, "metric": "sum", "group_by": "merchant", "direction": "out", "category": "bizum",
                    "period": "hoy", "compare_period": "ayer", "reason": "ambigua"}
            errors = c.model_errors(c.sanitize(full))
            self.assertFalse([e for e in errors if "admite" in e or "solo se rellena" in e or "sobran" in e], (intent, errors))


class DefaultsTest(unittest.TestCase):
    def test_direccion_por_defecto_en_ranking_y_recurrentes(self):
        self.assertEqual(c.expand({"intent": "top_merchants"}, TODAY)["direction"], "out")
        self.assertEqual(c.expand({"intent": "recurring"}, TODAY)["direction"], "out")

    def test_respeta_la_direccion_del_modelo(self):
        self.assertEqual(c.expand({"intent": "top_merchants", "direction": "in"}, TODAY)["direction"], "in")

    def test_otros_intents_siguen_en_any(self):
        self.assertEqual(c.expand({"intent": "last_transaction"}, TODAY)["direction"], "any")


class RefineTest(unittest.TestCase):
    def test_bizum(self):
        self.assertEqual(c.refine("¿Cuál fue mi último Bizum?", {"intent": "last_transaction"})["category"], "bizum")

    def test_cajero(self):
        self.assertEqual(c.refine("veces que saqué del cajero", {"intent": "aggregate", "metric": "count"})["category"], "efectivo")

    def test_comisiones(self):
        self.assertEqual(c.refine("¿Cuántas comisiones?", {"intent": "aggregate", "metric": "count"})["category"], "comisiones")

    def test_manda_sobre_la_categoria_del_modelo(self):
        self.assertEqual(c.refine("gasto en bizum", {"intent": "aggregate", "category": "otros"})["category"], "bizum")

    def test_si_encajan_dos_categorias_no_decide(self):
        q = {"intent": "aggregate", "category": "otros"}
        self.assertEqual(c.refine("bizum de la cena", q)["category"], "otros")

    def test_restaurantes_supermercado_ocio_gasolina(self):
        for question, category in (("¿Cuánto gasto en cenas?", "restaurantes"), ("gasto en el supermercado", "supermercado"),
                                   ("¿Cuánto gasto en ocio?", "ocio_suscripciones"), ("gasolina de agosto", "transporte")):
            self.assertEqual(c.refine(question, {"intent": "aggregate"})["category"], category, question)

    def test_no_actua_en_unknown_ni_balance(self):
        for intent in ("unknown", "balance", "compare_periods"):
            self.assertNotIn("category", c.refine("bizum", {"intent": intent}))

    def test_sin_palabra_clave_no_cambia(self):
        q = {"intent": "aggregate", "metric": "sum"}
        self.assertEqual(c.refine("cuánto gasté", q), q)

    def test_no_confunde_palabras_parecidas(self):
        self.assertNotIn("category", c.refine("comisionista", {"intent": "aggregate", "metric": "sum"}))

    def test_no_modifica_la_entrada(self):
        q = {"intent": "aggregate", "metric": "sum"}
        c.refine("bizum", q)
        self.assertNotIn("category", q)


if __name__ == "__main__":
    unittest.main()
