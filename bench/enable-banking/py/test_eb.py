"""Pruebas de los scripts de sondeo. Sin red, sin claves reales, sin datos reales.

Ejecutar:  python -m unittest -v      (desde esta carpeta)
"""

import base64
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import auth_finish
import auth_start
import eb_common as eb
import fetch_probe

FAKE_IBAN = "ES9121000418450200051332"
FAKE_AMOUNT = "999.99"
FAKE_TEXT = "CAFE SECRETO"


def run(main, argv, stdin_secret=None):
    out, err = io.StringIO(), io.StringIO()
    patches = [mock.patch("getpass.getpass", return_value=stdin_secret)] if stdin_secret is not None else []
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
    return code, out.getvalue(), err.getvalue()


class HomeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"SELFBANK_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)


class JwtTest(HomeTestCase):
    def test_firma_valida_y_campos_correctos(self):
        key = Path(self.tmp.name) / "k.pem"
        pub = Path(self.tmp.name) / "p.pem"
        openssl = eb.find_openssl()
        subprocess.run([openssl, "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
        subprocess.run([openssl, "rsa", "-in", str(key), "-pubout", "-out", str(pub)], check=True, capture_output=True)

        jwt = eb.sign_jwt("app-123", key, ttl=3600, now=1_000_000)
        header_b64, payload_b64, sig_b64 = jwt.split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)
        header = json.loads(base64.urlsafe_b64decode(pad(header_b64)))
        payload = json.loads(base64.urlsafe_b64decode(pad(payload_b64)))
        self.assertEqual(header, {"typ": "JWT", "alg": "RS256", "kid": "app-123"})
        self.assertEqual(payload["iss"], "enablebanking.com")
        self.assertEqual(payload["aud"], "api.enablebanking.com")
        self.assertEqual(payload["exp"] - payload["iat"], 3600)

        sig_file = Path(self.tmp.name) / "sig.bin"
        sig_file.write_bytes(base64.urlsafe_b64decode(pad(sig_b64)))
        data_file = Path(self.tmp.name) / "data.txt"
        data_file.write_bytes(f"{header_b64}.{payload_b64}".encode())
        verified = subprocess.run(
            [openssl, "dgst", "-sha256", "-verify", str(pub), "-signature", str(sig_file), str(data_file)],
            capture_output=True,
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_clave_inexistente_da_error_claro(self):
        with self.assertRaises(eb.EbError):
            eb.sign_jwt("app", Path(self.tmp.name) / "no-existe.pem")


class ExtractCodeTest(unittest.TestCase):
    def test_url_valida(self):
        url = "https://localhost:8765/callback?code=ABC123&state=xyz"
        self.assertEqual(eb.extract_code(url, "xyz"), "ABC123")

    def test_espacios_alrededor(self):
        self.assertEqual(eb.extract_code("  https://localhost:8765/callback?state=s&code=C  \n", "s"), "C")

    def test_state_distinto_se_rechaza(self):
        with self.assertRaises(eb.EbError):
            eb.extract_code("https://localhost:8765/callback?code=A&state=otro", "esperado")

    def test_sin_code(self):
        with self.assertRaises(eb.EbError):
            eb.extract_code("https://localhost:8765/callback?state=s", "s")

    def test_error_del_banco(self):
        with self.assertRaises(eb.EbError) as ctx:
            eb.extract_code("https://localhost:8765/callback?error=access_denied&state=s", "s")
        self.assertIn("access_denied", str(ctx.exception))


class ValidUntilTest(unittest.TestCase):
    def test_formato_rfc3339_con_zona(self):
        value = eb.valid_until(179)
        self.assertRegex(value, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.000000\+00:00$")


class SchemaTest(unittest.TestCase):
    def test_no_filtra_valores(self):
        sample = {"amount": FAKE_AMOUNT, "items": [{"text": FAKE_TEXT, "n": 3}], "iban": FAKE_IBAN}
        schema = eb.collect_schema(sample)
        text = json.dumps({k: sorted(v) for k, v in schema.items()})
        for secret in (FAKE_AMOUNT, FAKE_TEXT, FAKE_IBAN):
            self.assertNotIn(secret, text)
        self.assertEqual(schema["items[].n"], {"int"})


class DryRunTest(HomeTestCase):
    def test_no_envia_ni_firma_ni_escribe(self):
        with mock.patch.object(eb, "request", side_effect=AssertionError("no debe haber red")), mock.patch.object(
            eb, "sign_jwt", side_effect=AssertionError("no debe firmar")
        ):
            code, out, _ = run(auth_start.main, ["--profile", "sandbox", "--bank", "BBVA", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("DRY-RUN", out)
        self.assertIn('"name": "BBVA"', out)
        self.assertFalse(eb.pending_path("sandbox").exists())


class FlowTest(HomeTestCase):
    def fake_request(self, method, path, jwt, body=None, params=None):
        if path == "/auth":
            return {"url": "https://bank.example/login?x=1", "authorization_id": "auth-1"}
        if path == "/sessions":
            self.assertEqual(body, {"code": "THECODE"})
            return {
                "session_id": "sess-1",
                "aspsp": {"name": "BBVA", "country": "ES"},
                "psu_type": "personal",
                "access": {"valid_until": "2027-01-01T00:00:00+00:00"},
                "accounts": [
                    {"uid": "uid-0000-abcd", "identification_hash": "h1", "currency": "EUR", "account_id": {"iban": FAKE_IBAN}, "name": "Cuenta de Alonso"}
                ],
            }
        if path.endswith("/balances"):
            return {"balances": [{"balance_type": "CLAV", "balance_amount": {"currency": "EUR", "amount": FAKE_AMOUNT}}]}
        if path.endswith("/transactions"):
            if params.get("continuation_key") == "k2":
                return {"transactions": [self.tx("2026-02-01")]}
            return {"transactions": [self.tx("2026-01-05"), self.tx("2026-01-06")], "continuation_key": "k2"}
        raise AssertionError(path)

    @staticmethod
    def tx(date):
        return {
            "entry_reference": "e1",
            "transaction_amount": {"currency": "EUR", "amount": FAKE_AMOUNT},
            "credit_debit_indicator": "DBIT",
            "status": "BOOK",
            "booking_date": date,
            "remittance_information": [FAKE_TEXT],
        }

    def test_flujo_completo_sin_filtrar_datos(self):
        with mock.patch.object(eb, "sign_jwt", return_value="jwt"), mock.patch.object(eb, "request", self.fake_request):
            code, start_out, _ = run(auth_start.main, ["--profile", "sandbox", "--bank", "BBVA"])
            self.assertEqual(code, 0)
            self.assertIn("https://bank.example/login", start_out)
            state = eb.load_json(eb.pending_path("sandbox"))["state"]

            callback = f"https://localhost:8765/callback?code=THECODE&state={state}"
            code, finish_out, _ = run(auth_finish.main, ["--profile", "sandbox"], stdin_secret=callback)
            self.assertEqual(code, 0)
            self.assertFalse(eb.pending_path("sandbox").exists(), "el fichero pendiente debe borrarse")
            session_text = eb.session_path("sandbox").read_text(encoding="utf-8")
            for secret in (FAKE_IBAN, "Cuenta de Alonso", "THECODE"):
                self.assertNotIn(secret, session_text)
                self.assertNotIn(secret, finish_out)

            code, probe_out, _ = run(fetch_probe.main, ["--profile", "sandbox"])
            self.assertEqual(code, 0)
            self.assertIn("movimientos : 3", probe_out)
            self.assertIn("páginas     : 2", probe_out)
            for secret in (FAKE_AMOUNT, FAKE_TEXT, FAKE_IBAN):
                self.assertNotIn(secret, probe_out)
            raws = list(eb.raw_dir("sandbox").glob("*-transactions.json"))
            self.assertEqual(len(raws), 1)

    def test_state_ajeno_no_gasta_el_code(self):
        calls = []
        with mock.patch.object(eb, "sign_jwt", return_value="jwt"), mock.patch.object(
            eb, "request", lambda *a, **k: calls.append(a) or self.fake_request(*a, **k)
        ):
            run(auth_start.main, ["--profile", "sandbox", "--bank", "BBVA"])
            calls.clear()
            code, _, err = run(
                auth_finish.main, ["--profile", "sandbox"], stdin_secret="https://localhost:8765/callback?code=THECODE&state=falso"
            )
        self.assertEqual(code, 1)
        self.assertIn("state", err)
        self.assertEqual(calls, [], "no debe llamarse a /sessions con un state ajeno")
        self.assertTrue(eb.pending_path("sandbox").exists())


@unittest.skipIf(sys.platform == "win32", "los permisos POSIX no aplican en Windows")
class PermissionsTest(HomeTestCase):
    def test_ficheros_con_permiso_600(self):
        path = Path(self.tmp.name) / "x" / "s.json"
        eb.secure_write_json(path, {"a": 1})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
