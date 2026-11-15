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
                  "veces que he sacado dinero del cajero", "¿Cuánto me cobró Netflix?", "comisiones me han cobrado",
                  "Bizums que le he enviado a Juan", "los Bizums que le envié ayer",
                  "¿Cuántas veces pedí Glovo?", "¿Cuántos pedidos de Glovo hice?"):
            self.assertEqual(r.direction(q), "out", q)

    def test_ingreso(self):
        for q in ("¿Cuánto me han ingresado?", "mis ingresos totales", "he recibido dinero", "mi nómina"):
            self.assertEqual(r.direction(q), "in", q)

    def test_ingreso_por_envio_de_otro(self):
        for q in ("¿Cuánto me ha enviado María por Bizum?", "lo que me enviaron ayer"):
            self.assertEqual(r.direction(q), "in", q)

    def test_recibos_es_un_nombre_no_un_ingreso(self):
        self.assertIsNone(r.direction("¿Qué recibos domiciliados tengo activos?"))
        self.assertEqual(r.direction("¿Cuánto he recibido este mes?"), "in")

    def test_sin_pista_o_contradictoria(self):
        for q in ("¿Cuál fue mi último Bizum?", "Movimientos de la semana pasada", "gasté lo que ingresé", "movimientos con envío incluido"):
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

    def test_trimestre_pasado(self):
        self.assertEqual(r.periods("compara este trimestre con el trimestre pasado"), ["este_trimestre", "trimestre_pasado"])

    def test_no_modifica_la_entrada(self):
        q = {"intent": "aggregate", "metric": "sum", "period": "hoy"}
        r.apply("este mes", q)
        self.assertEqual(q, {"intent": "aggregate", "metric": "sum", "period": "hoy"})


class RouteTest(unittest.TestCase):
    def test_seguimiento_sin_contexto(self):
        for q in ("¿Y el mes anterior?", "¿Y cuánto fue en efectivo?", "y el año pasado"):
            self.assertEqual(r.forced_unknown(q), {"intent": "unknown", "reason": "ambigua"}, q)

    def test_ordenes_de_operar(self):
        for q in ("Transfiere 50 euros a Juan", "Cancela mi suscripción", "Bloquea mi tarjeta"):
            self.assertEqual(r.forced_unknown(q), {"intent": "unknown", "reason": "operacion"}, q)

    def test_saludos(self):
        for q in ("¿Qué tal?", "Hola", "gracias"):
            self.assertEqual(r.forced_unknown(q), {"intent": "unknown", "reason": "fuera_de_alcance"}, q)

    def test_preguntas_normales_no_son_unknown(self):
        for q in ("¿Cuánto gasté este mes?", "Enséñame mis compras", "¿Cuánto me han ingresado?", "Dime el gasto por categorías"):
            self.assertIsNone(r.forced_unknown(q), q)

    def test_desglose_por_categoria_y_mes(self):
        got = r.route("Dime el gasto por categorías de los últimos 30 días", {"intent": "list_transactions"})
        self.assertEqual((got["intent"], got["group_by"], got["metric"]), ("aggregate", "category", "sum"))
        got = r.route("Gasto mensual en supermercado", {"intent": "aggregate", "metric": "sum"})
        self.assertEqual(got["group_by"], "month")
        got = r.route("Desglosa mis ingresos por concepto", {"intent": "list_transactions"})
        self.assertEqual(got["group_by"], "merchant")

    def test_el_mes_con_mas_gastos(self):
        got = r.route("¿Cuál es el mes con más gastos?", {"intent": "compare_periods", "metric": "sum", "compare_period": "hoy"})
        self.assertEqual((got["intent"], got["group_by"]), ("aggregate", "month"))
        self.assertNotIn("compare_period", got)

    def test_una_comparacion_real_no_se_desglosa(self):
        q = {"intent": "compare_periods", "metric": "sum"}
        self.assertEqual(r.route("Compara mi gasto mensual de este año con el año pasado", q)["intent"], "compare_periods")

    def test_conteo_en_el_desglose(self):
        got = r.route("¿Cuántos pagos por mes?", {"intent": "list_transactions"})
        self.assertEqual(got["metric"], "count")

    def test_la_ultima_vez(self):
        got = r.route("¿Cuándo fue la última vez que fui al Burger King?", {"intent": "list_transactions", "text": "Burger King", "sort": "date_desc"})
        self.assertEqual(got["intent"], "last_transaction")
        self.assertEqual(got["text"], "Burger King")
        self.assertNotIn("sort", got)

    def test_el_ultimo_x(self):
        self.assertEqual(r.route("¿Cuál fue mi último pago?", {"intent": "aggregate", "metric": "sum"})["intent"], "last_transaction")

    def test_ultimo_periodo_no_es_ultimo_movimiento(self):
        for q in ("gasto del último mes", "gasto del último año", "la última semana", "los últimos 5 pagos"):
            self.assertNotEqual(r.route(q, {"intent": "aggregate", "metric": "sum"})["intent"], "last_transaction", q)

    def test_no_resucita_un_unknown_del_modelo(self):
        q = {"intent": "unknown", "reason": "ambigua"}
        self.assertEqual(r.route("¿Cuándo fue la última vez que fui al Burger King?", q), q)

    def test_apply_integra_la_ruta(self):
        got = r.apply("¿Y el mes anterior?", {"intent": "top_merchants", "direction": "in", "period": "mes_pasado"})
        self.assertEqual(got, {"intent": "unknown", "reason": "ambigua"})


class NewRulesTest(unittest.TestCase):
    def test_lo_que_no_se_sabe_hacer_es_unknown(self):
        for q in ("¿Cuánto he gastado de media al día este mes?", "¿Tengo algún cargo duplicado?", "¿Gasto más los fines de semana?",
                  "¿Me ha subido alguna suscripción?", "¿Dónde puedo ahorrar?", "¿Hay algún patrón de gasto?"):
            self.assertEqual(r.forced_unknown(q), {"intent": "unknown", "reason": "fuera_de_alcance"}, q)

    def test_mayor_ingreso_es_un_listado_ordenado(self):
        got = r.apply("¿Cuál ha sido mi mayor ingreso en los últimos 30 días?", {"intent": "top_merchants", "direction": "in"})
        self.assertEqual((got["intent"], got["sort"], got["limit"]), ("list_transactions", "amount_desc", 1))

    def test_los_n_sitios(self):
        got = r.apply("Dime los 3 sitios donde más gasté en octubre", {"intent": "top_merchants"})
        self.assertEqual((got["limit"], got.get("sort")), (3, None))

    def test_importes_piden_listado(self):
        got = r.apply("Gastos en farmacia de entre 10 y 40 euros", {"intent": "aggregate", "metric": "sum"})
        self.assertEqual((got["intent"], got["min_amount"], got["max_amount"]), ("list_transactions", 10, 40))
        self.assertNotIn("metric", got)

    def test_cuanto_con_importe_sigue_siendo_total(self):
        got = r.apply("¿Cuánto gasté en compras de más de 50 euros?", {"intent": "aggregate", "metric": "sum"})
        self.assertEqual(got["intent"], "aggregate")

    def test_texto_por_concepto(self):
        self.assertEqual(r.guess_text("¿Cuándo fue la última vez que cobré la nómina?"), "nomina")
        self.assertEqual(r.guess_text("dinero recibido por devoluciones este trimestre"), "devoluciones")

    def test_texto_por_nombre_propio(self):
        self.assertEqual(r.guess_text("¿Cuántas veces pedí Glovo el mes pasado?"), "Glovo")
        self.assertEqual(r.guess_text("¿Cuánto me ha enviado María por Bizum?"), "María")

    def test_no_adivina_donde_no_hay_nombre(self):
        for q in ("¿Cuánto gasté en octubre?", "Dime mis compras de este mes", "¿Cuánto me han ingresado por Bizum?", "Enséñame los ingresos del lunes"):
            self.assertIsNone(r.guess_text(q), q)

    def test_apply_completa_el_texto_solo_si_falta(self):
        got = r.apply("¿Cuántas veces pedí Glovo el mes pasado?", {"intent": "aggregate", "metric": "count"})
        self.assertEqual(got["text"], "Glovo")
        got = r.apply("¿Cuántas veces pedí Glovo el mes pasado?", {"intent": "aggregate", "metric": "count", "text": "Uber"})
        self.assertEqual(got["text"], "Uber")

    def test_desglose_resucita_un_unknown_del_modelo(self):
        got = r.apply("Evolución mensual de mis gastos este año", {"intent": "unknown", "reason": "ambigua"})
        self.assertEqual((got["intent"], got["group_by"]), ("aggregate", "month"))


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
            if got["intent"] != expected["intent"]:
                failures.append(f"{item['id']} intent: esperado {expected['intent']!r}, reglas {got['intent']!r}")
            for key in ("direction", "period", "compare_period", "min_amount", "max_amount", "sort", "limit", "text", "category"):
                default = "out" if key == "direction" and expected["intent"] in ("top_merchants", "recurring") else None
                want = expected.get(key, default)
                if got.get(key, default) != want:
                    failures.append(f"{item['id']} {key}: esperado {want!r}, reglas {got.get(key)!r}")
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()


class NoFalsePositivesTest(unittest.TestCase):
    def test_forced_unknown_solo_en_preguntas_unknown(self):
        for _, item in v.load(Path(v.HERE) / "questions.jsonl"):
            forced = r.forced_unknown(item["question"])
            if forced is not None:
                self.assertEqual(item["expected"]["intent"], "unknown", item["id"])
                self.assertEqual(forced["reason"], item["expected"]["reason"], item["id"])
