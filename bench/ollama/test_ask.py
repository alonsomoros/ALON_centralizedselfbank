import json
import unittest

import ask
import validate as v


class AskTest(unittest.TestCase):
    def test_los_ejemplos_son_validos(self):
        for text, query in ask.SHOTS:
            self.assertEqual(v.check(query, v.SCHEMA), [], text)
            self.assertEqual(ask.contract.model_errors(query), [], text)

    def test_los_ejemplos_no_estan_en_el_dataset(self):
        questions = {item["question"].strip().lower() for _, item in v.load(ask.CONSULTAS / "questions.jsonl")}
        for text, _ in ask.SHOTS:
            self.assertNotIn(text.strip().lower(), questions)

    def test_ejemplos_no_traen_null(self):
        for _, query in ask.SHOTS:
            self.assertNotIn(None, query.values())

    def test_mensajes(self):
        messages = ask.build_messages("¿Qué tal?", "2026-11-15")
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("2026-11-15", messages[0]["content"])
        self.assertEqual(messages[-1], {"role": "user", "content": "¿Qué tal?"})
        self.assertEqual(len(messages), 2 + 2 * len(ask.SHOTS))
        for m in messages[2:-1:2]:
            json.loads(m["content"])

    def test_esquema_sin_meta(self):
        self.assertNotIn("$schema", ask.schema_for_ollama())
        self.assertIn("$schema", v.SCHEMA)  # no se modifica el original

    def test_el_prompt_lista_todos_los_periodos(self):
        for tag in ask.contract.PERIODS[:9]:
            self.assertIn(tag, ask.SYSTEM)
        for n in ask.contract.LAST_DAYS:
            self.assertIn(f"ultimos_{n}_dias", ask.SYSTEM)

    def test_interpret_tuberia_completa(self):
        from datetime import date
        raw = {"intent": "last_transaction", "reason": "ambigua"}  # reason sobra y se ignora
        full, errors = ask.interpret("¿Cuál fue mi último Bizum?", raw, date(2026, 11, 15))
        self.assertEqual(errors, [])
        self.assertEqual((full["intent"], full["category"], full["direction"]), ("last_transaction", "bizum", "any"))

    def test_interpret_rechaza_lo_irreparable(self):
        from datetime import date
        full, errors = ask.interpret("x", {"intent": "aggregate"}, date(2026, 11, 15))
        self.assertIsNone(full)
        self.assertTrue(errors)

    def test_ejemplos_en_json_compacto(self):
        for m in ask.build_messages("x", "2026-11-15")[2:-1:2]:
            self.assertNotIn(", ", m["content"])

    def test_compare(self):
        self.assertEqual(ask.compare({"a": 1, "b": 2}, {"a": 1, "b": 3}), ["b"])
        self.assertEqual(ask.compare({"a": 1}, {"a": 1}), [])


if __name__ == "__main__":
    unittest.main()
