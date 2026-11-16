"""Sincronización periódica con el banco. Todo con datos SINTÉTICOS y un `fetch` falso: nunca se toca la red."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

from selfbank import bank, db, sync
from selfbank.tools import sync_cli
from tests import fixtures as fx

TODAY = date(2026, 11, 16)


def session(valid_until="2027-05-14T10:00:00+00:00", accounts=None):
    accounts = accounts if accounts is not None else [{"uid": "u1", "identification_hash": "h1", "currency": "EUR"}]
    return {"valid_until": valid_until, "accounts": accounts}


class FakeBank:
    """Responde a /transactions y /balances; guarda cada petición para inspeccionarla."""

    def __init__(self, transactions=None, fail_uids=()):
        self.calls = []
        self.transactions = transactions if transactions is not None else {}
        self.fail_uids = set(fail_uids)

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        uid = path.split("/")[2]
        if uid in self.fail_uids:
            raise bank.BankError("HTTP 500 simulado")
        if path.endswith("/balances"):
            return {"balances": [{"balance_type": "CLBD", "balance_amount": {"currency": "EUR", "amount": "100.00"}, "reference_date": "2026-11-15"}]}
        pages = self.transactions.get(uid, [{"transactions": [], "continuation_key": None}])
        key = (params or {}).get("continuation_key")
        index = int(key) if key else 0
        page = dict(pages[index])
        page["continuation_key"] = str(index + 1) if index + 1 < len(pages) else None
        return page

    def tx_calls(self):
        return [params for path, params in self.calls if path.endswith("/transactions")]


def two_pages():
    return [
        {"transactions": [fx.tx("2026-11-10", "10.00", "DBIT", fx.card_purchase("MERCADONA"))]},
        {"transactions": [fx.tx("2026-11-12", "5.00", "DBIT", fx.card_purchase("LIDL"))]},
    ]


class SyncCase(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    def run_sync(self, fake, sess=None, **kwargs):
        return sync.run_sync(self.conn, sess or session(), fake, TODAY, sleep=lambda _s: None, **kwargs)


class FirstAndLaterRunsTest(SyncCase):
    def test_la_primera_vez_pide_todo_el_historico_sin_fecha(self):
        fake = FakeBank({"u1": two_pages()})
        self.run_sync(fake)
        self.assertEqual(fake.tx_calls()[0], {"strategy": "longest"})

    def test_despues_pide_desde_el_ultimo_movimiento_menos_el_solape(self):
        self.run_sync(FakeBank({"u1": two_pages()}))
        fake = FakeBank({"u1": two_pages()})
        self.run_sync(fake, overlap_days=5)
        self.assertEqual(fake.tx_calls()[0], {"date_from": "2026-11-07"})  # último movimiento 2026-11-12 menos 5 días

    def test_pagina_con_continuation_key(self):
        fake = FakeBank({"u1": two_pages()})
        report = self.run_sync(fake)
        self.assertEqual([p.get("continuation_key") for p in fake.tx_calls()], [None, "1"])
        self.assertEqual(report["inserted"], 2)
        self.assertEqual(self.count(), 2)

    def test_es_idempotente_aunque_se_solapen_las_ventanas(self):
        self.run_sync(FakeBank({"u1": two_pages()}))
        report = self.run_sync(FakeBank({"u1": two_pages()}))
        self.assertEqual(report["inserted"], 0)
        self.assertEqual(self.count(), 2)

    def test_guarda_saldos_ultima_sincronizacion_y_caducidad(self):
        report = self.run_sync(FakeBank({"u1": two_pages()}))
        self.assertEqual(report["balances"], 1)
        self.assertIsNotNone(db.get_meta(self.conn, "last_sync"))
        self.assertEqual(db.get_meta(self.conn, "consent_valid_until"), "2027-05-14")


class ConsentTest(SyncCase):
    def test_caducado_no_hace_ninguna_peticion(self):
        fake = FakeBank()
        report = self.run_sync(fake, session("2026-11-15T10:00:00+00:00"))
        self.assertEqual(report["status"], "consent_expired")
        self.assertEqual(fake.calls, [])

    def test_caduca_hoy_aun_sincroniza(self):
        report = self.run_sync(FakeBank({"u1": two_pages()}), session("2026-11-16T23:00:00+00:00"))
        self.assertEqual(report["status"], "ok")

    def test_avisa_a_14_dias_o_menos_y_sigue(self):
        report = self.run_sync(FakeBank({"u1": two_pages()}), session("2026-11-30T10:00:00+00:00"))
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["consent_days_left"], 14)
        self.assertIn("consent_expiring", report["warnings"])

    def test_sin_aviso_con_margen(self):
        report = self.run_sync(FakeBank({"u1": two_pages()}), session("2026-12-01T10:00:00+00:00"))
        self.assertEqual(report["consent_days_left"], 15)
        self.assertNotIn("consent_expiring", report["warnings"])


class FailureTest(SyncCase):
    def two_accounts(self):
        return session(accounts=[{"uid": "bad", "identification_hash": "h-bad"}, {"uid": "u2", "identification_hash": "h2"}])

    def test_una_cuenta_que_falla_no_bloquea_a_las_demas(self):
        report = self.run_sync(FakeBank({"u2": two_pages()}, fail_uids={"bad"}), self.two_accounts())
        self.assertEqual(report["status"], "partial")
        self.assertEqual((report["accounts_ok"], report["accounts_failed"]), (1, 1))
        self.assertEqual(report["inserted"], 2)

    def test_todas_fallan(self):
        report = self.run_sync(FakeBank(fail_uids={"bad", "u2"}), self.two_accounts())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["inserted"], 0)

    def test_cuenta_sin_identificador_estable_se_omite(self):
        sess = session(accounts=[{"uid": "u1"}, {"uid": "u2", "identification_hash": "h2"}])
        fake = FakeBank({"u2": two_pages()})
        report = self.run_sync(fake, sess)
        self.assertEqual(report["accounts_skipped"], 1)
        self.assertTrue(all("/u1/" not in path for path, _ in fake.calls))

    def test_tope_de_paginas_marca_parcial(self):
        many = [{"transactions": [fx.tx(f"2026-11-{d:02d}", "1.00", "DBIT", fx.card_purchase("LIDL"))]} for d in range(1, 6)]
        report = self.run_sync(FakeBank({"u1": many}), max_pages=3)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["truncated"], 1)

    def test_el_informe_no_contiene_texto_de_movimientos(self):
        report = self.run_sync(FakeBank({"u1": two_pages()}))
        text = json.dumps(report)
        for secret in ("MERCADONA", "LIDL", "10.00", "TARJ"):
            self.assertNotIn(secret, text)


class CliCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.db = str(self.root / "selfbank.db")
        self.session_file = self.root / "session.json"

    def write_session(self, **kwargs):
        self.session_file.write_text(json.dumps(session(**kwargs)), encoding="utf-8")

    def run_cli(self, fake):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sync_cli.main(["--db", self.db, "--session", str(self.session_file), "--categories", str(self.root / "no.json")],
                                 fetch=fake, today=TODAY, sleep=lambda _s: None)
        return code, out.getvalue() + err.getvalue()


class SyncCliTest(CliCase):
    def test_ok_imprime_solo_contadores_y_sale_con_0(self):
        self.write_session()
        code, output = self.run_cli(FakeBank({"u1": two_pages()}))
        self.assertEqual(code, 0)
        self.assertIn("inserted=2", output)
        for secret in ("MERCADONA", "LIDL", "10.00"):
            self.assertNotIn(secret, output)

    def test_consentimiento_caducado_sale_con_3(self):
        self.write_session(valid_until="2026-11-01T00:00:00+00:00")
        fake = FakeBank()
        code, output = self.run_cli(fake)
        self.assertEqual(code, 3)
        self.assertEqual(fake.calls, [])
        self.assertIn("consentimiento caducado", output)

    def test_sin_sesion_sale_con_2(self):
        code, output = self.run_cli(FakeBank())
        self.assertEqual(code, 2)
        self.assertIn("sesión", output)

    def test_fallo_parcial_sale_con_1(self):
        self.write_session(accounts=[{"uid": "bad", "identification_hash": "h-bad"}, {"uid": "u2", "identification_hash": "h2"}])
        code, _ = self.run_cli(FakeBank({"u2": two_pages()}, fail_uids={"bad"}))
        self.assertEqual(code, 1)

    def test_el_mensaje_de_error_del_banco_no_se_imprime_entero(self):
        self.write_session()

        def boom(path, params=None):
            raise bank.BankError("HTTP 500 " + "x" * 5000)

        code, output = self.run_cli(boom)
        self.assertEqual(code, 1)
        self.assertLess(len(output), 600)


@unittest.skipUnless(shutil.which("openssl"), "openssl no disponible")
class JwtTest(unittest.TestCase):
    def test_firma_rs256_verificable_con_la_clave_publica(self):
        with tempfile.TemporaryDirectory() as folder:
            key, pub = Path(folder, "k.pem"), Path(folder, "k.pub")
            subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
            subprocess.run(["openssl", "rsa", "-in", str(key), "-pubout", "-out", str(pub)], check=True, capture_output=True)
            token = bank.sign_jwt("app-id", key, now=1_000)
            head, body, signature = token.split(".")
            pad = lambda s: s + "=" * (-len(s) % 4)
            import base64
            self.assertEqual(json.loads(base64.urlsafe_b64decode(pad(head))), {"typ": "JWT", "alg": "RS256", "kid": "app-id"})
            claims = json.loads(base64.urlsafe_b64decode(pad(body)))
            self.assertEqual((claims["iss"], claims["aud"], claims["iat"], claims["exp"]), ("enablebanking.com", "api.enablebanking.com", 1000, 4600))
            sig_file = Path(folder, "sig.bin")
            sig_file.write_bytes(base64.urlsafe_b64decode(pad(signature)))
            result = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(pub), "-signature", str(sig_file)],
                                    input=f"{head}.{body}".encode(), capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


class BankClientTest(unittest.TestCase):
    def test_un_error_http_no_vuelca_el_cuerpo_completo(self):
        import urllib.error

        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many", {}, io.BytesIO(b"y" * 5000))

        fetch = bank.make_fetch("app", "no-hace-falta.pem", sign=lambda *_: "jwt", opener=opener)
        with self.assertRaises(bank.BankError) as caught:
            fetch("/accounts/u1/balances")
        self.assertIn("429", str(caught.exception))
        self.assertLess(len(str(caught.exception)), 300)

    def test_la_peticion_lleva_el_jwt_y_los_parametros(self):
        seen = {}

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout):
            seen["url"], seen["auth"], seen["timeout"] = request.full_url, request.get_header("Authorization"), timeout
            return Response(b'{"ok": true}')

        fetch = bank.make_fetch("app", "k.pem", sign=lambda *_: "JWT123", opener=opener)
        self.assertEqual(fetch("/accounts/u1/transactions", {"date_from": "2026-11-07"}), {"ok": True})
        self.assertEqual(seen["url"], "https://api.enablebanking.com/accounts/u1/transactions?date_from=2026-11-07")
        self.assertEqual((seen["auth"], seen["timeout"]), ("Bearer JWT123", 30))


if __name__ == "__main__":
    unittest.main()
