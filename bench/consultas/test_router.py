import unittest

import router as rt
import router_report
import validate as v


class FastIntentTest(unittest.TestCase):
    def intent(self, question):
        got = rt.fast_intent(question)
        return got and got["intent"]

    def test_saldo(self):
        self.assertEqual(self.intent("¿Cuánto dinero tengo?"), "balance")
        self.assertEqual(self.intent("¿Cuál es mi saldo?"), "balance")

    def test_saldo_con_periodo_no_se_decide(self):
        self.assertIsNone(rt.fast_intent("¿Cuál era mi saldo el mes pasado?"))

    def test_recurrentes(self):
        for q in ("¿Qué suscripciones pago?", "¿Qué recibos domiciliados tengo activos?", "mis pagos fijos"):
            self.assertEqual(self.intent(q), "recurring", q)

    def test_ultimo_movimiento(self):
        self.assertEqual(self.intent("¿Cuál fue mi último Bizum?"), "last_transaction")

    def test_comparacion(self):
        self.assertEqual(self.intent("¿Gasté más este mes que el mes pasado?"), "compare_periods")
        self.assertEqual(self.intent("Compara mis ingresos de este trimestre con el trimestre pasado"), "compare_periods")

    def test_comparacion_necesita_dos_periodos(self):
        self.assertNotEqual(self.intent("¿Gasté más este mes?"), "compare_periods")

    def test_ranking(self):
        self.assertEqual(self.intent("¿Dónde gasto más dinero?"), "top_merchants")
        self.assertEqual(self.intent("Dime los 3 sitios donde más gasté en octubre"), "top_merchants")

    def test_listados(self):
        for q in ("¿Qué pagos hice ayer?", "Muéstrame mis últimos 10 movimientos", "¿Cuál fue mi mayor gasto del mes pasado?",
                  "Gastos de más de 50 euros en septiembre"):
            self.assertEqual(self.intent(q), "list_transactions", q)

    def test_agregados(self):
        self.assertEqual(rt.fast_intent("¿Cuánto gasté en transporte la semana pasada?"), {"intent": "aggregate", "metric": "sum"})
        self.assertEqual(rt.fast_intent("¿Cuántas veces he sacado dinero del cajero este año?"), {"intent": "aggregate", "metric": "count"})

    def test_desglose(self):
        self.assertEqual(self.intent("Dime el gasto por categorías de los últimos 30 días"), "aggregate")

    def test_unknown_forzado_pasa_por_la_ruta_rapida(self):
        self.assertEqual(rt.fast_intent("¿Y el mes anterior?"), {"intent": "unknown", "reason": "ambigua"})
        self.assertEqual(rt.fast_intent("Cancela mi suscripción de Spotify"), {"intent": "unknown", "reason": "operacion"})


class SafetyTest(unittest.TestCase):
    """La ruta rápida no debe adivinar entidades: ante una palabra desconocida, decide el modelo."""

    def test_comercio_en_minusculas_pasa_al_modelo(self):
        for q in ("cuanto gaste en mercadona", "¿Cuánto he gastado en Amazon este trimestre?", "¿Cuántas veces pedí Glovo el mes pasado?",
                  "¿Cuándo fue la última vez que fui al burguer king?"):
            self.assertIsNone(rt.fast_intent(q), q)

    def test_palabras_desconocidas_se_listan(self):
        self.assertEqual(rt.unresolved("cuanto gaste en mercadona"), ["mercadona"])
        self.assertEqual(rt.unresolved("¿Cuánto gasté en octubre?"), [])

    def test_gasto_e_ingreso_a_la_vez_pasa_al_modelo(self):
        self.assertIsNone(rt.fast_intent("¿Cuánto gasté y cuánto ingresé este mes?"))

    def test_sin_intent_claro_pasa_al_modelo(self):
        self.assertIsNone(rt.fast_intent("¿En qué gasté más dinero en agosto, en viajes o en restaurantes?"))

    def test_pregunta_de_cociente_no_se_responde_como_suma(self):
        self.assertEqual(rt.fast_intent("Cuánto de lo que ingresé el mes pasado he gastado hasta ahora?")["intent"], "unknown")

    def test_tokens_numericos_y_meses_se_conocen(self):
        self.assertEqual(rt.unresolved("gastos de más de 50 euros en septiembre"), [])


class DatasetTest(unittest.TestCase):
    """Regresión: lo que la ruta rápida resuelve en el dataset, lo resuelve bien. Cota optimista (las reglas
    se afinaron con estas preguntas)."""

    def setUp(self):
        self.items = [item for _, item in v.load(v.HERE / "questions.jsonl")]
        self.result = router_report.evaluate(self.items)

    def test_todo_lo_que_resuelve_es_correcto(self):
        wrong = [(item["id"], item["question"]) for item, _ in self.result["wrong"]]
        self.assertEqual(wrong, [])

    def test_cobertura_minima(self):
        self.assertGreaterEqual(len(self.result["covered"]) / self.result["total"], 0.70)


if __name__ == "__main__":
    unittest.main()
