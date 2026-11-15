import unittest
from collections import Counter
from pathlib import Path

import split
import validate as v


class SplitTest(unittest.TestCase):
    def setUp(self):
        self.items = [item for _, item in v.load(Path(v.HERE) / "questions.jsonl")]
        self.parts = split.assign(self.items)

    def test_cubre_todo_y_sin_solapes(self):
        self.assertEqual(set(self.parts), {i["id"] for i in self.items})
        self.assertEqual(set(self.parts.values()), {"dev", "test", "holdout"})

    def test_es_determinista(self):
        self.assertEqual(self.parts, split.assign(list(reversed(self.items))))

    def test_tamanos_razonables(self):
        counts = Counter(self.parts.values())
        self.assertGreaterEqual(counts["test"], 14)
        self.assertGreaterEqual(counts["dev"], 22)

    def test_unknown_en_ambos(self):
        unknown = {i["id"]: self.parts[i["id"]] for i in self.items if i["expected"]["intent"] == "unknown"}
        self.assertEqual(set(unknown.values()), {"dev", "test"})

    def test_intents_principales_en_ambos(self):
        for intent in ("aggregate", "list_transactions", "top_merchants"):
            got = {self.parts[i["id"]] for i in self.items if i["expected"]["intent"] == intent} - {"holdout"}
            self.assertEqual(got, {"dev", "test"}, intent)

    def test_holdout_explicito_no_altera_el_resto(self):
        base = split.assign([i for i in self.items if "split" not in i])
        self.assertEqual({k: x for k, x in self.parts.items() if x != "holdout"}, base)

    def test_hay_holdout(self):
        self.assertGreaterEqual(sum(1 for x in self.parts.values() if x == "holdout"), 5)

    def test_un_intent_unico_va_a_desarrollo(self):
        self.assertEqual(split.assign([{"id": "a", "expected": {"intent": "balance"}}]), {"a": "dev"})


if __name__ == "__main__":
    unittest.main()
