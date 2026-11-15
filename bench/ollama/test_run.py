import json
import unittest

import run

ITEM = {
    "id": "t1", "today": "2026-11-15", "question": "¿Cuánto gasté en restaurantes en octubre?",
    "expected": {"intent": "aggregate", "metric": "sum", "direction": "out", "category": "restaurantes", "period": "octubre"},
}
UNKNOWN = {
    "id": "t2", "today": "2026-11-15", "question": "¿Qué tal?",
    "expected": {"intent": "unknown", "reason": "fuera_de_alcance"},
}


def reply(**fields):
    return json.dumps(fields)


class ScoreTest(unittest.TestCase):
    def test_exacta(self):
        s = run.score(ITEM, reply(**ITEM["expected"]))
        self.assertTrue(s["json_ok"] and s["schema_ok"] and s["intent_ok"] and s["exact"])

    def test_json_invalido(self):
        s = run.score(ITEM, "no es json")
        self.assertFalse(s["json_ok"] or s["intent_ok"] or s["exact"])

    def test_no_es_objeto(self):
        self.assertFalse(run.score(ITEM, "[1, 2]")["json_ok"])

    def test_intent_bien_pero_campo_distinto(self):
        s = run.score(ITEM, reply(intent="aggregate", metric="sum", direction="out", category="restaurantes", period="septiembre"))
        self.assertTrue(s["intent_ok"] and s["schema_ok"])
        self.assertFalse(s["exact"])
        self.assertEqual(sorted(s["diff"]), ["from", "to"])

    def test_campos_sobrantes_se_ignoran(self):
        s = run.score(ITEM, reply(**ITEM["expected"], compare_period="hoy", reason="ambigua"))
        self.assertTrue(s["schema_ok"] and s["exact"])

    def test_ranking_sin_direccion_cuenta_como_gasto(self):
        item = {"id": "r", "today": "2026-11-15", "question": "¿Dónde gasto más?",
                "expected": {"intent": "top_merchants", "direction": "out"}}
        self.assertTrue(run.score(item, reply(intent="top_merchants"))["exact"])

    def test_incoherente_cuenta_como_intent_pero_no_como_esquema(self):
        s = run.score(ITEM, reply(intent="aggregate", period="octubre"))  # falta metric
        self.assertTrue(s["json_ok"] and s["intent_ok"])
        self.assertFalse(s["schema_ok"] or s["exact"])

    def test_palabra_clave_completa_la_categoria(self):
        item = {"id": "b", "today": "2026-11-15", "question": "¿Cuál fue mi último Bizum?",
                "expected": {"intent": "last_transaction", "category": "bizum"}}
        self.assertTrue(run.score(item, reply(intent="last_transaction"))["exact"])

    def test_unknown_con_otra_reason_es_exacta_pero_reason_mal(self):
        s = run.score(UNKNOWN, reply(intent="unknown", reason="ambigua"))
        self.assertTrue(s["exact"] and s["intent_ok"])
        self.assertFalse(s["reason_ok"])

    def test_unknown_reason_correcta(self):
        self.assertTrue(run.score(UNKNOWN, reply(intent="unknown", reason="fuera_de_alcance"))["reason_ok"])

    def test_reason_none_si_no_es_unknown(self):
        self.assertIsNone(run.score(ITEM, reply(**ITEM["expected"]))["reason_ok"])


class StatsTest(unittest.TestCase):
    def test_percentil(self):
        data = list(range(1, 101))
        self.assertEqual(run.percentile(data, 50), 50)
        self.assertEqual(run.percentile(data, 95), 95)
        self.assertEqual(run.percentile([3.0], 95), 3.0)
        self.assertIsNone(run.percentile([], 50))

    def row(self, **kw):
        base = {"json_ok": True, "schema_ok": True, "intent_ok": True, "exact": True, "reason_ok": None,
                "total_s": 2.0, "gen_tokens": 20, "gen_s": 2.0, "temp_c": 55.0, "throttled": "0x0",
                "mem_available_mib": 4500, "ollama_rss_mib": 1800}
        base.update(kw)
        return base

    def test_resumen(self):
        rows = [self.row(), self.row(exact=False, intent_ok=False, total_s=4.0, temp_c=60.0)]
        s = run.summarize(rows)
        self.assertEqual((s["n"], s["intent_pct"], s["exact_pct"]), (2, 50.0, 50.0))
        self.assertEqual((s["total_max_s"], s["temp_max_c"], s["gen_tok_s_mean"]), (4.0, 60.0, 10.0))
        self.assertFalse(s["throttled_any"])

    def test_resumen_detecta_throttling(self):
        self.assertTrue(run.summarize([self.row(throttled="0x50005")])["throttled_any"])

    def test_resumen_tolera_falta_de_sensores(self):
        s = run.summarize([self.row(temp_c=None, throttled=None, mem_available_mib=None, ollama_rss_mib=None)])
        self.assertIsNone(s["temp_max_c"])
        self.assertFalse(s["throttled_any"])

    def test_resumen_vacio(self):
        self.assertEqual(run.summarize([]), {"n": 0})

    def test_criterio_pasa(self):
        s = run.summarize([self.row()] * 5)
        self.assertTrue(run.passes(s)["PASA"])

    def test_criterio_falla_por_intent_latencia_ram_y_throttling(self):
        for kw in ({"intent_ok": False}, {"total_s": 9.0}, {"mem_available_mib": 500}, {"throttled": "0x4"}):
            s = run.summarize([self.row(**kw)] * 5)
            self.assertFalse(run.passes(s)["PASA"], kw)


if __name__ == "__main__":
    unittest.main()
