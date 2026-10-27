"""The critic agent: a second model family reviews each router decision, plus the tie-break.

The critic sees the message, a compact digest of the facts the router gathered, the
router's decision and the past messages it cited. It never sees the router's
reasoning, so it judges the decision on the facts alone. If it objects, the router
revises once; if it still objects, the tie-break in this module settles the row.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from context import ContextStore
from context.records import Message
from llm import GroqError, RateLimitError
from perception.media import parse_json_reply
from tools import TOOL_NAMES, ToolContext, render
from tools.base import UNTRUSTED_CLOSE, UNTRUSTED_OPEN, clip, past_message, untrusted

from .errors import QuotaExhausted
from .guard import MESSAGE_TYPES
from .prompts import PROMPTS_DIR, message_facts

DEFAULT_CRITIC_MODEL = "qwen/qwen3.8-27b"
# Groq's free tier caps this model at 1,000 output tokens a minute; a review needs about 150.
CRITIC_MAX_TOKENS = 500
FACT_TEXT_LIMIT, FACT_LIST_LIMIT = 120, 4
OBJECTION_CONFIDENCE_CAP = 0.77
TIE_BREAK_CONFIDENCE_CAP = 0.70
RISK_TYPES = ("scam", "spam")
TIE_BREAK_RISK_REASON = "The reviewers disagreed and saw signs of a scam or unwanted bait, so the message is muted as a precaution."
TIE_BREAK_DIGEST_REASON = "The reviewers disagreed about how urgent this is, so it waits for the digest instead of interrupting the user."
JSON_NUDGE = "Reply with the JSON object only, in the format from your instructions."


class CriticUnavailable(Exception):
    """No usable review could be obtained (an API error or no valid JSON). The router's row stands."""


@dataclass
class Review:
    verdict: str
    safety_risk: bool
    issues: List[str] = field(default_factory=list)
    missing_facts: List[str] = field(default_factory=list)
    suggested_type: Optional[str] = None
    notes: str = ""

    @property
    def agrees(self) -> bool:
        return self.verdict == "agree"

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Critic:
    def __init__(self, store: ContextStore, client, model: Optional[str] = None,
                 fallback_model: Optional[str] = None, prompts_dir: Path = PROMPTS_DIR):
        self.store = store
        self.client = client
        primary = model or os.environ.get("CRITIC_MODEL") or DEFAULT_CRITIC_MODEL
        # No default fallback: a critic from the router's own model family would share its blind spots.
        fallback = fallback_model if fallback_model is not None else os.environ.get("CRITIC_FALLBACK_MODEL", "")
        self.models = list(dict.fromkeys(m for m in (primary, fallback) if m))
        self.system_prompt = (prompts_dir / "critic_system.md").read_text(encoding="utf-8")
        self._model_index = 0

    @property
    def model(self) -> str:
        return self.models[self._model_index]

    def review(self, message: Message, steps: List[Dict[str, Any]], row: Dict[str, str],
               calls: List[Dict[str, Any]]) -> Review:
        """Review one decision. Appends each model call's usage to `calls`."""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": critic_input(self.store, message, steps, row)},
        ]
        problem = ""
        for _ in range(2):
            text = self._chat(messages, calls)
            try:
                return parse_review(text)
            except ValueError as error:
                problem = str(error)
                messages = messages + [{"role": "assistant", "content": text or "(no answer)"},
                                       {"role": "user", "content": JSON_NUDGE}]
        raise CriticUnavailable(f"no valid review: {problem}")

    def _chat(self, messages: List[Dict[str, Any]], calls: List[Dict[str, Any]]) -> str:
        while True:
            model = self.model
            try:
                response = self.client.chat(model=model, messages=messages, response_format={"type": "json_object"},
                                            max_tokens=CRITIC_MAX_TOKENS)
            except RateLimitError as error:
                if self._model_index + 1 < len(self.models):
                    self._model_index += 1
                    continue
                raise QuotaExhausted(f"critic: {clip(str(error), 300)}") from error
            except GroqError as error:
                raise CriticUnavailable(clip(str(error), 300)) from error
            calls.append({"model": model, "from_cache": bool(getattr(self.client, "last_from_cache", False)),
                          "usage": response.get("usage") or {}})
            return self.client.message_text(response)


def critic_input(store: ContextStore, message: Message, steps: List[Dict[str, Any]], row: Dict[str, str]) -> str:
    """What the critic is shown: the message, a digest of the router's facts, its decision and evidence."""
    ctx = ToolContext(store, message)
    facts = facts_digest(steps)
    tools = list(dict.fromkeys(f["tool"] for f in facts))
    decision = {k: v for k, v in row.items() if k != "message_id"}
    decision["confidence"] = float(row["confidence"])
    evidence = []
    for message_id in row["evidence_message_ids"].split(";"):
        past = store.history.get(message_id)
        if past is not None:
            evidence.append(render(past_message(past, ctx, FACT_TEXT_LIMIT)))
    return "\n".join(
        ["Review this routing decision.", "", "## The message"] + message_facts(message, store)
        + ["", f"## Facts the router gathered (tools used: {', '.join(tools) or 'none'})"]
        + [render(f) for f in facts]
        + ["", "## The router's decision", json.dumps(decision, ensure_ascii=False),
           "", "## The past messages it cited as evidence"] + (evidence or ["none"])
    )


def facts_digest(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each tool result the router saw, with long text clipped and long lists shortened."""
    facts = []
    for step in steps:
        result = step.get("result") or {}
        if step.get("tool") == "gather" and "results" in result:
            facts += [_compact(r) for r in result["results"]]
        else:
            facts.append({"tool": step.get("tool"), **_compact(result)})
    return facts


def parse_review(text: str) -> Review:
    raw = parse_json_reply(text)
    verdict = str(raw.get("verdict", "")).strip().lower()
    if verdict not in ("agree", "object"):
        raise ValueError(f"verdict {raw.get('verdict')!r} is not agree or object")
    suggested = str(raw.get("suggested_type") or "").strip().lower()
    return Review(
        verdict=verdict,
        safety_risk=raw.get("safety_risk") is True or str(raw.get("safety_risk")).lower() == "true",
        issues=[clip(str(i), 200) for i in _as_list(raw.get("issues"))][:5],
        missing_facts=[t for t in _as_list(raw.get("missing_facts")) if t in TOOL_NAMES],
        suggested_type=suggested if suggested in MESSAGE_TYPES else None,
        notes=clip(str(raw.get("notes") or ""), 300),
    )


def revision_request(review: Review) -> str:
    """The objection as the router reads it before revising."""
    lines = ["A reviewer checked your decision against the facts and objected:"]
    lines += [f"- {issue}" for issue in review.issues] or ["- (no details given)"]
    if review.missing_facts:
        lines.append(f"The reviewer says you should also check: {', '.join(review.missing_facts)}.")
    lines.append("Weigh the objection against the facts. You may call gather once more if you need to. Then reply "
                 "with your final JSON decision, and keep your original decision if you still believe it is right.")
    return "\n".join(lines)


def cap_confidence(row: Dict[str, str], cap: float) -> Dict[str, str]:
    return {**row, "confidence": f"{min(float(row['confidence']), cap):.2f}"}


def tie_break(row: Dict[str, str], first_row: Dict[str, str], reviews: List[Review]) -> Dict[str, str]:
    """Settle a lasting disagreement: any safety risk → mute; otherwise digest. Confidence at most 0.70."""
    router_types = [row["message_type"], first_row["message_type"]]
    risky = any(r.safety_risk for r in reviews) or any(t in RISK_TYPES for t in router_types)
    if risky:
        candidates = router_types + [r.suggested_type for r in reviews]
        action, message_type = "mute", next((t for t in candidates if t in RISK_TYPES), "scam")
        keep_reason = (row["action"], row["message_type"]) == (action, message_type)
        reason = row["reason"] if keep_reason else TIE_BREAK_RISK_REASON
    else:
        action, message_type = "digest", row["message_type"]
        reason = row["reason"] if row["action"] == "digest" else TIE_BREAK_DIGEST_REASON
    settled = {**row, "action": action, "message_type": message_type, "reason": reason}
    return cap_confidence(settled, TIE_BREAK_CONFIDENCE_CAP)


def _compact(value: Any) -> Any:
    if isinstance(value, str):
        if value.startswith(UNTRUSTED_OPEN) and value.endswith(UNTRUSTED_CLOSE):
            return untrusted(value[len(UNTRUSTED_OPEN):-len(UNTRUSTED_CLOSE)], FACT_TEXT_LIMIT)
        return clip(value, FACT_TEXT_LIMIT)
    if isinstance(value, list):
        return [_compact(v) for v in value[:FACT_LIST_LIMIT]]
    if isinstance(value, dict):
        return {k: _compact(v) for k, v in value.items()}
    return value


def _as_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]
