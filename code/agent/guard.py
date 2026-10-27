"""Output guard: turn the agent's draft into a valid output row, or a fallback row.

The guard checks format only. It never changes the agent's action; if the action
is unusable, the whole row falls back. Every change it makes is noted in the trace.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from context import ContextStore
from context.records import Message
from tools.base import clip

ACTIONS = ("notify", "digest", "mute")
MESSAGE_TYPES = (
    "personal", "urgent", "event", "payment", "business_update", "promotion",
    "greeting", "forward", "spam", "scam", "unknown",
)
OUTPUT_COLUMNS = ("message_id", "action", "message_type", "reason", "confidence", "evidence_message_ids")
CONFIDENCE_CEILING = 0.92
MAX_EVIDENCE = 3
EVIDENCE_PENALTY = 0.10
REASON_LIMIT = 300
FALLBACK_REASON = "Could not be analysed; held for later review."
_TAGS = re.compile(r"<\s*/?\s*untrusted_content\s*>", re.IGNORECASE)
# Typographic dashes and quotes become plain ASCII, so the CSV reads the same in any tool.
_PLAIN_PUNCTUATION = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
                                    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})


class InvalidDecision(ValueError):
    """The draft cannot be turned into a row, so a fallback row is used instead."""


def fallback_row(message_id: str) -> Dict[str, str]:
    return {
        "message_id": message_id,
        "action": "digest",
        "message_type": "unknown",
        "reason": FALLBACK_REASON,
        "confidence": "0.50",
        "evidence_message_ids": "none",
    }


def check_decision(draft: Dict[str, Any], message: Message, store: ContextStore) -> Tuple[Dict[str, str], List[str]]:
    """Validate a draft decision. Returns the output row and notes on anything the guard changed."""
    notes: List[str] = []
    action = str(draft.get("action", "")).strip().lower()
    if action not in ACTIONS:
        raise InvalidDecision(f"action {draft.get('action')!r} is not one of {', '.join(ACTIONS)}")

    message_type = str(draft.get("message_type", "")).strip().lower()
    if message_type not in MESSAGE_TYPES:
        notes.append(f"message_type {draft.get('message_type')!r} is not allowed; used 'unknown'")
        message_type = "unknown"

    reason = clip(_TAGS.sub("", str(draft.get("reason") or "")).translate(_PLAIN_PUNCTUATION), REASON_LIMIT)
    if not reason:
        raise InvalidDecision("reason is empty")

    try:
        confidence = float(draft.get("confidence"))
    except (TypeError, ValueError):
        notes.append(f"confidence {draft.get('confidence')!r} is not a number; used 0.50")
        confidence = 0.50
    if not 0.0 <= confidence <= CONFIDENCE_CEILING:
        notes.append(f"confidence {confidence} clamped to 0-{CONFIDENCE_CEILING}")
        confidence = min(max(confidence, 0.0), CONFIDENCE_CEILING)

    cited = _evidence_ids(draft.get("evidence_message_ids"))
    valid = [i for i in cited if _is_own_past_message(i, message, store)][:MAX_EVIDENCE]
    dropped = [i for i in cited if i not in valid]
    if dropped:
        notes.append(f"dropped evidence not in this user's earlier history (or over {MAX_EVIDENCE}): {', '.join(dropped)}")
    if cited and not valid:
        confidence = max(confidence - EVIDENCE_PENALTY, 0.0)
        notes.append(f"no valid evidence left; confidence lowered by {EVIDENCE_PENALTY:.2f}")

    row = {
        "message_id": message.message_id,
        "action": action,
        "message_type": message_type,
        "reason": reason,
        "confidence": f"{confidence:.2f}",
        "evidence_message_ids": ";".join(valid) or "none",
    }
    return row, notes


def _evidence_ids(value: Any) -> List[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else re.split(r"[;,\s]+", str(value))
    ids = [str(i).strip().strip("`'\"") for i in items]
    return list(dict.fromkeys(i for i in ids if i and i.lower() != "none"))


def _is_own_past_message(message_id: str, message: Message, store: ContextStore) -> bool:
    past = store.history.get(message_id)
    return bool(past and past.user_id == message.user_id and past.created_at < message.created_at)
