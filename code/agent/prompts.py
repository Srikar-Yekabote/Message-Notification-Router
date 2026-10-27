"""The router's prompts: a fixed system prompt, and the per-message intake that follows it.

The system prompt and the tool definitions are identical on every call, so Groq
can cache them as a shared prefix; only the intake and tool results differ.
"""

from __future__ import annotations

from pathlib import Path

from context import ContextStore
from context.records import Message
from tools.base import clip, untrusted

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
INTAKE_TEXT_LIMIT = 1500


def build_system_prompt(prompts_dir: Path = PROMPTS_DIR) -> str:
    """router_system.md with the message types, routing policy and style examples filled in."""
    template = (prompts_dir / "router_system.md").read_text(encoding="utf-8")
    for name in ("types", "policy", "style_examples"):
        section = (prompts_dir / f"{name}.md").read_text(encoding="utf-8").strip()
        template = template.replace("{{" + name + "}}", section)
    return template


def intake(message: Message, store: ContextStore) -> str:
    """The incoming message as the router first sees it: plain facts, with the text marked untrusted."""
    m = message
    if m.business_id:
        sender = f"business account {m.business_id}"
    else:
        sender = f"user {m.sender_user_id}"
    lines = [
        "Route this incoming WhatsApp message for its recipient. Gather the facts you need with the tools, "
        "then reply with the JSON object.",
        "",
        f"message_id: {m.message_id}",
        f"received_at: {m.created_at:%Y-%m-%d %H:%M} ({m.created_at:%A})",
        f"recipient: user {m.user_id}",
        f"conversation: {m.conversation_type}",
        f"sender: {sender}",
    ]
    if m.group_id:
        group = store.groups.get(m.group_id)
        name = f' ("{clip(group.group_name, 60)}")' if group else ""
        lines.append(f"group: {m.group_id}{name}")
    if m.media_type:
        kind = "voice note" if m.media_type == "voice" else m.media_type
        lines.append(f"attachment: {kind} {m.media_id} (read it with inspect_media)")
    else:
        lines.append("attachment: none")
    lines.append(f"forwarded_count: {m.forwarded_count}")
    lines.append(f"text: {untrusted(m.text, INTAKE_TEXT_LIMIT) or '(no text)'}")
    return "\n".join(lines)
