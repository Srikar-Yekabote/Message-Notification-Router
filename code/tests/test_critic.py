"""Tests for the critic, the revision loop and the tie-break, using scripted model replies (no network)."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent import Critic, QuotaExhausted, Review, Router
from agent.critic import (OBJECTION_CONFIDENCE_CAP, TIE_BREAK_CONFIDENCE_CAP, TIE_BREAK_DIGEST_REASON,
                          TIE_BREAK_RISK_REASON, critic_input, facts_digest, parse_review, tie_break)
from context import load_store
from llm import GroqError, RateLimitError
from tools.base import UNTRUSTED_CLOSE, UNTRUSTED_OPEN

USAGE = {"prompt_tokens": 100, "completion_tokens": 10}


class ScriptedClient:
    """Scripted replies per model; records every request."""

    def __init__(self, replies, rate_limited=()):
        self.replies = {model: list(items) for model, items in replies.items()}
        self.rate_limited = set(rate_limited)
        self.requests = []
        self.last_from_cache = False

    def chat(self, model, messages, **kwargs):
        if model in self.rate_limited:
            raise RateLimitError("tokens per day (TPD) limit reached")
        self.requests.append({"model": model, "messages": copy.deepcopy(messages), **kwargs})
        reply = self.replies[model].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {"choices": [{"message": reply}], "usage": USAGE}

    @staticmethod
    def message_text(response):
        return response["choices"][0]["message"].get("content") or ""

    def cached_chat(self, model, messages, **kwargs):
        return None

    def sent_to(self, model):
        return [r for r in self.requests if r["model"] == model]


def gather(*requests):
    return {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function", "function": {
        "name": "gather", "arguments": json.dumps({"requests": list(requests)})}}]}


def decision(**fields):
    base = {"action": "notify", "message_type": "business_update", "reason": "A delivery update the user awaits.",
            "confidence": 0.88, "evidence_message_ids": "message_0032"}
    return {"role": "assistant", "content": json.dumps({**base, **fields})}


def review(verdict="agree", **fields):
    base = {"verdict": verdict, "safety_risk": False, "issues": [], "missing_facts": [], "suggested_type": None,
            "notes": ""}
    return {"role": "assistant", "content": json.dumps({**base, **fields})}


OBJECTION = review("object", issues=["2: the sender's domain is a look-alike of amazon.in"],
                   missing_facts=["safety_scan"])


class CriticFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = load_store()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def route(self, router_replies, critic_replies, message_id="msg_026", rate_limited=()):
        client = ScriptedClient({"router": router_replies, "critic": critic_replies}, rate_limited)
        critic = Critic(self.store, client, model="critic", fallback_model="")
        router = Router(self.store, client, model="router", fallback_model="", traces_dir=Path(self.tmp.name),
                        critic=critic)
        return router.route(message_id), client

    def test_agreement_keeps_the_router_decision(self):
        result, client = self.route([gather({"tool": "sender_profile"}), decision()], [review()])
        self.assertEqual((result.row["action"], result.row["confidence"]), ("notify", "0.88"))
        self.assertEqual(result.trace["critic"]["outcome"], "accepted")
        request = client.sent_to("critic")[0]
        self.assertEqual(request["response_format"], {"type": "json_object"})
        shown = request["messages"][1]["content"]
        self.assertIn("tools used: sender_profile", shown)
        self.assertIn('"action": "notify"', shown)
        self.assertIn('"id":"message_0032"', shown)
        self.assertNotIn("reasoning", shown)

    def test_objection_leads_to_one_revision_with_capped_confidence(self):
        result, client = self.route(
            [gather({"tool": "sender_profile"}), decision(),
             gather({"tool": "safety_scan"}), decision(action="mute", message_type="scam", confidence=0.9)],
            [OBJECTION, review()])
        self.assertEqual((result.row["action"], result.row["message_type"]), ("mute", "scam"))
        self.assertEqual(result.row["confidence"], f"{OBJECTION_CONFIDENCE_CAP:.2f}")
        self.assertEqual(result.trace["critic"]["outcome"], "revised")
        revision_turn = client.sent_to("router")[2]["messages"]
        self.assertEqual(revision_turn[-2]["role"], "assistant")  # the first answer stays in the conversation
        self.assertIn("look-alike", revision_turn[-1]["content"])
        self.assertIn("safety_scan", revision_turn[-1]["content"])
        self.assertIn("tools used: sender_profile, safety_scan", client.sent_to("critic")[1]["messages"][1]["content"])

    def test_lasting_disagreement_without_risk_goes_to_digest(self):
        result, _ = self.route([decision(), decision()], [review("object", issues=["3: weak evidence"])] * 2)
        self.assertEqual((result.row["action"], result.row["reason"]), ("digest", TIE_BREAK_DIGEST_REASON))
        self.assertEqual(result.row["confidence"], f"{TIE_BREAK_CONFIDENCE_CAP:.2f}")
        self.assertEqual(result.trace["critic"]["outcome"], "tie_break")

    def test_lasting_disagreement_with_risk_is_muted(self):
        risky = review("object", safety_risk=True, issues=["1: asks for a fee"], suggested_type="scam")
        result, _ = self.route([decision(), decision()], [risky, risky])
        self.assertEqual((result.row["action"], result.row["message_type"]), ("mute", "scam"))
        self.assertEqual(result.row["reason"], TIE_BREAK_RISK_REASON)

    def test_failed_revision_goes_to_the_tie_break(self):
        no_json = {"role": "assistant", "content": "Let me think."}
        result, _ = self.route([decision(), no_json, no_json], [OBJECTION])
        self.assertEqual(result.trace["critic"]["outcome"], "tie_break")
        self.assertIn("revision_error", result.trace["critic"])
        self.assertFalse(result.fell_back)

    def test_unavailable_critic_leaves_the_router_decision(self):
        result, _ = self.route([decision()], [GroqError("HTTP 500")])
        self.assertEqual(result.trace["critic"]["outcome"], "unavailable")
        self.assertEqual(result.row["action"], "notify")
        bad = {"role": "assistant", "content": "I agree."}
        result, _ = self.route([decision()], [bad, bad])
        self.assertEqual(result.trace["critic"]["outcome"], "unavailable")

    def test_critic_quota_stops_the_run(self):
        with self.assertRaises(QuotaExhausted):
            self.route([decision()], [], rate_limited={"critic"})

    def test_trace_records_critic_usage(self):
        result, _ = self.route([decision()], [review()])
        self.assertEqual(result.trace["critic"]["usage"]["model_calls"], 1)
        self.assertEqual(result.trace["critic"]["usage"]["counted_tokens"], 110)


class CriticPartsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = load_store()

    def test_parse_review_cleans_the_reply(self):
        parsed = parse_review('{"verdict": "OBJECT", "safety_risk": "true", "issues": "1: wrong", '
                              '"missing_facts": ["safety_scan", "make_coffee"], "suggested_type": "Scam"}')
        self.assertEqual((parsed.verdict, parsed.safety_risk, parsed.issues), ("object", True, ["1: wrong"]))
        self.assertEqual((parsed.missing_facts, parsed.suggested_type), (["safety_scan"], "scam"))
        with self.assertRaises(ValueError):
            parse_review('{"verdict": "maybe"}')

    def test_facts_digest_clips_text_and_lists_but_keeps_tags(self):
        steps = [{"tool": "gather", "result": {"results": [
            {"tool": "search_history", "results": [{"text": f"{UNTRUSTED_OPEN}{'x' * 500}{UNTRUSTED_CLOSE}"}] * 9}]}}]
        (facts,) = facts_digest(steps)
        self.assertEqual(len(facts["results"]), 4)
        text = facts["results"][0]["text"]
        self.assertTrue(text.startswith(UNTRUSTED_OPEN) and text.endswith(UNTRUSTED_CLOSE))
        self.assertLess(len(text), 160)

    def test_critic_input_shows_cited_evidence(self):
        message = self.store.message("msg_026")
        row = {"message_id": "msg_026", "action": "mute", "message_type": "scam", "reason": "r",
               "confidence": "0.90", "evidence_message_ids": "message_0032"}
        shown = critic_input(self.store, message, [], row)
        self.assertIn("tools used: none", shown)
        self.assertIn("reported it", shown)

    def test_tie_break_keeps_a_matching_router_reason(self):
        row = {"message_id": "m", "action": "mute", "message_type": "spam", "reason": "Loan bait.",
               "confidence": "0.85", "evidence_message_ids": "none"}
        settled = tie_break(row, row, [Review("object", False)])
        self.assertEqual((settled["action"], settled["message_type"], settled["reason"]), ("mute", "spam", "Loan bait."))
        self.assertEqual(settled["confidence"], "0.70")

    def test_critic_prompt_has_the_checks_and_no_sample_ids(self):
        prompt = Critic(self.store, client=None).system_prompt
        self.assertIn("# Your six checks", prompt)
        self.assertNotIn("sample_msg_", prompt)


if __name__ == "__main__":
    unittest.main()
