import unittest
from datetime import date

from selfbank import answers, contract, describe

TODAY = date(2026, 11, 15)


def full(**model):
    return contract.expand(model, TODAY)


class FormatTest(unittest.TestCase):
    def test_fechas(self):
        self.assertEqual(describe.fmt_date("2026-10-01"), "1 oct 2026")
        self.assertEqual(describe.fmt_range("2026-10-01", "2026-10-31"), "1 oct – 31 oct 2026")
        self.assertEqual(describe.fmt_range("2026-11-15", "2026-11-15"), "15 nov 2026")
        self.assertEqual(describe.fmt_range("2025-11-16", "2026-11-15"), "16 nov 2025 – 15 nov 2026")

    def test_importes(self):
        self.assertEqual(describe.money(1234.5), "1.234,50 €")
        self.assertEqual(describe.money(0), "0,00 €")
        self.assertEqual(describe.money(-12), "-12,00 €")
        self.assertEqual(describe.money(1234567.891), "1.234.567,89 €")

    def test_importes_de_filtro(self):
        self.assertEqual(describe.amount_label(50), "50 €")
        self.assertEqual(describe.amount_label(1200), "1.200 €")
        self.assertEqual(describe.amount_label(12.5), "12,50 €")

    def test_periodo_en_frase(self):
        self.assertEqual(describe.period_text(full(intent="balance")), "en todo el histórico")
        self.assertEqual(describe.period_text(full(intent="list_transactions", period="ayer")), "el 14 nov 2026")
        self.assertEqual(describe.period_text(full(intent="list_transactions", period="octubre")), "del 1 oct al 31 oct 2026")


class DescribeTest(unittest.TestCase):
    def test_agregado_con_filtros_y_periodo(self):
        got = describe.describe(full(intent="aggregate", metric="sum", direction="out", category="restaurantes", period="octubre"))
        self.assertEqual(got["title"], "Gasto total")
        self.assertEqual(got["chips"], ["restaurantes", "1 oct – 31 oct 2026"])
        self.assertEqual(got["text"], "Gasto total · restaurantes · 1 oct – 31 oct 2026")

    def test_titulos_por_intent(self):
        cases = [
            (dict(intent="balance"), "Saldo actual"),
            (dict(intent="aggregate", metric="count", direction="out"), "Número de pagos"),
            (dict(intent="aggregate", metric="sum", direction="in", group_by="month"), "Ingresos totales por mes"),
            (dict(intent="list_transactions", direction="in"), "Ingresos"),
            (dict(intent="last_transaction", direction="out"), "Último pago"),
            (dict(intent="top_merchants", direction="out"), "Ranking de comercios por gasto"),
            (dict(intent="recurring", direction="out"), "Cargos periódicos"),
            (dict(intent="compare_periods", metric="sum", direction="out", period="este_mes", compare_period="mes_pasado"), "Comparación de gasto"),
        ]
        for model, title in cases:
            self.assertEqual(describe.describe(full(**model))["title"], title, model)

    def test_importes_y_orden(self):
        got = describe.describe(full(intent="list_transactions", min_amount=10, max_amount=40, sort="amount_desc", limit=3))
        self.assertEqual(got["chips"], ["entre 10 € y 40 €", "los 3 mayores"])
        self.assertIn("más de 50 €", describe.describe(full(intent="list_transactions", min_amount=50))["chips"])
        self.assertIn("menos de 10 €", describe.describe(full(intent="list_transactions", max_amount=10))["chips"])

    def test_texto_buscado_y_comparacion(self):
        got = describe.describe(full(intent="compare_periods", metric="sum", direction="out", text="Zara", period="este_mes", compare_period="mes_pasado"))
        self.assertEqual(got["chips"], ["«Zara»", "1 nov – 15 nov 2026", "frente a 1 oct – 31 oct 2026"])

    def test_unknown(self):
        got = describe.describe(full(intent="unknown", reason="ambigua"))
        self.assertEqual((got["title"], got["chips"]), ("Sin interpretar", []))

    def test_cada_intent_se_describe_sin_error(self):
        for intent in ("aggregate", "list_transactions", "last_transaction", "balance", "compare_periods", "top_merchants", "recurring", "unknown"):
            model = {"intent": intent, "metric": "sum", "period": "este_mes", "compare_period": "mes_pasado", "reason": "ambigua"}
            query = full(**contract.sanitize(model))
            self.assertTrue(describe.describe(query)["text"])


class AnswersTest(unittest.TestCase):
    def render(self, result, **model):
        return answers.render(full(**model), result)

    def test_suma_gasto(self):
        got = self.render({"value": 1234.5, "count": 9}, intent="aggregate", metric="sum", direction="out", category="restaurantes", period="octubre")
        self.assertEqual(got["text"], "Has gastado 1.234,50 € (restaurantes) del 1 oct al 31 oct 2026.")
        self.assertEqual((got["kind"], got["data"]), ("scalar", {"value": 1234.5, "unit": "EUR"}))

    def test_cuenta(self):
        got = self.render({"value": 0, "count": 1}, intent="aggregate", metric="count", direction="out", category="efectivo", period="este_ano")
        self.assertEqual(got["text"], "Hay 1 pago (efectivo) del 1 ene al 15 nov 2026.")
        self.assertEqual(got["data"], {"value": 1, "unit": "count"})

    def test_agrupado(self):
        rows = [{"group": "restaurantes", "amount": 300.0, "count": 5}, {"group": "ocio_suscripciones", "amount": 90.0, "count": 3}]
        got = self.render({"rows": rows}, intent="aggregate", metric="sum", direction="out", group_by="category", period="este_mes")
        self.assertEqual(got["kind"], "table")
        self.assertIn("restaurantes con 300,00 €", got["text"])
        self.assertEqual(got["data"]["rows"], [["restaurantes", 300.0], ["ocio y suscripciones", 90.0]])
        self.assertEqual([c["key"] for c in got["data"]["columns"]], ["category", "amount"])

    def test_mes_con_mas_gastos(self):
        got = self.render({"rows": [{"group": "2026-03", "amount": 2100.0, "count": 40}]}, intent="aggregate", metric="sum", direction="out",
                          group_by="month", sort="amount_desc", limit=1)
        self.assertIn("mar 2026 con 2.100,00 €", got["text"])

    def test_listado_y_vacio(self):
        rows = [{"date": "2026-11-14", "description": "COMERCIO DEMO", "amount": -12.5}]
        got = self.render({"rows": rows}, intent="list_transactions", direction="out", period="ayer")
        self.assertEqual(got["text"], "Estos son tus 1 pago el 14 nov 2026.")
        self.assertEqual([c["key"] for c in got["data"]["columns"]], ["date", "description", "amount"])
        empty = self.render({"rows": []}, intent="list_transactions", direction="out", period="ayer")
        self.assertEqual((empty["kind"], empty["data"]), ("none", None))

    def test_ultimo(self):
        rows = [{"date": "2026-11-03", "description": "COMERCIO DEMO", "amount": -20.0}]
        got = self.render({"rows": rows}, intent="last_transaction", direction="out", text="Zara")
        self.assertEqual(got["text"], "Tu último pago («Zara») fue de 20,00 € el 3 nov 2026.")
        self.assertIn("No encuentro", self.render({"rows": []}, intent="last_transaction")["text"])

    def test_saldo(self):
        self.assertEqual(self.render({"value": 2345.67}, intent="balance")["text"], "Tu saldo actual es 2.345,67 €.")

    def test_comparacion(self):
        got = self.render({"current": 420.0, "reference": 1200.0}, intent="compare_periods", metric="sum", direction="out", period="este_mes", compare_period="mes_pasado")
        self.assertEqual(got["text"], "1 nov – 15 nov 2026: 420,00 €. 1 oct – 31 oct 2026: 1.200,00 €. Diferencia: 780,00 € menos.")
        self.assertEqual(len(got["data"]["rows"]), 2)

    def test_ranking_y_recurrentes(self):
        top = self.render({"rows": [{"merchant": "TIENDA DEMO", "amount": 120.0, "count": 4}]}, intent="top_merchants", direction="out", period="octubre")
        self.assertIn("TIENDA DEMO (120,00 €)", top["text"])
        rec = self.render({"rows": [{"merchant": "SERVICIO DEMO", "amount": 9.99, "every_days": 30}]}, intent="recurring", direction="out")
        self.assertIn("Detecto 1 cargo periódico", rec["text"])
        self.assertEqual(self.render({"rows": []}, intent="recurring", direction="out")["kind"], "none")

    def test_unknown_usa_el_mensaje_fijo(self):
        for reason, text in contract.MESSAGES.items():
            got = self.render(None, intent="unknown", reason=reason)
            self.assertEqual((got["text"], got["kind"]), (text, "none"))


if __name__ == "__main__":
    unittest.main()
