"""Pruebas de la API con el servidor simulado, levantado de verdad en un puerto libre."""

import http.client
import json
import threading
import unittest
from datetime import date
from pathlib import Path

from selfbank.api import mock, server
from selfbank.schema import check

TOKEN = "token-de-prueba"
ORIGIN = "http://localhost:5173"
SCHEMAS = server.SCHEMAS
DATASET = Path(__file__).resolve().parents[1] / "bench" / "consultas" / "questions.jsonl"


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = mock.MockEngine(model_delay=0.01)
        cls.server = server.make_server(cls.engine, TOKEN, port=0, cors_origins=[ORIGIN], quiet=True)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, method, path, body=None, token=TOKEN, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(headers or {})
        if token is not None:
            h["Authorization"] = f"Bearer {token}"
        data = raw
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=h)
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        parsed = json.loads(payload) if payload else None
        return response.status, response, parsed

    def ask(self, question):
        status, response, body = self.call("POST", "/api/ask", {"question": question})
        self.assertEqual(status, 200, body)
        self.assertEqual(check(body, SCHEMAS["AskResponse"]), [], question)
        return body

    def assert_error(self, status, code, got):
        s, response, body = got
        self.assertEqual(s, status, body)
        self.assertEqual(check(body, SCHEMAS["Error"]), [])
        self.assertEqual(body["error"]["code"], code)
        return response


class AuthAndRoutingTest(ApiTestCase):
    def test_health_no_pide_token(self):
        status, _, body = self.call("GET", "/api/health", token=None)
        self.assertEqual((status, body), (200, {"ok": True}))

    def test_sin_token_o_con_token_malo(self):
        for token in (None, "otro", ""):
            response = self.assert_error(401, "unauthorized", self.call("GET", "/api/status", token=token))
            self.assertEqual(response.getheader("WWW-Authenticate"), "Bearer")

    def test_esquema_de_autorizacion_incorrecto(self):
        got = self.call("GET", "/api/status", token=None, headers={"Authorization": f"Basic {TOKEN}"})
        self.assert_error(401, "unauthorized", got)

    def test_todas_las_rutas_protegidas_menos_health(self):
        for method, path in (("GET", "/api/status"), ("GET", "/api/suggestions"), ("POST", "/api/ask"), ("POST", "/api/feedback")):
            self.assert_error(401, "unauthorized", self.call(method, path, {"question": "x"}, token=None))

    def test_ruta_inexistente_y_metodo_incorrecto(self):
        self.assert_error(404, "not_found", self.call("GET", "/api/nada"))
        self.assert_error(405, "method_not_allowed", self.call("GET", "/api/ask"))
        self.assert_error(405, "method_not_allowed", self.call("POST", "/api/status", {}))

    def test_cabeceras_de_seguridad(self):
        _, response, _ = self.call("GET", "/api/status")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        self.assertTrue(response.getheader("Content-Type").startswith("application/json"))

    def test_token_vacio_no_se_admite(self):
        with self.assertRaises(ValueError):
            server.make_server(self.engine, "", port=0)


class StatusAndSuggestionsTest(ApiTestCase):
    def test_status(self):
        status, _, body = self.call("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(check(body, SCHEMAS["Status"]), [])
        self.assertEqual(body["mode"], "mock")

    def test_sugerencias_cumplen_el_contrato(self):
        status, _, body = self.call("GET", "/api/suggestions")
        self.assertEqual((status, check(body, SCHEMAS["Suggestions"])), (200, []))

    def test_cada_sugerencia_se_responde_al_instante_sin_modelo(self):
        _, _, body = self.call("GET", "/api/suggestions")
        for suggestion in body["suggestions"]:
            answer = self.ask(suggestion["question"])
            self.assertEqual((answer["source"], answer["status"]), ("rules", "answered"), suggestion["question"])


class AskTest(ApiTestCase):
    def test_respuesta_por_reglas(self):
        got = self.ask("¿Cuánto gasté en transporte la semana pasada?")
        self.assertEqual((got["status"], got["source"], got["reason"]), ("answered", "rules", None))
        self.assertEqual(got["interpretation"]["title"], "Gasto total")
        self.assertIn("transporte", got["interpretation"]["chips"])
        self.assertEqual(got["answer"]["kind"], "scalar")
        self.assertTrue(got["mock"])

    def test_respuesta_por_el_modelo(self):
        got = self.ask("cuanto gaste en mercadona")
        self.assertEqual((got["status"], got["source"]), ("answered", "model"))
        self.assertIn("«mercadona»", got["interpretation"]["chips"])

    def test_pregunta_ambigua(self):
        got = self.ask("¿Y el mes anterior?")
        self.assertEqual((got["status"], got["reason"], got["answer"]["kind"]), ("needs_clarification", "ambigua", "none"))

    def test_no_soportado(self):
        got = self.ask("¿Cuánto he gastado de media al día este mes?")
        self.assertEqual((got["status"], got["reason"]), ("unsupported", "fuera_de_alcance"))
        got = self.ask("Cancela mi suscripción de Spotify")
        self.assertEqual((got["status"], got["reason"]), ("unsupported", "operacion"))

    def test_la_interpretacion_incluye_la_consulta_completa(self):
        query = self.ask("¿Cuál es mi saldo?")["interpretation"]["query"]
        self.assertEqual(len(query), 15)
        self.assertEqual(query["intent"], "balance")

    def test_mismo_texto_mismas_cifras(self):
        a, b = self.ask("¿Cuánto gasté en octubre?"), self.ask("¿Cuánto gasté en octubre?")
        self.assertEqual(a["answer"], b["answer"])
        self.assertNotEqual(a["id"], b["id"])

    def test_la_pregunta_se_recorta(self):
        self.assertEqual(self.ask("   ¿Cuál es mi saldo?  ")["question"], "¿Cuál es mi saldo?")

    def test_acentos_y_emojis_viajan_bien(self):
        got = self.ask("¿Cuánto gasté en café ☕ el año pasado?")
        self.assertEqual(got["question"], "¿Cuánto gasté en café ☕ el año pasado?")

    def test_todo_el_dataset_cumple_el_contrato(self):
        count = 0
        for line in DATASET.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            got = self.ask(item["question"])  # ask() ya comprueba el esquema
            self.assertIn(got["status"], ("answered", "needs_clarification", "unsupported"))
            count += 1
        self.assertGreaterEqual(count, 60)

    def test_los_datos_de_tabla_cumplen_su_esquema(self):
        seen = set()
        for question in ("Dime el gasto por categorías de los últimos 30 días", "Dime mis últimas 5 compras", "¿Gasté más este mes que el mes pasado?",
                         "¿Dónde gasto más dinero?", "¿Qué suscripciones pago?", "¿Cuánto gasté en octubre?"):
            answer = self.ask(question)["answer"]
            schema = SCHEMAS["TableData"] if answer["kind"] == "table" else SCHEMAS["ScalarData"]
            self.assertEqual(check(answer["data"], schema), [], question)
            seen.add(answer["kind"])
        self.assertEqual(seen, {"table", "scalar"})

    def test_filas_coinciden_con_las_columnas(self):
        answer = self.ask("Dime mis últimas 5 compras")["answer"]
        width = len(answer["data"]["columns"])
        self.assertTrue(all(len(row) == width for row in answer["data"]["rows"]))
        self.assertEqual(len(answer["data"]["rows"]), 5)


class KeepAliveTest(ApiTestCase):
    """Una respuesta de error que no llega a leer el cuerpo no debe desincronizar la conexión persistente."""

    def request_then_health(self, method, path, body, headers):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=body, headers=headers)
        first = conn.getresponse()
        first.read()
        conn.request("GET", "/api/health")  # misma conexión (o una nueva si el servidor la cerró)
        second = conn.getresponse()
        payload = second.read()
        conn.close()
        return first.status, second.status, json.loads(payload)

    def test_401_con_cuerpo_sin_leer(self):
        body = json.dumps({"question": "hola"}).encode()
        got = self.request_then_health("POST", "/api/ask", body, {"Content-Type": "application/json"})
        self.assertEqual(got, (401, 200, {"ok": True}))

    def test_415_con_cuerpo_sin_leer(self):
        got = self.request_then_health("POST", "/api/ask", b'{"question":"hola"}', {"Authorization": f"Bearer {TOKEN}", "Content-Type": "text/plain"})
        self.assertEqual(got, (415, 200, {"ok": True}))

    def test_413_con_cuerpo_sin_leer(self):
        got = self.request_then_health("POST", "/api/ask", b'{"question":"' + b"a" * 9000 + b'"}',
                                       {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
        self.assertEqual(got, (413, 200, {"ok": True}))

    def test_405_y_404_con_cuerpo_sin_leer(self):
        auth = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
        self.assertEqual(self.request_then_health("POST", "/api/status", b'{"a":1}', auth), (405, 200, {"ok": True}))
        self.assertEqual(self.request_then_health("POST", "/api/nada", b'{"a":1}', auth), (404, 200, {"ok": True}))

    def test_una_peticion_valida_sigue_siendo_persistente(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        for _ in range(3):
            conn.request("GET", "/api/health")
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, 200)
            self.assertNotEqual((response.getheader("Connection") or "").lower(), "close")
        conn.close()


class ValidationTest(ApiTestCase):
    def test_pregunta_vacia_o_larga(self):
        for question in ("", "x" * 501):
            self.assert_error(400, "invalid_request", self.call("POST", "/api/ask", {"question": question}))

    def test_campos_extra_o_faltantes(self):
        self.assert_error(400, "invalid_request", self.call("POST", "/api/ask", {"question": "hola", "extra": 1}))
        self.assert_error(400, "invalid_request", self.call("POST", "/api/ask", {}))
        self.assert_error(400, "invalid_request", self.call("POST", "/api/ask", {"question": 5}))

    def test_json_invalido(self):
        got = self.call("POST", "/api/ask", raw=b"{no es json", headers={"Content-Type": "application/json"})
        self.assert_error(400, "invalid_request", got)

    def test_content_type_incorrecto(self):
        got = self.call("POST", "/api/ask", raw=b'{"question":"hola"}', headers={"Content-Type": "text/plain"})
        self.assert_error(415, "unsupported_media_type", got)

    def test_cuerpo_demasiado_grande(self):
        got = self.call("POST", "/api/ask", raw=b'{"question":"' + b"a" * 9000 + b'"}', headers={"Content-Type": "application/json"})
        self.assert_error(413, "payload_too_large", got)

    def test_cuerpo_que_no_es_objeto(self):
        got = self.call("POST", "/api/ask", raw=b"[1,2]", headers={"Content-Type": "application/json"})
        self.assert_error(400, "invalid_request", got)


class ScenarioTest(ApiTestCase):
    def test_modelo_ocupado(self):
        response = self.assert_error(503, "model_busy", self.call("POST", "/api/ask", {"question": "__busy__"}))
        self.assertEqual(response.getheader("Retry-After"), "5")

    def test_error_interno_no_filtra_detalles(self):
        s, _, body = self.call("POST", "/api/ask", {"question": "__error__"})
        self.assertEqual((s, body["error"]["code"]), (500, "internal"))
        self.assertNotIn("Traceback", json.dumps(body))


class FeedbackTest(ApiTestCase):
    def test_feedback_correcto(self):
        answer = self.ask("¿Cuánto gasté este mes?")
        s, _, body = self.call("POST", "/api/feedback", {"answer_id": answer["id"], "verdict": "wrong", "correction": "quería el mes pasado"})
        self.assertEqual((s, check(body, SCHEMAS["FeedbackResponse"]), body["stored"]), (200, [], True))
        self.assertEqual(self.engine.feedback_log[-1]["verdict"], "wrong")

    def test_respuesta_desconocida(self):
        self.assert_error(404, "not_found", self.call("POST", "/api/feedback", {"answer_id": "ans_000000000000", "verdict": "ok"}))

    def test_veredicto_o_id_invalidos(self):
        answer = self.ask("¿Cuánto gasté este mes?")
        self.assert_error(400, "invalid_request", self.call("POST", "/api/feedback", {"answer_id": answer["id"], "verdict": "quizá"}))
        self.assert_error(400, "invalid_request", self.call("POST", "/api/feedback", {"answer_id": "xx", "verdict": "ok"}))

    def test_correccion_demasiado_larga(self):
        answer = self.ask("¿Cuánto gasté este mes?")
        self.assert_error(400, "invalid_request", self.call("POST", "/api/feedback", {"answer_id": answer["id"], "verdict": "wrong", "correction": "x" * 301}))


class CorsTest(ApiTestCase):
    def test_preflight_de_un_origen_permitido(self):
        _, response, _ = self.call("OPTIONS", "/api/ask", token=None, headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"})
        self.assertEqual(response.status, 204)
        self.assertEqual(response.getheader("Access-Control-Allow-Origin"), ORIGIN)
        self.assertIn("Authorization", response.getheader("Access-Control-Allow-Headers"))

    def test_origen_no_permitido_no_recibe_cabeceras_cors(self):
        _, response, _ = self.call("GET", "/api/health", token=None, headers={"Origin": "https://sitio-ajeno.example"})
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))

    def test_peticion_sin_origen_no_lleva_cors(self):
        _, response, _ = self.call("GET", "/api/health", token=None)
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))


class MockHelpersTest(unittest.TestCase):
    def test_meses_consecutivos_sin_saltos(self):
        self.assertEqual(mock.month_keys(date(2026, 11, 15), 6), ["2026-11", "2026-10", "2026-09", "2026-08", "2026-07", "2026-06"])

    def test_meses_cruzan_de_ano(self):
        self.assertEqual(mock.month_keys(date(2027, 2, 1), 4), ["2027-02", "2027-01", "2026-12", "2026-11"])

    def test_desglose_por_mes_sin_meses_repetidos_ni_saltados(self):
        e = mock.MockEngine(today=date(2026, 11, 15), model_delay=0)
        rows = e.ask("Evolución mensual de mis gastos este año")["answer"]["data"]["rows"]
        labels = [r[0] for r in rows]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertEqual(set(labels), {"nov 2026", "oct 2026", "sep 2026", "ago 2026", "jul 2026", "jun 2026"})

    def test_token_desde_fichero(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "token")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("secreto-largo\n")
            os.chmod(path, 0o600)
            self.assertEqual(mock.read_token_file(path), "secreto-largo")
            if os.name == "posix":
                os.chmod(path, 0o644)
                with self.assertRaises(PermissionError):
                    mock.read_token_file(path)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("  \n")
            os.chmod(path, 0o600)
            with self.assertRaises(ValueError):
                mock.read_token_file(path)


class MockEngineTest(unittest.TestCase):
    def test_las_cifras_son_deterministas_y_no_negativas_en_totales(self):
        e = mock.MockEngine(model_delay=0)
        a, b = e.ask("¿Cuánto gasté en octubre?"), e.ask("¿Cuánto gasté en octubre?")
        self.assertEqual(a["answer"]["data"], b["answer"]["data"])
        self.assertGreater(a["answer"]["data"]["value"], 0)

    def test_importes_respetan_los_filtros(self):
        e = mock.MockEngine(model_delay=0)
        rows = e.ask("Gastos de más de 50 euros en septiembre")["answer"]["data"]["rows"]
        self.assertTrue(rows)
        self.assertTrue(all(abs(r[2]) >= 50 for r in rows))
        rows = e.ask("Pagos de menos de 10 euros este mes")["answer"]["data"]["rows"]
        self.assertTrue(all(abs(r[2]) <= 10 for r in rows))

    def test_limite_se_respeta(self):
        e = mock.MockEngine(model_delay=0)
        self.assertEqual(len(e.ask("Muéstrame mis últimos 10 movimientos")["answer"]["data"]["rows"]), 10)
        self.assertEqual(len(e.ask("Dime los 3 sitios donde más gasté en octubre")["answer"]["data"]["rows"]), 3)

    def test_el_historial_de_respuestas_esta_acotado(self):
        e = mock.MockEngine(model_delay=0, keep=3)
        for _ in range(6):
            e.ask("¿Cuál es mi saldo?")
        self.assertEqual(len(e.answers), 3)


if __name__ == "__main__":
    unittest.main()
