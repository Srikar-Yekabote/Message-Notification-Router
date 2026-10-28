"""Tests for the blind-set scorer (evaluation only; the agents never see labels)."""

import unittest

from context import load_sample_labels
from evaluation.main import critic_effect, error_bucket, score, token_f1


def row(action, message_type, evidence="none", confidence="0.80", reason="A reason."):
    return {"action": action, "message_type": message_type, "reason": reason, "confidence": confidence,
            "evidence_message_ids": evidence}


class ScorerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.labels = load_sample_labels()
        cls.scam = cls.labels["sample_msg_053"]  # mute / scam

    def test_critic_effects_are_judged_against_the_label(self):
        wrong, right = row("notify", "urgent"), row("mute", "scam")
        self.assertEqual(critic_effect(wrong, right, self.scam), {"action": "fixed", "message_type": "fixed"})
        self.assertEqual(critic_effect(right, wrong, self.scam), {"action": "broke", "message_type": "broke"})
        self.assertEqual(critic_effect(right, right, self.scam)["action"], "unchanged")

    def test_score_counts_accuracy_evidence_and_buckets(self):
        expected_evidence = ";".join(self.scam.evidence_message_ids)
        traces = {
            "sample_msg_053": {"row": row("mute", "scam", expected_evidence + ";message_9999", "0.90"),
                               "critic": {"outcome": "revised", "before": row("notify", "urgent"), "reviews": []},
                               "models": ["router-model"]},
            "sample_msg_049": {"row": row("digest", "personal"), "models": ["router-model"]},
        }
        report = score(traces, self.labels)
        self.assertEqual(report["accuracy"]["router_action"], 1)
        self.assertEqual((report["accuracy"]["final_action"], report["accuracy"]["final_both"]), (2, 1))
        self.assertEqual(report["evidence"]["precision"], 0.5)
        self.assertEqual(report["changes"][0]["effect"]["action"], "fixed")
        self.assertEqual(report["buckets"], {"type confusion": ["sample_msg_049"]})
        self.assertEqual(report["by_model"]["router-model"]["n"], 2)
        self.assertEqual(report["calibration"]["0.86+"], [1, 1])

    def test_error_buckets_follow_the_first_problem(self):
        self.assertEqual(error_bucket(row("digest", "scam"), self.scam, True), "safety")
        self.assertEqual(error_bucket(row("mute", "spam"), self.scam, True), "type confusion")
        self.assertEqual(error_bucket(row("mute", "scam"), self.scam, False), "evidence")
        self.assertIsNone(error_bucket(row("mute", "scam"), self.scam, True))

    def test_token_f1(self):
        self.assertEqual(token_f1("a b c", "a b c"), 1.0)
        self.assertEqual(token_f1("a b", "c d"), 0.0)
        self.assertEqual(token_f1("A b", "a c"), 0.5)


if __name__ == "__main__":
    unittest.main()
