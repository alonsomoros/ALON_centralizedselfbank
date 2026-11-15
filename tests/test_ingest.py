import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from selfbank import db, ingest
from selfbank.categorize import Categorizer
from tests import fixtures as fx


class ConnCase(unittest.TestCase):
    def fresh(self):
        conn = db.connect(":memory:")
        self.addCleanup(conn.close)
        return conn


class AmountsTest(unittest.TestCase):
    def test_centimos_exactos(self):
        for text, cents in (("12.34", 1234), ("0.05", 5), ("1000", 100000), ("19.9", 1990), ("0", 0)):
            self.assertEqual(ingest.parse_cents(text), cents, text)

    def test_importes_invalidos(self):
        for text in ("", "abc", "1.234", "1,5", None):
            with self.assertRaises(ingest.IngestError):
                ingest.parse_cents(text)

    def test_nunca_hay_error_de_coma_flotante(self):
        self.assertEqual(ingest.parse_cents("0.10") + ingest.parse_cents("0.20"), 30)


class IngestTest(ConnCase):
    def setUp(self):
        self.conn = self.fresh()
        self.pages = fx.synthetic_pages()
        self.total = sum(len(p["transactions"]) for p in self.pages)

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    def test_importa_todo(self):
        stats = ingest.ingest_pages(self.conn, self.pages, "cuenta-1")
        self.assertEqual((stats["read"], stats["inserted"]), (self.total, self.total))
        self.assertEqual(self.count(), self.total)

    def test_es_idempotente(self):
        ingest.ingest_pages(self.conn, self.pages, "cuenta-1")
        again = ingest.ingest_pages(self.conn, self.pages, "cuenta-1")
        self.assertEqual(again["inserted"], 0)
        self.assertEqual(again["already_present"], self.total)
        self.assertEqual(self.count(), self.total)

    def test_conserva_los_duplicados_exactos(self):
        ingest.ingest_pages(self.conn, self.pages, "cuenta-1")
        n = self.conn.execute("SELECT COUNT(*) FROM transactions WHERE booking_date = '2026-10-20' AND amount_cents = 150").fetchone()[0]
        self.assertEqual(n, 2)

    def test_un_duplicado_que_aparece_despues_se_anade_una_vez(self):
        first = [fx.tx("2026-11-01", "1.50", "DBIT", fx.card_purchase("BAR LA ESQUINA"))] * 2
        ingest.ingest_pages(self.conn, fx.pages(first), "c")
        self.assertEqual(self.count(), 2)
        third = first + [fx.tx("2026-11-01", "1.50", "DBIT", fx.card_purchase("BAR LA ESQUINA"))]
        stats = ingest.ingest_pages(self.conn, fx.pages(third), "c")
        self.assertEqual((stats["inserted"], stats["already_present"]), (1, 2))
        self.assertEqual(self.count(), 3)
        self.assertEqual(ingest.ingest_pages(self.conn, fx.pages(third), "c")["inserted"], 0)

    def test_solapamiento_de_ventanas(self):
        rows = fx.synthetic_transactions()
        ingest.ingest_pages(self.conn, fx.pages(rows[:300]), "c")
        stats = ingest.ingest_pages(self.conn, fx.pages(rows[100:]), "c")
        self.assertEqual(stats["inserted"], len(rows) - 300)
        self.assertEqual(self.count(), len(rows))

    def test_una_ventana_mas_corta_no_borra_nada(self):
        rows = fx.synthetic_transactions()
        ingest.ingest_pages(self.conn, fx.pages(rows), "c")
        ingest.ingest_pages(self.conn, fx.pages(rows[:50]), "c")
        self.assertEqual(self.count(), len(rows))

    def test_orden_de_las_paginas_no_importa(self):
        rows = fx.synthetic_transactions()
        other = self.fresh()
        ingest.ingest_pages(self.conn, fx.pages(rows), "c")
        ingest.ingest_pages(other, fx.pages(list(reversed(rows))), "c")
        query = "SELECT booking_date, amount_cents, direction, description, kind, category FROM transactions ORDER BY booking_date, amount_cents, description"
        self.assertEqual([tuple(r) for r in self.conn.execute(query)], [tuple(r) for r in other.execute(query)])

    def test_cuentas_separadas(self):
        ingest.ingest_pages(self.conn, fx.pages([fx.tx("2026-11-01", "5.00", "DBIT", "COMPRA X, Y, Z")]), "a")
        ingest.ingest_pages(self.conn, fx.pages([fx.tx("2026-11-01", "5.00", "DBIT", "COMPRA X, Y, Z")]), "b")
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 2)

    def test_pendientes_y_defectuosos_no_se_guardan_ni_rompen(self):
        rows = [
            fx.tx("2026-11-01", "5.00", "DBIT", "PAGO EN BAR, X, TARJ. :*123456"),
            fx.tx("2026-11-01", "5.00", "DBIT", "PAGO EN BAR2, X, TARJ. :*123456", status="PDNG"),
            fx.tx("2026-11-01", "abc", "DBIT", "x"),
            fx.tx("2026-11-01", "5.00", "???", "x"),
            fx.tx("2026-11-01", "-5.00", "DBIT", "x"),
            dict(fx.tx("2026-11-01", "5.00", "DBIT", "x"), booking_date=None, value_date=None),
        ]
        stats = ingest.ingest_pages(self.conn, fx.pages(rows), "c")
        self.assertEqual((stats["inserted"], stats["skipped_not_booked"], stats["rejected"]), (1, 1, 4))

    def test_texto_ausente(self):
        row = fx.tx("2026-11-01", "5.00", "DBIT", "")
        row["remittance_information"] = None
        ingest.ingest_pages(self.conn, fx.pages([row]), "c")
        self.assertEqual(self.conn.execute("SELECT description FROM transactions").fetchone()[0], "(sin concepto)")

    def test_varios_fragmentos_de_texto_se_unen(self):
        row = fx.tx("2026-11-01", "5.00", "DBIT", "x")
        row["remittance_information"] = ["PAGO EN BAR,", "MADRID"]
        ingest.ingest_pages(self.conn, fx.pages([row]), "c")
        self.assertEqual(self.conn.execute("SELECT description FROM transactions").fetchone()[0], "PAGO EN BAR, MADRID")


class RecategorizeTest(ConnCase):
    def test_recalcula_con_reglas_nuevas_sin_tocar_importes_ni_claves(self):
        conn = self.fresh()
        rows = [fx.tx("2026-11-01", "5.00", "DBIT", fx.card_purchase("KIWOKO")), fx.tx("2026-11-02", "7.00", "DBIT", fx.card_purchase("MERCADONA"))]
        ingest.ingest_pages(conn, fx.pages(rows), "c")
        before = [tuple(r) for r in conn.execute("SELECT id, amount_cents, dedup_hash, dedup_n FROM transactions ORDER BY id")]
        self.assertEqual(dict(conn.execute("SELECT merchant_norm, category FROM transactions").fetchall()), {"kiwoko": "otros", "mercadona": "supermercado"})
        stats = ingest.recategorize(conn, Categorizer({"mascotas": ["kiwoko"]}))
        self.assertEqual((stats["changed"], stats["total"]), (1, 2))
        self.assertEqual(dict(conn.execute("SELECT merchant_norm, category FROM transactions").fetchall()), {"kiwoko": "mascotas", "mercadona": "supermercado"})
        self.assertEqual([tuple(r) for r in conn.execute("SELECT id, amount_cents, dedup_hash, dedup_n FROM transactions ORDER BY id")], before)
        self.assertEqual(ingest.recategorize(conn, Categorizer({"mascotas": ["kiwoko"]}))["changed"], 0)  # idempotente

    def test_arregla_datos_clasificados_con_reglas_antiguas(self):
        conn = self.fresh()
        ingest.ingest_pages(conn, fx.pages([fx.tx("2026-11-01", "5.00", "DBIT", "COMPRA RARO SL, PARIS, TARJETA 4111111111111234 , COMISION 1,20")]), "c")
        with conn:
            conn.execute("UPDATE transactions SET category = 'comisiones'")  # el error histórico
        self.assertEqual(ingest.recategorize(conn)["changed"], 1)
        self.assertEqual(conn.execute("SELECT category FROM transactions").fetchone()[0], "otros")

    def test_una_reimportacion_posterior_no_duplica_tras_reclasificar(self):
        conn = self.fresh()
        pages = fx.synthetic_pages()
        ingest.ingest_pages(conn, pages, "c")
        ingest.recategorize(conn, Categorizer({"mascotas": ["bar la esquina"]}))
        self.assertEqual(ingest.ingest_pages(conn, pages, "c")["inserted"], 0)


class SensitiveDataTest(ConnCase):
    def test_la_base_no_contiene_numeros_de_tarjeta(self):
        conn = self.fresh()
        ingest.ingest_pages(conn, fx.synthetic_pages(), "c")
        for column in ("description", "description_norm", "merchant", "merchant_norm"):
            for (value,) in conn.execute(f"SELECT {column} FROM transactions WHERE {column} IS NOT NULL"):
                self.assertIsNone(re.search(r"\d{9,}", value), (column, value))
                self.assertNotIn("411111", value)
                self.assertNotRegex(value, r"\*\d{5,}")

    def test_hay_textos_con_tarjeta_en_el_origen_y_se_enmascaran(self):
        raw = [t for p in fx.synthetic_pages() for t in p["transactions"]]
        self.assertTrue(any(fx.CARD16 in t["remittance_information"][0] for t in raw))
        conn = self.fresh()
        ingest.ingest_pages(conn, fx.synthetic_pages(), "c")
        masked = conn.execute("SELECT COUNT(*) FROM transactions WHERE description LIKE '%XXXXXXXXXXXX1234%'").fetchone()[0]
        self.assertGreater(masked, 0)

    def test_el_origen_no_se_modifica(self):
        pages = fx.synthetic_pages()
        before = repr(pages)
        ingest.ingest_pages(self.fresh(), pages, "c")
        self.assertEqual(repr(pages), before)


class DatabaseTest(ConnCase):
    def test_conexion_de_solo_lectura_no_puede_escribir(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "x.db")
            rw = db.connect(path)
            ingest.ingest_pages(rw, fx.pages([fx.tx("2026-11-01", "5.00", "DBIT", "COMPRA X, Y, Z")]), "c")
            rw.close()
            ro = db.connect(path, read_only=True)
            self.assertEqual(ro.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)
            for sql in ("DELETE FROM transactions", "INSERT INTO meta VALUES ('a','b')", "DROP TABLE transactions", "UPDATE transactions SET amount_cents = 0"):
                with self.assertRaises(sqlite3.OperationalError):
                    ro.execute(sql)
            ro.close()

    def test_permisos_600(self):
        if os.name != "posix":
            self.skipTest("solo en POSIX")
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "sub", "x.db")
            db.connect(path).close()
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_restricciones_del_esquema(self):
        conn = self.fresh()
        account = ingest.get_or_create_account(conn, "c")
        base = (account, "2026-01-01", None, 100, "out", "EUR", "d", "d", "other", None, None, "otros", "BOOK", None, "h", 1)
        sql = ("INSERT INTO transactions (account_id, booking_date, value_date, amount_cents, direction, currency, description, description_norm, "
               "kind, merchant, merchant_norm, category, status, entry_reference, dedup_hash, dedup_n) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
        conn.execute(sql, base)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(sql, base)  # misma clave de deduplicación
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(sql, base[:3] + (-1,) + base[4:14] + ("h2", 1))  # importe negativo
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(sql, base[:4] + ("sideways",) + base[5:14] + ("h3", 1))  # sentido inválido

    def test_version_de_esquema(self):
        conn = self.fresh()
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
        conn.execute("PRAGMA user_version = 99")
        with self.assertRaises(RuntimeError):
            db.init(conn)

    def test_meta(self):
        conn = self.fresh()
        self.assertIsNone(db.get_meta(conn, "x"))
        db.set_meta(conn, "x", "1")
        db.set_meta(conn, "x", "2")
        self.assertEqual(db.get_meta(conn, "x"), "2")


class BalancesTest(ConnCase):
    def test_saldos(self):
        conn = self.fresh()
        data = {"balances": [
            {"balance_type": "CLBD", "balance_amount": {"currency": "EUR", "amount": "1234.56"}, "reference_date": "2026-11-14"},
            {"balance_type": "OPBD", "balance_amount": {"currency": "EUR", "amount": "-20.10"}, "reference_date": "2026-11-14"},
            {"balance_type": "XX", "balance_amount": None},
        ]}
        self.assertEqual(ingest.ingest_balances(conn, data, "c"), 2)
        got = dict(conn.execute("SELECT balance_type, amount_cents FROM balances").fetchall())
        self.assertEqual(got, {"CLBD": 123456, "OPBD": -2010})
        data["balances"][0]["balance_amount"]["amount"] = "1.00"
        ingest.ingest_balances(conn, data, "c")  # se actualiza, no se duplica
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM balances").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT amount_cents FROM balances WHERE balance_type='CLBD'").fetchone()[0], 100)


if __name__ == "__main__":
    unittest.main()
