import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from selfbank import contract, db
from selfbank.tools import categorize_wizard, ingest_cli, recategorize, system_check
from tests import fixtures as fx


def run(main, args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(args)
    return code, out.getvalue()


class ToolsCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.pages = fx.synthetic_pages()
        self.total = sum(len(p["transactions"]) for p in self.pages)
        (self.raw / "20261115T081455Z-1-transactions.json").write_text(json.dumps(self.pages), encoding="utf-8")
        (self.raw / "20261115T081455Z-1-balances.json").write_text(json.dumps(
            {"balances": [{"balance_type": "CLBD", "balance_amount": {"currency": "EUR", "amount": "1234.56"}, "reference_date": "2026-11-14"}]}), encoding="utf-8")
        self.session = self.root / "session.json"
        self.session.write_text(json.dumps({"valid_until": "2027-05-14T10:00:00+00:00", "accounts": [{"uid": "u1", "identification_hash": "hash-estable", "currency": "EUR"}]}), encoding="utf-8")
        self.db = str(self.root / "selfbank.db")

    def ingest(self, *extra):
        return run(ingest_cli.main, ["--raw-dir", str(self.raw), "--session", str(self.session), "--db", self.db,
                                     "--categories", str(self.root / "no.json"), *extra])


class IngestCliTest(ToolsCase):
    def test_importa_y_cuenta(self):
        code, out = self.ingest()
        self.assertEqual(code, 0)
        self.assertIn(f"inserted={self.total}", out)
        self.assertIn("saldos guardados=1", out)
        self.assertIn(f"Total de movimientos en la base: {self.total}", out)

    def test_es_idempotente(self):
        self.ingest()
        _, out = self.ingest()
        self.assertNotIn("inserted=", out.replace("inserted=0", ""))
        self.assertIn(f"already_present={self.total}", out)

    def test_guarda_la_caducidad_del_consentimiento(self):
        self.ingest()
        conn = db.connect(self.db, read_only=True)
        self.assertEqual(db.get_meta(conn, "consent_valid_until"), "2027-05-14")
        conn.close()

    def test_no_imprime_contenido_de_movimientos(self):
        _, out = self.ingest()
        for word in ("MERCADONA", "NETFLIX", "ANA GARCIA", "ALQUILER", "EMPRESA"):
            self.assertNotIn(word, out)

    def test_usa_el_fichero_mas_reciente(self):
        newer = [fx.tx("2026-11-10", "1.00", "DBIT", "COMPRA UNICA, X, Y")]
        (self.raw / "20261116T000000Z-1-transactions.json").write_text(json.dumps(fx.pages(newer)), encoding="utf-8")
        _, out = self.ingest()
        self.assertIn("Total de movimientos en la base: 1", out)

    def test_cuenta_que_no_esta_en_la_sesion_se_omite(self):
        (self.raw / "20261115T081455Z-2-transactions.json").write_text(json.dumps(fx.pages([fx.tx("2026-11-10", "1.00", "DBIT", "X")])), encoding="utf-8")
        code, out = self.ingest()
        self.assertEqual(code, 0)
        self.assertIn(f"Total de movimientos en la base: {self.total}", out)

    def test_latest_files(self):
        (self.raw / "ruido.txt").write_text("x")
        (self.raw / "20261114T000000Z-1-transactions.json").write_text("[]")
        found = ingest_cli.latest_files(self.raw)
        self.assertEqual(found[1]["transactions"].name, "20261115T081455Z-1-transactions.json")


class RecategorizeCliTest(ToolsCase):
    def test_aplica_las_categorias_propias(self):
        self.ingest()
        mine = self.root / "mine.json"
        mine.write_text(json.dumps({"mascotas": ["bar la esquina"]}), encoding="utf-8")
        code, out = run(recategorize.main, ["--db", self.db, "--categories", str(mine)])
        self.assertEqual(code, 0)
        self.assertRegex(out, r"reclasificados: [1-9]\d*")
        self.assertNotIn("BAR LA ESQUINA", out)
        conn = db.connect(self.db, read_only=True)
        self.assertGreater(conn.execute("SELECT COUNT(*) FROM transactions WHERE category = 'mascotas'").fetchone()[0], 0)
        conn.close()
        self.assertIn("reclasificados: 0", run(recategorize.main, ["--db", self.db, "--categories", str(mine)])[1])


class WizardTest(ToolsCase):
    def setUp(self):
        super().setUp()
        self.ingest()
        self.conn = db.connect(self.db)
        self.addCleanup(self.conn.close)
        self.rules = self.root / "mine.json"
        self.say = []

    def drive(self, answers, limit=40):
        feed = iter(answers)
        return categorize_wizard.run(self.conn, self.rules, limit, ask=lambda prompt: next(feed, "q"), say=self.say.append)

    def test_palabra_clave_general(self):
        self.assertEqual(categorize_wizard.keyword_for("carrefour express 1234 madrid"), "carrefour express madrid")
        self.assertEqual(categorize_wizard.keyword_for("tienda 12"), "tienda")
        self.assertEqual(categorize_wizard.keyword_for("12 34"), "12 34")

    def test_agrupa_sucursales(self):
        with self.conn:
            ids = [r[0] for r in self.conn.execute("SELECT id FROM transactions WHERE direction='out' AND kind='purchase' LIMIT 3")]
            for n, row_id in enumerate(ids):
                self.conn.execute("UPDATE transactions SET category='otros', merchant='SUPERMERCADO NUEVO %d', merchant_norm='supermercado nuevo %d' WHERE id=?" % (n, n), (row_id,))
        found = dict(categorize_wizard.candidates(self.conn, 100))
        self.assertEqual(found.get("supermercado nuevo"), 3)

    def test_clasifica_y_mejora_la_cobertura(self):
        with self.conn:
            self.conn.execute("UPDATE transactions SET category='otros' WHERE merchant_norm IN ('mercadona', 'lidl')")
        todo = categorize_wizard.candidates(self.conn, 5)
        self.assertTrue(todo)
        result = self.drive([str(categorize_wizard.MENU.index("supermercado") + 1)] * len(todo))
        self.assertGreater(result["added"], 0)
        self.assertGreater(result["after"][0], result["before"][0])
        saved = json.loads(self.rules.read_text(encoding="utf-8"))
        self.assertIn("supermercado", saved)

    def test_saltar_y_terminar_no_guardan_nada(self):
        with self.conn:
            self.conn.execute("UPDATE transactions SET category='otros' WHERE merchant_norm = 'mercadona'")
        result = self.drive(["", "99", "abc", "q"])
        self.assertEqual(result["added"], 0)
        self.assertFalse(self.rules.exists())

    def test_fichero_con_permisos_600_y_sin_duplicados(self):
        with self.conn:
            self.conn.execute("UPDATE transactions SET category='otros' WHERE merchant_norm = 'mercadona'")
        self.drive(["1"])
        if os.name == "posix":
            self.assertEqual(os.stat(self.rules).st_mode & 0o777, 0o600)
        with self.conn:
            self.conn.execute("UPDATE transactions SET category='otros' WHERE merchant_norm = 'mercadona'")
        self.drive(["1"])
        saved = json.loads(self.rules.read_text(encoding="utf-8"))
        self.assertEqual(sum(len(v) for v in saved.values()), 1)

    def test_solo_ofrece_categorias_que_se_pueden_consultar(self):
        allowed = set(contract.load_schema(contract.MODEL_SCHEMA_PATH)["properties"]["category"]["enum"])
        self.assertTrue(set(categorize_wizard.MENU) <= allowed)


class SystemCheckTest(ToolsCase):
    def check(self, *extra):
        self.ingest()
        return run(system_check.main, ["--db", self.db, "--raw-dir", str(self.raw), "--categories", str(self.root / "no.json"), *extra])

    def test_con_datos_sinteticos_todo_correcto(self):
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("TODO CORRECTO", out)
        for section in ("1. Integridad", "2. Calidad", "3. Las preguntas", "4. Contraste", "5. Seguridad"):
            self.assertIn(section, out)
        self.assertNotIn("[FALLO]", out)

    def test_no_muestra_contenido_ni_importes_por_defecto(self):
        _, out = self.check()
        for word in ("MERCADONA", "NETFLIX", "ANA GARCIA", "ALQUILER", "EMPRESA", "LIDL"):
            self.assertNotIn(word, out)
        self.assertNotRegex(out, r"\d+[.,]\d{2} €")

    def test_las_opciones_explicitas_si_muestran_datos(self):
        _, out = self.check("--show-monthly", "3", "--uncategorized", "5")
        self.assertRegex(out, r"2026-10\s+gastos\s+[\d.,]+ €")
        self.assertIn("DATOS REALES", out)

    def test_no_ensucia_el_historial_de_la_base_real(self):
        self.check()
        conn = db.connect(self.db, read_only=True)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM history").fetchone()[0], 0)
        conn.close()
        self.assertEqual([p.name for p in Path(self.db).parent.iterdir() if p.name.startswith(".check-")], [])

    def test_detecta_una_fuga_de_tarjeta(self):
        self.ingest()
        conn = db.connect(self.db)
        with conn:
            conn.execute("UPDATE transactions SET description = description || ' 4111111111111234' WHERE id = (SELECT MIN(id) FROM transactions)")
        conn.close()
        code, out = run(system_check.main, ["--db", self.db, "--raw-dir", str(self.raw), "--categories", str(self.root / "no.json")])
        self.assertEqual(code, 1)
        self.assertIn("[FALLO] la base no contiene números de tarjeta", out)

    def test_detecta_diferencias_con_el_bruto(self):
        self.ingest()
        conn = db.connect(self.db)
        with conn:
            conn.execute("DELETE FROM transactions WHERE id = (SELECT MAX(id) FROM transactions)")
        conn.close()
        code, out = run(system_check.main, ["--db", self.db, "--raw-dir", str(self.raw), "--categories", str(self.root / "no.json")])
        self.assertEqual(code, 1)
        self.assertIn("[FALLO] mismo nº de movimientos", out)

    def test_marca_fallo_si_el_modelo_no_contesta(self):
        from unittest import mock
        from selfbank import llm as llm_module
        dead = lambda model: llm_module.OllamaClient(model=model, host="http://127.0.0.1:9", timeout=1)
        self.ingest()
        with mock.patch.object(system_check, "OllamaClient", dead):
            code, out = run(system_check.main, ["--db", self.db, "--raw-dir", str(self.raw), "--categories", str(self.root / "no.json"), "--llm"])
        self.assertEqual(code, 1)
        self.assertIn("[FALLO] el modelo contestó a todas las preguntas que le tocaban", out)

    def test_revisa_que_el_historial_no_guarde_resultados(self):
        from selfbank.engine import Engine
        self.ingest()
        Engine(self.db, clock=lambda: fx.TODAY).ask("¿Cuánto gasté en octubre?")
        args = ["--db", self.db, "--raw-dir", str(self.raw), "--categories", str(self.root / "no.json")]
        code, out = run(system_check.main, args)
        self.assertEqual(code, 0, out)
        self.assertIn("el historial guarda solo la consulta interpretada (15 campos), nunca resultados  (1 preguntas revisadas)", out)
        conn = db.connect(self.db)
        with conn:
            conn.execute("UPDATE history SET query_json = ?", (json.dumps({"intent": "aggregate", "resultado": 1234.56}),))
        conn.close()
        code, out = run(system_check.main, args)
        self.assertEqual(code, 1)
        self.assertIn("[FALLO] el historial guarda solo la consulta interpretada", out)

    def test_dos_ejecuciones_no_arrastran_fallos(self):
        self.ingest()
        args = ["--db", self.db, "--raw-dir", str(self.raw), "--categories", str(self.root / "no.json")]
        conn = db.connect(self.db)
        with conn:
            conn.execute("DELETE FROM transactions WHERE id = (SELECT MAX(id) FROM transactions)")
        conn.close()
        self.assertEqual(run(system_check.main, args)[0], 1)
        self.ingest()  # restaura el dato borrado
        self.assertEqual(run(system_check.main, args)[0], 0)


if __name__ == "__main__":
    unittest.main()
