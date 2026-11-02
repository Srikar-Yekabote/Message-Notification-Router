"""Tests for writing and checking output.csv (no network)."""

import csv
import tempfile
import unittest
from pathlib import Path

from agent import fallback_row
from agent.guard import OUTPUT_COLUMNS
from agent.output import check_output, row_order, write_output
from context import load_store


class OutputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = load_store()
        cls.ids = cls.store.data.message_ids
        first = cls.store.message(cls.ids[0])
        cls.own_evidence = cls.store.user_history(first.user_id, before=first.created_at)[0].message_id
        cls.other_evidence = next(m.message_id for m in cls.store.data.history if m.user_id != first.user_id)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "output.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self, **changes):
        rows = {m: fallback_row(m) for m in self.ids}
        rows[self.ids[0]] = {**rows[self.ids[0]], **changes}
        return rows

    def write(self, rows, order=None):
        write_output(rows, order or self.ids, self.path)
        return check_output(self.path, self.store)

    def test_the_dataset_has_110_messages_to_predict(self):
        self.assertEqual(len(self.ids), 110)
        self.assertTrue(all(m.startswith("msg_") for m in self.ids))

    def test_valid_rows_pass_and_tricky_reasons_survive_quoting(self):
        reason = 'A "trusted" admin, writing about Route B; act today.'
        problems = self.write(self.rows(action="notify", message_type="event", reason=reason, confidence="0.88",
                                        evidence_message_ids=self.own_evidence))
        self.assertEqual(problems, [])
        with self.path.open(newline="", encoding="utf-8") as f:
            first = next(csv.DictReader(f))
        self.assertEqual((first["reason"], first["evidence_message_ids"]), (reason, self.own_evidence))

    def test_row_order_follows_the_template(self):
        shuffled = list(reversed(self.ids))
        with self.path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(OUTPUT_COLUMNS)
            writer.writerows([m, "", "", "", "", ""] for m in shuffled)
        self.assertEqual(row_order(self.store, self.path), shuffled)
        self.path.write_text("message_id\nmsg_001\n", encoding="utf-8")  # not the full set: use messages.csv order
        self.assertEqual(row_order(self.store, self.path), list(self.ids))

    def test_bad_values_are_reported(self):
        cases = {
            "action": {"action": "later"},
            "message_type": {"message_type": "marketing"},
            "reason": {"reason": "line one\nline two"},
            "confidence": {"confidence": "1.4"},
            "evidence": {"evidence_message_ids": self.other_evidence},
        }
        for word, change in cases.items():
            problems = self.write(self.rows(**change))
            self.assertEqual(len(problems), 1, word)
            self.assertIn(word, problems[0])

    def test_missing_duplicate_and_unknown_rows_are_reported(self):
        rows = self.rows()
        order = self.ids[1:] + [self.ids[1]]
        problems = self.write(rows, order)
        self.assertTrue(any("have no row" in p for p in problems))
        self.assertTrue(any("duplicate" in p for p in problems))
        rows["msg_999"] = fallback_row("msg_999")
        problems = self.write(rows, self.ids + ["msg_999"])
        self.assertTrue(any("msg_999" in p and "not in messages.csv" in p for p in problems))

    def test_wrong_columns_are_reported(self):
        self.path.write_text("id,action\nmsg_023,notify\n", encoding="utf-8")
        self.assertTrue(any("columns" in p for p in check_output(self.path, self.store)))


if __name__ == "__main__":
    unittest.main()
