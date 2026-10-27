"""Shared pieces for the agent's tools: the per-message context and output helpers.

Tools return facts, never verdicts. Every string that came from a sender (message
text, text read from images, transcripts) is wrapped in <untrusted_content> tags,
so the agent can always tell data apart from instructions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from context import ContextStore
from context.loaders import TIMESTAMP_FORMAT
from context.records import Message
from perception import MEDIA_CACHE_DIR, load_media_entry

UNTRUSTED_OPEN, UNTRUSTED_CLOSE = "<untrusted_content>", "</untrusted_content>"
_UNTRUSTED_TAG = re.compile(r"<\s*/?\s*untrusted_content\s*>", re.IGNORECASE)

HISTORY_TEXT_LIMIT = 140
MEDIA_GIST_LIMIT = 120


class ToolInputError(ValueError):
    """The agent called a tool with arguments it cannot use. The message goes back to the agent."""


@dataclass
class ToolContext:
    """Everything a tool needs, bound to the one incoming message being routed.

    Because the tools are bound to the message, the agent cannot read another
    user's data or any history dated at or after the message.
    """

    store: ContextStore
    message: Message
    client: Any = None  # a GroqClient; only media follow-up questions make live calls
    media_cache_dir: Path = MEDIA_CACHE_DIR

    @classmethod
    def for_message(cls, store: ContextStore, message_id: str, client: Any = None,
                    media_cache_dir: Path = MEDIA_CACHE_DIR) -> "ToolContext":
        return cls(store, store.message(message_id), client, Path(media_cache_dir))

    @property
    def now(self) -> datetime:
        return self.message.created_at

    def history(self, sender_key: Optional[str] = None, group_id: Optional[str] = None) -> List[Message]:
        """The recipient's past messages before this one, oldest first."""
        return self.store.user_history(self.message.user_id, before=self.now, sender_key=sender_key, group_id=group_id)

    def media_entry(self, media_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return load_media_entry(media_id, self.media_cache_dir) if media_id else None


def untrusted(text: str, limit: int) -> str:
    """Clip sender-supplied text and wrap it in tags it cannot close early. Empty text stays empty."""
    text = clip(_UNTRUSTED_TAG.sub("[tag removed]", text or ""), limit)
    return f"{UNTRUSTED_OPEN}{text}{UNTRUSTED_CLOSE}" if text else ""


def clip(text: str, limit: int) -> str:
    """Collapse whitespace to single spaces and cut to `limit` characters."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def fmt(when: Optional[datetime]) -> Optional[str]:
    return when.strftime(TIMESTAMP_FORMAT) if when else None


def not_applicable(note: str) -> Dict[str, Any]:
    return {"applicable": False, "note": note}


def render(result: Dict[str, Any]) -> str:
    """Compact JSON, exactly as the agent receives it."""
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


def media_text(entry: Optional[Dict[str, Any]]) -> str:
    """The words a cached media entry contributes: image summary and text, or the transcript."""
    if not entry:
        return ""
    analysis = entry["analysis"]
    if entry["media_type"] == "voice":
        return analysis.get("transcript", "")
    return " ".join(part for part in (analysis.get("summary", ""), analysis.get("ocr_text", "")) if part)


def past_message(m: Message, ctx: ToolContext, text_limit: int = HISTORY_TEXT_LIMIT) -> Dict[str, Any]:
    """One history message in compact form, with how the recipient reacted to it."""
    d: Dict[str, Any] = {
        "id": m.message_id,
        "at": fmt(m.created_at),
        "from": m.sender_key,
        "conversation": m.group_id or m.conversation_type,
    }
    if m.text.strip():
        d["text"] = untrusted(m.text, text_limit)
    if m.media_type:
        d["media"] = f"{m.media_type}:{m.media_id}"
        gist = _media_gist(ctx.media_entry(m.media_id))
        if gist:
            d["media_gist"] = untrusted(gist, MEDIA_GIST_LIMIT)
    if m.reaction:
        d["user_reaction"] = m.reaction.describe()
        d["outcome"] = m.reaction.outcome
    return d


def _media_gist(entry: Optional[Dict[str, Any]]) -> str:
    if not entry:
        return ""
    analysis = entry["analysis"]
    return analysis.get("transcript" if entry["media_type"] == "voice" else "summary", "")
