"""Motor real: pregunta -> respuesta con SQL real, con un modelo falso para controlar lo que traduce."""

import http.client
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from selfbank import db, engine as engine_module, ingest, llm as llm_module
from selfbank.api import server
from selfbank.api.server import ApiError
from selfbank.engine import Engine
from selfbank.executor import Executor
from selfbank.schema import check
from tests import fixtures as fx

TODAY = fx.TODAY
DATASET = Path(__file__).resolve().parents[1] / "bench" / "consultas" / "questions.jsonl"


class FakeLLM:
    model = "modelo-falso"

    def __init__(self, answer=None, delay=0.0):
        self.answer, self.delay, self.calls, self.active, self.max_active = answer, delay, [], 0, 0
        self._lock = threading.Lock()

    def translate(self, question, today):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.calls.append(question)
            time.sleep(self.delay)
            return self.answer
        finally:
            with self._lock:
                self.active -= 1

    def is_loaded(self):
        return True


class EngineCase(unittest.TestCase):
    def make(self, llm=None, pages=None, **kwargs):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = os.path.join(folder.name, "t.db")
        conn = db.connect(path)
        ingest.ingest_pages(conn, pages if pages is not None else fx.synthetic_pages(), "c")
        db.set_meta(conn, "consent_valid_until", "2027-05-14")
        conn.close()
        self.path = path
        return Engine(path, llm=llm, clock=lambda: TODAY, **kwargs)


class AskTest(EngineCase):
    def test_pregunta_por_reglas_con_cifras_reales(self):
        engine = self.make()
        got = engine.ask("¿Cuánto gasté en octubre?")
        self.assertEqual((got["status"], got["source"]), ("answered", "rules"))
        conn = db.connect(self.path, read_only=True)
        direct = Executor(conn).run(got["interpretation"]["query"])
        conn.close()
        self.assertEqual(got["answer"]["data"]["value"], direct["value"])
        self.assertGreater(direct["value"], 0)
        self.assertIn("Has gastado", got["answer"]["text"])
        self.assertEqual(got["interpretation"]["chips"], ["1 oct – 31 oct 2026"])

    def test_el_resultado_cuadra_con_un_calculo_manual(self):
        engine = self.make(pages=fx.pages([fx.tx("2026-10-02", "10.10", "DBIT", fx.card_purchase("MERCADONA")),
                                           fx.tx("2026-10-03", "0.20", "DBIT", fx.card_purchase("LIDL")),
                                           fx.tx("2026-10-04", "5.00", "CRDT", "BIZUM DE ANA CONCEPTO x")]))
        self.assertEqual(engine.ask("¿Cuánto gasté en octubre?")["answer"]["data"], {"value": 10.30, "unit": "EUR"})
        self.assertEqual(engine.ask("¿Cuánto me han ingresado en octubre?")["answer"]["data"]["value"], 5.00)
        self.assertEqual(engine.ask("¿Cuántas veces gasté en octubre?")["answer"]["data"], {"value": 2, "unit": "count"})

    def test_listado_y_ultimo(self):
        engine = self.make()
        got = engine.ask("Muéstrame mis últimos 10 movimientos")
        self.assertEqual((got["answer"]["kind"], len(got["answer"]["data"]["rows"])), ("table", 10))
        last = engine.ask("¿Cuál fue mi último Bizum?")
        self.assertEqual(last["answer"]["kind"], "table")
        self.assertEqual(last["interpretation"]["query"]["category"], "bizum")

    def test_desglose_y_ranking_y_recurrentes(self):
        engine = self.make()
        by_cat = engine.ask("Dime el gasto por categorías de los últimos 30 días")
        self.assertEqual(by_cat["answer"]["kind"], "table")
        top = engine.ask("¿Dónde gasto más dinero?")
        self.assertEqual(top["answer"]["kind"], "table")
        rec = engine.ask("¿Qué suscripciones pago?")
        self.assertIn("NETFLIX.COM", [row[0] for row in rec["answer"]["data"]["rows"]])

    def test_comparacion(self):
        got = self.make().ask("¿Gasté más este mes que el mes pasado?")
        self.assertEqual(got["answer"]["kind"], "table")
        self.assertIn("Diferencia", got["answer"]["text"])

    def test_sin_modelo_lo_que_no_resuelven_las_reglas_pide_aclaracion(self):
        got = self.make(llm=None).ask("cuanto gaste en mercadona")
        self.assertEqual((got["status"], got["reason"], got["source"]), ("needs_clarification", "ambigua", "model"))
        self.assertEqual(got["answer"]["kind"], "none")

    def test_con_modelo_se_ejecuta_lo_que_traduce(self):
        fake = FakeLLM({"intent": "aggregate", "metric": "sum", "text": "mercadona"})
        got = self.make(llm=fake).ask("cuanto gaste en mercadona")
        self.assertEqual((got["status"], got["source"]), ("answered", "model"))
        self.assertEqual(fake.calls, ["cuanto gaste en mercadona"])
        self.assertEqual(got["interpretation"]["chips"], ["«mercadona»"])
        self.assertEqual(got["interpretation"]["query"]["direction"], "out")  # lo fija la pregunta («gasté»)

    def test_el_modelo_no_se_usa_si_las_reglas_resuelven(self):
        fake = FakeLLM({"intent": "balance"})
        self.make(llm=fake).ask("¿Cuánto gasté en octubre?")
        self.assertEqual(fake.calls, [])

    def test_salida_incoherente_o_nula_del_modelo_es_no_te_he_entendido(self):
        for answer in (None, {"intent": "aggregate"}, {"intent": "compare_periods", "metric": "sum"}):
            got = self.make(llm=FakeLLM(answer)).ask("cuanto gaste en mercadona")
            self.assertEqual((got["status"], got["reason"]), ("needs_clarification", "ambigua"), answer)

    def test_unknown_forzado_por_reglas(self):
        engine = self.make()
        self.assertEqual(engine.ask("Cancela mi suscripción de Spotify")["reason"], "operacion")
        self.assertEqual(engine.ask("¿Cuánto he gastado de media al día este mes?")["reason"], "fuera_de_alcance")

    def test_saldo_sin_datos_es_un_aviso_no_un_error(self):
        got = self.make().ask("¿Cuál es mi saldo?")
        self.assertEqual((got["status"], got["answer"]["kind"]), ("unsupported", "none"))
        self.assertIn("saldo", got["answer"]["text"])

    def test_saldo_con_datos(self):
        engine = self.make()
        conn = db.connect(self.path)
        ingest.ingest_balances(conn, {"balances": [{"balance_type": "CLBD", "balance_amount": {"currency": "EUR", "amount": "2345.67"}}]}, "c")
        conn.close()
        self.assertEqual(engine.ask("¿Cuál es mi saldo?")["answer"]["text"], "Tu saldo actual es 2.345,67 €.")

    def test_consulta_que_tarda_demasiado_se_aborta(self):
        heavy = fx.pages([fx.tx("2026-10-01", "1.00", "DBIT", fx.card_purchase(f"COMERCIO NUMERO {i}")) for i in range(5000)], per_page=5000)
        engine = self.make(llm=FakeLLM({"intent": "aggregate", "metric": "sum", "text": "zzzz yyyy"}), pages=heavy)
        original = engine_module.QUERY_SECONDS
        engine_module.QUERY_SECONDS = -1  # el plazo ya ha vencido: la búsqueda de texto recorre los 5000 movimientos
        self.addCleanup(setattr, engine_module, "QUERY_SECONDS", original)
        got = engine.ask("cuanto gaste en zzzz yyyy")
        self.assertEqual(got["status"], "unsupported")
        self.assertIn("tardado demasiado", got["answer"]["text"])

    def test_una_consulta_normal_no_se_aborta_con_el_plazo_real(self):
        got = self.make().ask("¿Cuánto gasté en octubre?")
        self.assertEqual(got["status"], "answered")

    def test_una_pregunta_a_la_vez_al_modelo(self):
        fake = FakeLLM({"intent": "aggregate", "metric": "sum"}, delay=0.05)
        engine = self.make(llm=fake)
        threads = [threading.Thread(target=engine.ask, args=("cuanto gaste en mercadona",)) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual((len(fake.calls), fake.max_active), (4, 1))

    def test_modelo_ocupado_da_503(self):
        fake = FakeLLM({"intent": "aggregate", "metric": "sum"}, delay=0.5)
        engine = self.make(llm=fake)
        original = engine_module.MODEL_QUEUE_SECONDS
        engine_module.MODEL_QUEUE_SECONDS = 0.05
        self.addCleanup(setattr, engine_module, "MODEL_QUEUE_SECONDS", original)
        first = threading.Thread(target=engine.ask, args=("cuanto gaste en mercadona",))
        first.start()
        time.sleep(0.1)
        with self.assertRaises(ApiError) as caught:
            engine.ask("cuanto gaste en lidl")
        self.assertEqual((caught.exception.status, caught.exception.code), (503, "model_busy"))
        first.join()


class HistoryAndStatusTest(EngineCase):
    def test_historial_sin_cifras_y_con_feedback(self):
        engine = self.make()
        got = engine.ask("¿Cuánto gasté en octubre?")
        conn = db.connect(self.path, read_only=True)
        row = conn.execute("SELECT question, source, status, query_json, verdict FROM history WHERE answer_id = ?", (got["id"],)).fetchone()
        conn.close()
        self.assertEqual((row["question"], row["source"], row["status"], row["verdict"]), ("¿Cuánto gasté en octubre?", "rules", "answered", None))
        self.assertNotIn(str(got["answer"]["data"]["value"]), row["query_json"])
        self.assertTrue(engine.feedback(got["id"], "wrong", "quería septiembre"))
        conn = db.connect(self.path, read_only=True)
        self.assertEqual(tuple(conn.execute("SELECT verdict, correction FROM history WHERE answer_id = ?", (got["id"],)).fetchone()), ("wrong", "quería septiembre"))
        conn.close()
        self.assertFalse(engine.feedback("ans_000000000000", "ok", None))

    def test_el_historial_esta_acotado(self):
        engine = self.make(history_limit=5)
        for _ in range(12):
            engine.ask("¿Cuál es mi saldo?")
        conn = db.connect(self.path, read_only=True)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM history").fetchone()[0], 5)
        conn.close()

    def test_estado(self):
        status = self.make().status()
        self.assertEqual(check(status, server.SCHEMAS["Status"]), [])
        self.assertEqual((status["mode"], status["data"]["accounts"], status["consent"]["valid_until"]), ("live", 1, "2027-05-14"))
        self.assertEqual(status["consent"]["days_left"], (date(2027, 5, 14) - TODAY).days)
        self.assertEqual(status["data"]["data_through"], "2026-11-14")

    def test_estado_con_modelo(self):
        status = self.make(llm=FakeLLM()).status()
        self.assertEqual(status["model"], {"name": "modelo-falso", "loaded": True})
        self.assertEqual(self.make(llm=None).status()["model"], {"name": "ninguno", "loaded": False})

    def test_base_vacia_no_rompe(self):
        engine = self.make(pages=fx.pages([]))
        self.assertEqual(engine.status()["data"]["data_through"], None)
        got = engine.ask("¿Cuánto gasté en octubre?")
        self.assertEqual(got["answer"]["data"]["value"], 0.0)


class ApiIntegrationTest(EngineCase):
    """El motor real detrás de la API real: todo el dataset debe cumplir el contrato."""

    def test_todo_el_dataset_por_http(self):
        engine = self.make()
        httpd = server.make_server(engine, "tok", port=0, quiet=True)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        count, statuses = 0, set()
        for line in DATASET.read_text(encoding="utf-8").splitlines():
            question = json.loads(line)["question"]
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("POST", "/api/ask", json.dumps({"question": question}).encode(), {"Authorization": "Bearer tok", "Content-Type": "application/json"})
            response = conn.getresponse()
            body = json.loads(response.read())
            conn.close()
            self.assertEqual(response.status, 200, (question, body))
            self.assertEqual(check(body, server.SCHEMAS["AskResponse"]), [], question)
            self.assertNotIn("mock", body)
            statuses.add(body["status"])
            count += 1
        self.assertGreaterEqual(count, 60)
        self.assertEqual(statuses, {"answered", "needs_clarification", "unsupported"})


class OllamaClientTest(unittest.TestCase):
    def serve(self, chat_reply, ps=None, delay=0.0):
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                time.sleep(delay)
                if chat_reply == "caido":
                    self.connection.close()
                    return
                payload = json.dumps(chat_reply).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                payload = json.dumps(ps or {"models": []}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return f"http://127.0.0.1:{httpd.server_address[1]}", seen

    def test_traduce_y_valida(self):
        host, seen = self.serve({"message": {"content": json.dumps({"intent": "balance"})}})
        client = llm_module.OllamaClient(host=host, timeout=5)
        self.assertEqual(client.translate("¿Cuál es mi saldo?", TODAY), {"intent": "balance"})
        body = seen["body"]
        self.assertEqual(body["model"], "qwen2.5:3b")
        self.assertEqual(body["keep_alive"], -1)  # número, no texto: Ollama rechaza "-1" ("missing unit in duration")
        self.assertIs(type(body["keep_alive"]), int)
        self.assertEqual(body["options"]["temperature"], 0)
        self.assertIn("properties", body["format"])
        self.assertNotIn("$schema", body["format"])
        self.assertEqual(body["messages"][-1], {"role": "user", "content": "¿Cuál es mi saldo?"})
        self.assertIn("2026-11-15", body["messages"][0]["content"])

    def test_no_envia_datos_bancarios(self):
        host, seen = self.serve({"message": {"content": "{}"}})
        llm_module.OllamaClient(host=host, timeout=5).translate("cuanto gaste en mercadona", TODAY)
        text = json.dumps(seen["body"], ensure_ascii=False)
        self.assertNotIn("EUR", text)
        self.assertNotRegex(text, r"\d{9,}")

    def test_respuestas_defectuosas_dan_none(self):
        for reply in ({"message": {"content": "no es json"}}, {"message": {"content": "[1,2]"}}, {"message": {"content": json.dumps({"intent": "inventado"})}},
                      {"message": {"content": json.dumps({"intent": "balance", "extra": 1})}}, {"sin": "message"}, "caido"):
            host, _ = self.serve(reply)
            self.assertIsNone(llm_module.OllamaClient(host=host, timeout=2).translate("x", TODAY), reply)

    def test_los_fallos_no_son_silenciosos(self):
        client = llm_module.OllamaClient(host="http://127.0.0.1:9", timeout=1)
        self.assertEqual((client.failures, client.last_error), (0, None))
        client.translate("pregunta con datos privados 123", TODAY)
        client.translate("otra", TODAY)
        self.assertEqual(client.failures, 2)
        self.assertNotIn("privados", client.last_error)  # no se guarda la pregunta
        host, _ = self.serve({"message": {"content": json.dumps({"intent": "inventado"})}})
        bad = llm_module.OllamaClient(host=host, timeout=2)
        bad.translate("x", TODAY)
        self.assertEqual((bad.failures, bad.last_error), (1, "la respuesta del modelo no cumple el esquema"))

    def test_http_400_se_registra(self):
        class Rejecting(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(400)
                self.send_header("Content-Length", "0")
                self.end_headers()

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Rejecting)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        client = llm_module.OllamaClient(host=f"http://127.0.0.1:{httpd.server_address[1]}", timeout=2)
        self.assertIsNone(client.translate("x", TODAY))
        self.assertIn("HTTPError", client.last_error)
        self.assertIn("400", client.last_error)

    def test_sin_servidor_da_none(self):
        self.assertIsNone(llm_module.OllamaClient(host="http://127.0.0.1:9", timeout=1).translate("x", TODAY))

    def test_solo_acepta_servidores_locales(self):
        for host in ("http://example.com:11434", "http://192.168.1.50:11434", "https://api.ejemplo.net", "http://127.0.0.1.evil.com"):
            with self.assertRaises(ValueError):
                llm_module.OllamaClient(host=host)
        for host in ("http://127.0.0.1:11434", "http://localhost:11434"):
            llm_module.OllamaClient(host=host)

    def test_el_calentamiento_tolera_un_arranque_lento(self):
        host, seen = self.serve({"message": {"content": json.dumps({"intent": "balance"})}}, delay=0.4)  # servidor lento
        client = llm_module.OllamaClient(host=host, timeout=0.1)       # plazo normal imposible de cumplir
        self.assertIsNone(client.translate("x", TODAY))                # la pregunta normal agota su plazo
        self.assertEqual(client.translate("x", TODAY, timeout=5), {"intent": "balance"})
        client.warm_up(TODAY)                                          # el calentamiento usa un plazo largo por defecto
        self.assertEqual(seen["body"]["messages"][-1]["content"], "¿Cuál es mi saldo?")

    def test_modelo_cargado(self):
        host, _ = self.serve({}, ps={"models": [{"name": "qwen2.5:3b"}]})
        self.assertTrue(llm_module.OllamaClient(host=host).is_loaded())
        self.assertFalse(llm_module.OllamaClient(model="otro", host=host).is_loaded())


if __name__ == "__main__":
    unittest.main()
