"""Ejecutor SQL: cifras calculadas a mano, seguridad y comparación diferencial contra un cálculo independiente."""

import os
import random
import tempfile
import unittest
from datetime import date

from selfbank import contract, db, ingest
from selfbank.tools.oracle import Oracle
from selfbank.executor import Executor, ExecutorError
from selfbank.text import squash
from tests import fixtures as fx

TODAY = date(2026, 11, 15)


def query(**model):
    return contract.expand(contract.sanitize(model), TODAY)


def card(merchant):
    return fx.card_purchase(merchant)


MICRO = [
    ("2026-10-01", "100.00", "DBIT", card("MERCADONA")),
    ("2026-10-05", "50.50", "DBIT", card("MERCADONA")),
    ("2026-10-10", "20.00", "DBIT", card("GLOVO")),
    ("2026-10-15", "2000.00", "CRDT", "TRANSFERENCIA DE EMPRESA EJEMPLO SL, CONCEPTO NOMINA OCTUBRE"),
    ("2026-10-20", "30.00", "DBIT", "BIZUM A LUIS DE MARTA RUIZ CONCEPTO: regalo"),
    ("2026-10-21", "15.00", "CRDT", "BIZUM DE ANA GARCIA LOPEZ CONCEPTO cena"),
    ("2026-11-02", "75.25", "DBIT", card("MERCADONA")),
    ("2026-11-03", "9.99", "DBIT", card("NETFLIX.COM")),
    ("2026-11-04", "49.90", "DBIT", card("ZARA")),
    ("2026-11-04", "49.90", "CRDT", "ANUL PAGO EN ZARA, MADRID, TARJ. :*123456"),
    ("2026-11-05", "8.00", "CRDT", "DEVOLUCION COMPRA PEDIDO"),
]


class ExecutorCase(unittest.TestCase):
    def build(self, rows):
        conn = db.connect(":memory:")
        self.addCleanup(conn.close)
        ingest.ingest_pages(conn, fx.pages([fx.tx(*r) for r in rows]), "c")
        return conn


class MicroTest(ExecutorCase):
    def setUp(self):
        self.conn = self.build(MICRO)
        self.ex = Executor(self.conn)

    def run_q(self, **model):
        return self.ex.run(query(**model))

    def test_suma_de_gastos_y_de_ingresos(self):
        self.assertEqual(self.run_q(intent="aggregate", metric="sum", direction="out", period="octubre"), {"value": 200.50, "count": 4})
        self.assertEqual(self.run_q(intent="aggregate", metric="sum", direction="in", period="octubre"), {"value": 2015.00, "count": 2})

    def test_neto_con_direccion_any(self):
        self.assertEqual(self.run_q(intent="aggregate", metric="sum", period="octubre")["value"], 1814.50)

    def test_conteo(self):
        got = self.run_q(intent="aggregate", metric="count", direction="out")
        self.assertEqual(got["count"], 7)  # 3 Mercadona + Glovo + Bizum + Netflix + Zara

    def test_periodo_y_total_historico(self):
        self.assertEqual(self.run_q(intent="aggregate", metric="sum", direction="out")["value"], 200.50 + 75.25 + 9.99 + 49.90)
        self.assertEqual(self.run_q(intent="aggregate", metric="sum", direction="out", period="ayer")["value"], 0.0)

    def test_categoria(self):
        self.assertEqual(self.run_q(intent="aggregate", metric="sum", direction="out", category="supermercado", period="octubre")["value"], 150.50)

    def test_texto_exacto_plural_y_aproximado(self):
        base = dict(intent="aggregate", metric="sum", direction="out")
        self.assertEqual(self.run_q(text="mercadona", **base)["value"], 225.75)
        self.assertEqual(self.run_q(text="MERCADONA", **base)["value"], 225.75)
        self.assertEqual(self.run_q(text="Mercadonna", **base)["value"], 225.75)  # errata: coincidencia aproximada
        self.assertEqual(self.run_q(intent="aggregate", metric="sum", direction="in", text="devoluciones")["value"], 8.00)  # plural
        self.assertEqual(self.run_q(intent="aggregate", metric="sum", direction="in", text="nómina")["value"], 2000.00)  # tildes

    def test_alternativas_con_barra(self):
        self.assertEqual(self.run_q(intent="aggregate", metric="sum", direction="out", text="glovo|netflix")["value"], 29.99)

    def test_texto_inexistente_da_cero_sin_error(self):
        got = self.run_q(intent="aggregate", metric="sum", direction="out", text="zzzzqqq")
        self.assertEqual(got, {"value": 0.0, "count": 0})

    def test_importes(self):
        rows = self.run_q(intent="list_transactions", direction="out", min_amount=50)["rows"]
        self.assertEqual(sorted(r["amount"] for r in rows), [-100.0, -75.25, -50.5])
        rows = self.run_q(intent="list_transactions", direction="out", max_amount=20)["rows"]
        self.assertEqual(sorted(r["amount"] for r in rows), [-20.0, -9.99])
        rows = self.run_q(intent="list_transactions", direction="out", min_amount=20, max_amount=50)["rows"]
        self.assertEqual(sorted(r["amount"] for r in rows), [-49.9, -30.0, -20.0])

    def test_desglose_por_categoria(self):
        rows = self.run_q(intent="aggregate", metric="sum", direction="out", group_by="category")["rows"]
        self.assertEqual([(r["group"], r["amount"]) for r in rows],
                         [("supermercado", 225.75), ("compras", 49.9), ("bizum", 30.0), ("restaurantes", 20.0), ("ocio_suscripciones", 9.99)])
        self.assertEqual(rows[0]["count"], 3)

    def test_desglose_por_mes_en_orden_cronologico(self):
        rows = self.run_q(intent="aggregate", metric="sum", direction="out", group_by="month")["rows"]
        self.assertEqual([(r["group"], r["amount"]) for r in rows], [("2026-10", 200.50), ("2026-11", 135.14)])

    def test_mes_con_mas_gastos(self):
        rows = self.run_q(intent="aggregate", metric="sum", direction="out", group_by="month", sort="amount_desc", limit=1)["rows"]
        self.assertEqual([(r["group"], r["amount"]) for r in rows], [("2026-10", 200.50)])

    def test_desglose_por_comercio_y_conteo(self):
        rows = self.run_q(intent="aggregate", metric="count", direction="out", group_by="merchant")["rows"]
        self.assertEqual((rows[0]["group"], rows[0]["amount"]), ("MERCADONA", 3))

    def test_listado_por_defecto_es_lo_mas_reciente(self):
        rows = self.run_q(intent="list_transactions", direction="out", limit=3)["rows"]
        self.assertEqual([r["date"] for r in rows], ["2026-11-04", "2026-11-03", "2026-11-02"])
        self.assertTrue(all(r["amount"] < 0 for r in rows))

    def test_listado_ordenado_por_importe(self):
        rows = self.run_q(intent="list_transactions", direction="out", sort="amount_desc", limit=2)["rows"]
        self.assertEqual([r["amount"] for r in rows], [-100.0, -75.25])
        rows = self.run_q(intent="list_transactions", direction="out", sort="amount_asc", limit=1)["rows"]
        self.assertEqual(rows[0]["amount"], -9.99)

    def test_signo_en_listados(self):
        rows = self.run_q(intent="list_transactions", direction="in", limit=1, sort="amount_desc")["rows"]
        self.assertEqual(rows[0]["amount"], 2000.00)

    def test_ultimo_movimiento(self):
        row = self.run_q(intent="last_transaction", direction="out")["rows"][0]
        self.assertEqual((row["date"], row["amount"]), ("2026-11-04", -49.9))
        row = self.run_q(intent="last_transaction", category="bizum")["rows"][0]
        self.assertEqual((row["date"], row["amount"]), ("2026-10-21", 15.0))
        self.assertEqual(self.run_q(intent="last_transaction", text="inexistente")["rows"], [])

    def test_comparacion(self):
        got = self.run_q(intent="compare_periods", metric="sum", direction="out", period="este_mes", compare_period="mes_pasado")
        self.assertEqual(got, {"current": 135.14, "reference": 200.50})
        got = self.run_q(intent="compare_periods", metric="count", direction="out", period="este_mes", compare_period="mes_pasado")
        self.assertEqual(got, {"current": 3.0, "reference": 4.0})

    def test_ranking_excluye_personas_salvo_que_se_pidan(self):
        rows = self.run_q(intent="top_merchants", direction="out")["rows"]
        self.assertEqual(rows[0], {"merchant": "MERCADONA", "amount": 225.75, "count": 3})
        self.assertNotIn("LUIS", [r["merchant"] for r in rows])
        contacts = self.run_q(intent="top_merchants", direction="out", category="bizum")["rows"]
        self.assertEqual([r["merchant"] for r in contacts], ["LUIS"])

    def test_ranking_de_ingresos_incluye_contrapartes(self):
        names = [r["merchant"] for r in self.run_q(intent="top_merchants", direction="in")["rows"]]
        self.assertEqual(names[0], "EMPRESA EJEMPLO SL")
        self.assertIn("ANA GARCIA LOPEZ", names)

    def test_ranking_respeta_el_limite(self):
        self.assertEqual(len(self.run_q(intent="top_merchants", direction="out", limit=2)["rows"]), 2)
        self.assertEqual(len(self.run_q(intent="top_merchants", direction="out")["rows"]), 4)

    def test_saldo(self):
        with self.assertRaises(ExecutorError):
            self.run_q(intent="balance")
        ingest.ingest_balances(self.conn, {"balances": [
            {"balance_type": "CLBD", "balance_amount": {"currency": "EUR", "amount": "1234.56"}},
            {"balance_type": "OPBD", "balance_amount": {"currency": "EUR", "amount": "1000.00"}}]}, "c")
        self.assertEqual(self.run_q(intent="balance"), {"value": 1234.56})

    def test_base_vacia(self):
        ex = Executor(self.build([]))
        self.assertEqual(ex.run(query(intent="aggregate", metric="sum", direction="out")), {"value": 0.0, "count": 0})
        self.assertEqual(ex.run(query(intent="list_transactions"))["rows"], [])
        self.assertEqual(ex.run(query(intent="top_merchants"))["rows"], [])
        self.assertEqual(ex.run(query(intent="recurring"))["rows"], [])
        self.assertEqual(ex.run(query(intent="aggregate", metric="sum", group_by="month"))["rows"], [])

    def test_tope_de_filas(self):
        rows = [("2026-10-01", "1.00", "DBIT", card(f"TIENDA {i}")) for i in range(300)]
        ex = Executor(self.build(rows))
        self.assertEqual(len(ex.run(query(intent="list_transactions"))["rows"]), Executor.DEFAULT_LIST)
        self.assertEqual(len(ex.run(query(intent="list_transactions", limit=1000))["rows"]), Executor.MAX_LIST)

    def test_intent_desconocido(self):
        with self.assertRaises(ExecutorError):
            self.ex.run({"intent": "unknown"})


class SecurityTest(ExecutorCase):
    def setUp(self):
        self.conn = self.build(MICRO)
        self.ex = Executor(self.conn)

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    def test_inyeccion_en_el_texto(self):
        for payload in ("'; DROP TABLE transactions; --", "' OR '1'='1", "x' UNION SELECT 1,2,3 --", "\"; DELETE FROM transactions; --", "%", "_", "\\"):
            got = self.ex.run(query(intent="aggregate", metric="sum", direction="out", text=payload))
            self.assertEqual(got["count"], 0, payload)
            self.assertEqual(self.count(), len(MICRO), payload)

    def test_los_comodines_de_like_son_literales(self):
        self.assertEqual(self.ex.run(query(intent="list_transactions", text="%"))["rows"], [])
        self.assertEqual(self.ex.run(query(intent="list_transactions", text="m_r"))["rows"], [])  # «_» no es comodín: si lo fuera, casaría con «mer»
        self.assertNotEqual(self.ex.run(query(intent="list_transactions", text="mer"))["rows"], [])

    def test_inyeccion_en_la_categoria_y_el_desglose(self):
        bad = dict(query(intent="aggregate", metric="sum"), category="x'; DROP TABLE transactions; --")
        self.assertEqual(self.ex.run(bad)["count"], 0)
        bad = dict(query(intent="aggregate", metric="sum"), group_by="category; DROP TABLE transactions")
        with self.assertRaises(ExecutorError):
            self.ex.run(bad)
        bad = dict(query(intent="list_transactions"), sort="date_desc; DROP TABLE transactions")
        with self.assertRaises(KeyError):
            self.ex.run(bad)
        self.assertEqual(self.count(), len(MICRO))

    def test_el_ejecutor_funciona_en_modo_solo_lectura(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "x.db")
            rw = db.connect(path)
            ingest.ingest_pages(rw, fx.pages([fx.tx(*r) for r in MICRO]), "c")
            rw.close()
            ro = db.connect(path, read_only=True)
            ex = Executor(ro)
            self.assertEqual(ex.run(query(intent="aggregate", metric="sum", direction="out", period="octubre"))["value"], 200.50)
            ro.close()


class RecurringTest(ExecutorCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)
        ingest.ingest_pages(self.conn, fx.synthetic_pages(), "c")
        self.ex = Executor(self.conn)

    def test_detecta_suscripciones_y_recibos_fijos(self):
        rows = self.ex.run(query(intent="recurring", direction="out"))["rows"]
        found = {r["merchant"]: (r["amount"], r["every_days"]) for r in rows}
        self.assertEqual(found.get("NETFLIX.COM"), (12.99, 30))
        self.assertEqual(found.get("SPOTIFY"), (9.99, 30))
        self.assertEqual(found.get("GIMNASIO EJEMPLO"), (29.9, 30))
        self.assertEqual(found.get("RECIBO ALQUILER VIVIENDA"), (650.0, 30))

    def test_no_confunde_habitos_con_cargos_periodicos(self):
        names = [r["merchant"] for r in self.ex.run(query(intent="recurring", direction="out"))["rows"]]
        for habitual in ("MERCADONA", "LIDL", "GLOVO", "REPSOL", "BAR LA ESQUINA", "CAJERO BANCO"):
            self.assertNotIn(habitual, names)
        self.assertNotIn("EMPRESA EJEMPLO SL", names)  # la nómina es un ingreso

    def test_ordenado_por_importe(self):
        rows = self.ex.run(query(intent="recurring", direction="out"))["rows"]
        self.assertEqual([r["amount"] for r in rows], sorted((r["amount"] for r in rows), reverse=True))

    def test_filtros(self):
        rows = self.ex.run(query(intent="recurring", direction="out", category="ocio_suscripciones"))["rows"]
        self.assertEqual({r["merchant"] for r in rows}, {"NETFLIX.COM", "SPOTIFY", "GIMNASIO EJEMPLO"})
        rows = self.ex.run(query(intent="recurring", direction="out", text="netflix"))["rows"]
        self.assertEqual([r["merchant"] for r in rows], ["NETFLIX.COM"])

    def test_un_cargo_que_se_dejo_de_pagar_no_cuenta(self):
        rows = [(f"2025-0{m}-10", "9.99", "DBIT", card("SERVICIO VIEJO")) for m in range(1, 7)]
        rows.append(("2026-11-01", "5.00", "DBIT", card("OTRA COSA")))
        ex = Executor(self.build(rows))
        self.assertEqual(ex.run(query(intent="recurring", direction="out"))["rows"], [])

    def test_pocos_cargos_no_son_periodicos(self):
        rows = [("2026-08-10", "9.99", "DBIT", card("SERVICIO NUEVO")), ("2026-09-10", "9.99", "DBIT", card("SERVICIO NUEVO"))]
        self.assertEqual(Executor(self.build(rows)).run(query(intent="recurring", direction="out"))["rows"], [])


# ---------------------------------------------------------------------------------------------
# Test diferencial: el SQL frente a un cálculo independiente en Python sobre los mismos datos.
# ---------------------------------------------------------------------------------------------

class DifferentialTest(ExecutorCase):
    def setUp(self):
        raw = fx.synthetic_transactions()
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)
        ingest.ingest_pages(self.conn, fx.pages(raw), "c")
        self.ex = Executor(self.conn)
        self.oracle = Oracle(raw)
        self.rng = random.Random(2026)

    def random_model(self):
        rng = self.rng
        model = {"intent": "aggregate", "metric": rng.choice(["sum", "count"]), "direction": rng.choice(["out", "in", "any"])}
        if rng.random() < 0.5:
            model["category"] = rng.choice(["supermercado", "restaurantes", "transporte", "ocio_suscripciones", "bizum", "compras", "efectivo", "hogar_facturas", "transferencias"])
        if rng.random() < 0.5:
            model["period"] = rng.choice(["este_mes", "mes_pasado", "este_ano", "ultimos_30_dias", "ultimos_90_dias", "ultimos_365_dias", "octubre", "septiembre", "ayer", "hoy", "ano_pasado"])
        if rng.random() < 0.2:
            model["text"] = rng.choice(["mercadona", "netflix", "glovo", "ana garcia", "zara", "lidl|dia supermercados", "alquiler"])
        if rng.random() < 0.2:
            model["min_amount"] = rng.choice([5, 20, 50])
        if rng.random() < 0.15:
            model["max_amount"] = rng.choice([30, 100, 700])
        return model

    def test_300_consultas_aleatorias_de_totales(self):
        compared = 0
        for _ in range(300):
            model = self.random_model()
            q = query(**model)
            if q["min_amount"] is not None and q["max_amount"] is not None and q["min_amount"] > q["max_amount"]:
                continue
            rows = self.oracle.select(q)
            got = self.ex.run(q)
            self.assertEqual(got["count"], len(rows), model)
            self.assertAlmostEqual(got["value"], self.oracle.total(rows, q["direction"]) / 100, places=2, msg=str(model))
            compared += 1
        self.assertGreater(compared, 250)

    def test_desgloses_aleatorios(self):
        for _ in range(60):
            model = self.random_model()
            model["group_by"] = self.rng.choice(["category", "merchant", "month"])
            q = query(**model)
            if q["min_amount"] is not None and q["max_amount"] is not None and q["min_amount"] > q["max_amount"]:
                continue
            rows = self.oracle.select(q)
            got = self.ex.run(q)["rows"]
            self.assertEqual(sum(r["count"] for r in got), len(rows), model)
            if q["metric"] == "sum":
                self.assertAlmostEqual(sum(r["amount"] for r in got), self.oracle.total(rows, q["direction"]) / 100, places=2, msg=str(model))
            keys = [r["group"] for r in got]
            self.assertEqual(len(keys), len(set(keys)), model)

    def test_listados_contienen_exactamente_lo_que_cumple_el_filtro(self):
        for _ in range(60):
            model = self.random_model()
            model.update(intent="list_transactions", limit=200)
            model.pop("metric")
            q = query(**model)
            if q["min_amount"] is not None and q["max_amount"] is not None and q["min_amount"] > q["max_amount"]:
                continue
            expected = sorted((r["booking_date"], r["description"], r["amount_cents"] * (1 if r["direction"] == "in" else -1)) for r in self.oracle.select(q))
            got = sorted((r["date"], r["description"], round(r["amount"] * 100)) for r in self.ex.run(q)["rows"])
            self.assertEqual(got, expected[:200] if len(expected) <= 200 else got, model)
            if len(expected) <= 200:
                self.assertEqual(got, expected, model)

    def test_ordenaciones(self):
        q = query(intent="list_transactions", direction="out", sort="amount_desc", limit=30)
        amounts = [abs(r["amount"]) for r in self.ex.run(q)["rows"]]
        self.assertEqual(amounts, sorted(amounts, reverse=True))
        q = query(intent="list_transactions", direction="out", sort="amount_asc", limit=30)
        amounts = [abs(r["amount"]) for r in self.ex.run(q)["rows"]]
        self.assertEqual(amounts, sorted(amounts))
        q = query(intent="list_transactions", limit=40)
        dates = [r["date"] for r in self.ex.run(q)["rows"]]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_ranking_frente_al_oraculo(self):
        q = query(intent="top_merchants", direction="out", limit=5)
        buckets = {}
        for r in self.oracle.rows:
            if r["direction"] == "out" and r["merchant_norm"] and r["kind"] not in ("bizum", "transfer"):
                buckets[r["merchant_norm"]] = buckets.get(r["merchant_norm"], 0) + r["amount_cents"]
        expected = sorted(buckets.values(), reverse=True)[:5]
        self.assertEqual([round(r["amount"] * 100) for r in self.ex.run(q)["rows"]], expected)


if __name__ == "__main__":
    unittest.main()
