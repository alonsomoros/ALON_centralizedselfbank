import unittest
from pathlib import Path

import rules as r
import validate as v


class NormalizeTest(unittest.TestCase):
    def test_tildes_y_enie(self):
        self.assertEqual(r.normalize("¿Cuánto gasté este AÑO?"), "¿cuanto gaste este ano?")

    def test_numeros(self):
        for raw, want in (("50", 50), ("12,50", 12.5), ("1.200", 1200), ("1.200,50", 1200.5), ("tres", 3), ("7", 7)):
            self.assertEqual(r.parse_number(raw), want, raw)


class PeriodsTest(unittest.TestCase):
    def check(self, question, want):
        self.assertEqual(r.periods(question), want, question)

    def test_basicos(self):
        self.check("¿Cuánto gasté hoy?", ["hoy"])
        self.check("¿Y ayer?", ["ayer"])
        self.check("Llevo gastado este mes", ["este_mes"])
        self.check("el mes pasado", ["mes_pasado"])
        self.check("el mes anterior", ["mes_pasado"])
        self.check("esta semana", ["esta_semana"])
        self.check("la semana pasada", ["semana_pasada"])
        self.check("este trimestre", ["este_trimestre"])
        self.check("este año", ["este_ano"])
        self.check("de lo que llevo de año", ["este_ano"])
        self.check("el año pasado", ["ano_pasado"])

    def test_ultimos(self):
        self.check("en los últimos 30 días", ["ultimos_30_dias"])
        self.check("los últimos 7 días", ["ultimos_7_dias"])
        self.check("los últimos 15 días", ["ultimos_15_dias"])
        self.check("el último mes", ["ultimos_30_dias"])
        self.check("la última semana", ["ultimos_7_dias"])
        self.check("los últimos 12 meses", ["ultimos_365_dias"])
        self.check("en el último año", ["ultimos_365_dias"])

    def test_meses(self):
        self.check("¿Cuánto gasté en octubre?", ["octubre"])
        self.check("gastos de más de 50 euros en septiembre", ["septiembre"])

    def test_comparacion_en_orden(self):
        self.check("¿Gasté más este mes que el mes pasado?", ["este_mes", "mes_pasado"])
        self.check("¿Gasté más en octubre que en septiembre?", ["octubre", "septiembre"])
        self.check("¿Gasté más ayer que hoy?", ["ayer", "hoy"])

    def test_sin_periodo(self):
        self.check("¿Cuánto gasté en Mercadona?", [])
        self.check("¿Cuántos últimos 10 días?", [])  # no soportado
        self.check("Mis últimas 5 compras", [])      # 'últimas 5' no es un periodo

    def test_no_confunde_palabras(self):
        self.check("el mayor gasto", [])
        self.check("un mayorista", [])


class DirectionTest(unittest.TestCase):
    def test_gasto(self):
        for q in ("¿Cuánto gasté?", "Mis compras", "Pagos de menos de 10 euros", "¿Qué suscripciones pago?",
                  "veces que he sacado dinero del cajero", "¿Cuánto me cobró Netflix?", "comisiones me han cobrado"):
            self.assertEqual(r.direction(q), "out", q)

    def test_ingreso(self):
        for q in ("¿Cuánto me han ingresado?", "mis ingresos totales", "he recibido dinero", "mi nómina"):
            self.assertEqual(r.direction(q), "in", q)

    def test_sin_pista_o_contradictoria(self):
        for q in ("¿Cuál fue mi último Bizum?", "Movimientos de la semana pasada", "gasté lo que ingresé"):
            self.assertIsNone(r.direction(q), q)


class AmountsTest(unittest.TestCase):
    def test_minimo(self):
        self.assertEqual(r.amounts("Gastos de más de 50 euros"), {"min_amount": 50})
        self.assertEqual(r.amounts("superiores a 1.200 €"), {"min_amount": 1200})

    def test_maximo(self):
        self.assertEqual(r.amounts("Pagos de menos de 10 euros"), {"max_amount": 10})
        self.assertEqual(r.amounts("hasta 12,50 euros"), {"max_amount": 12.5})

    def test_entre(self):
        self.assertEqual(r.amounts("de entre 20 y 100 euros"), {"min_amount": 20, "max_amount": 100})

    def test_sin_importe(self):
        self.assertEqual(r.amounts("más de 3 veces"), {})
        self.assertEqual(r.amounts("¿Dónde gasto más dinero?"), {})


class OrderingTest(unittest.TestCase):
    def test_ultimas_n(self):
        self.assertEqual(r.ordering("Dime mis últimas 5 compras", "list_transactions", None), {"sort": "date_desc", "limit": 5})
        self.assertEqual(r.ordering("los últimos tres pagos", "list_transactions", None), {"sort": "date_desc", "limit": 3})

    def test_ultimos_dias_no_es_limite(self):
        self.assertEqual(r.ordering("en los últimos 30 días", "aggregate", None), {})

    def test_mayores(self):
        self.assertEqual(r.ordering("mis tres mayores gastos", "list_transactions", None), {"sort": "amount_desc", "limit": 3})
        self.assertEqual(r.ordering("¿Cuál fue mi mayor gasto?", "list_transactions", None), {"sort": "amount_desc", "limit": 1})

    def test_mas_barato(self):
        self.assertEqual(r.ordering("el más barato", "list_transactions", None), {"sort": "amount_asc", "limit": 1})

    def test_cual_es_el_mas(self):
        self.assertEqual(r.ordering("¿Cuál es el mes con más gastos?", "aggregate", "month"), {"sort": "amount_desc", "limit": 1})
        self.assertEqual(r.ordering("¿Cuál es el sitio en el que más he gastado?", "top_merchants", "merchant"), {"limit": 1})

    def test_nada(self):
        self.assertEqual(r.ordering("¿Dónde gasto más dinero?", "top_merchants", "merchant"), {})


class CleanTextTest(unittest.TestCase):
    def test_texto_numerico_se_descarta(self):
        self.assertNotIn("text", r.clean_text({"intent": "list_transactions", "text": ">50"}))

    def test_texto_que_es_categoria(self):
        q = r.clean_text({"intent": "aggregate", "text": "Restaurantes"})
        self.assertEqual((q.get("text"), q.get("category")), (None, "restaurantes"))

    def test_no_pisa_categoria_existente(self):
        q = r.clean_text({"intent": "aggregate", "text": "ocio", "category": "viajes"})
        self.assertEqual((q.get("text"), q.get("category")), (None, "viajes"))

    def test_comercio_se_respeta(self):
        self.assertEqual(r.clean_text({"intent": "aggregate", "text": "Mercadona"})["text"], "Mercadona")


class ApplyTest(unittest.TestCase):
    def test_descarta_lo_inventado(self):
        q = {"intent": "aggregate", "metric": "sum", "direction": "in", "text": "Mercadona", "period": "este_ano", "sort": "date_desc"}
        got = r.apply("cuanto gaste en mercadona", q)
        self.assertEqual(got, {"intent": "aggregate", "metric": "sum", "direction": "out", "text": "Mercadona"})

    def test_sobrescribe_el_periodo_mal_leido(self):
        q = {"intent": "aggregate", "metric": "sum", "direction": "in", "period": "mes_pasado"}
        self.assertEqual(r.apply("¿Cuánto me han ingresado este mes?", q)["period"], "este_mes")

    def test_comparacion_toma_los_dos_periodos(self):
        q = {"intent": "compare_periods", "metric": "sum", "period": "mes_pasado", "compare_period": "hoy"}
        got = r.apply("¿Gasté más este mes que el mes pasado?", q)
        self.assertEqual((got["period"], got["compare_period"]), ("este_mes", "mes_pasado"))

    def test_comparacion_con_un_solo_periodo_queda_incompleta(self):
        got = r.apply("¿Gasté más?", {"intent": "compare_periods", "metric": "sum", "period": "hoy", "compare_period": "ayer"})
        self.assertNotIn("period", got)  # el validador lo rechazará: pedirá reformular

    def test_importes(self):
        got = r.apply("Gastos de más de 50 euros en septiembre", {"intent": "list_transactions", "text": ">50"})
        self.assertEqual((got.get("min_amount"), got.get("text"), got["period"]), (50, None, "septiembre"))

    def test_agregado_sin_desglose_ignora_orden(self):
        got = r.apply("¿Cuántas veces he sacado dinero?", {"intent": "aggregate", "metric": "count", "sort": "amount_desc", "limit": 3})
        self.assertNotIn("sort", got)
        self.assertNotIn("limit", got)

    def test_unknown_y_balance_no_se_tocan(self):
        for q in ({"intent": "unknown", "reason": "ambigua"}, {"intent": "balance"}):
            self.assertEqual(r.apply("¿Cuánto gasté hoy?", q), q)

    def test_no_modifica_la_entrada(self):
        q = {"intent": "aggregate", "metric": "sum", "period": "hoy"}
        r.apply("este mes", q)
        self.assertEqual(q, {"intent": "aggregate", "metric": "sum", "period": "hoy"})


class DatasetTest(unittest.TestCase):
    """Con un modelo 'perfecto' en intent/metric/category/text, las reglas deben completar el resto."""

    OWNED = ("intent", "metric", "group_by", "category", "text", "reason")

    def test_reglas_reconstruyen_los_campos_estructurales_del_dataset(self):
        failures = []
        for _, item in v.load(Path(v.HERE) / "questions.jsonl"):
            expected = item["expected"]
            if expected["intent"] in ("unknown", "balance"):
                continue
            from_model = {k: x for k, x in expected.items() if k in self.OWNED}
            got = r.apply(item["question"], from_model)
            for key in ("direction", "period", "compare_period", "min_amount", "max_amount", "sort", "limit"):
                default = "out" if key == "direction" and expected["intent"] in ("top_merchants", "recurring") else None
                want = expected.get(key, default)
                if got.get(key, default) != want:
                    failures.append(f"{item['id']} {key}: esperado {want!r}, reglas {got.get(key)!r}")
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
