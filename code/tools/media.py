"""inspect_media: the cached image analysis or voice transcript, plus optional follow-up questions.

The cache is built once by `main.py perceive`. A follow-up question about an image
makes one more vision-model call, which the LLM client caches like any other.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

from llm import GroqError
from perception.media import image_data_url, vision_model

from .base import ToolContext, ToolInputError, clip, not_applicable, untrusted

QUESTION_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "media_question.md"
QUESTION_MAX_TOKENS = 400
QUESTION_LIMIT = 200
OCR_LIMIT, SUMMARY_LIMIT, TRANSCRIPT_LIMIT, ANSWER_LIMIT = 600, 300, 600, 400
LIST_ITEM_LIMIT = 60


def inspect_media(ctx: ToolContext, question: str = "") -> Dict[str, Any]:
    """What the attached image shows or the voice note says, from the media cache."""
    m = ctx.message
    if not m.media_type:
        return not_applicable("The message has no image or voice note.")
    if len(question) > QUESTION_LIMIT:
        raise ToolInputError(f"question must be at most {QUESTION_LIMIT} characters")

    result: Dict[str, Any] = {"media": f"{m.media_type}:{m.media_id}"}
    entry = ctx.media_entry(m.media_id)
    if entry is None:
        result["available"] = False
        result["note"] = "This media could not be analysed, so only the message text is available."
    elif m.media_type == "image":
        result.update(_image_facts(entry["analysis"]))
    else:
        result.update(_voice_facts(entry["analysis"]))

    if question.strip():
        result["question"] = clip(question, QUESTION_LIMIT)
        result.update(_ask(ctx, question))
    return result


def _image_facts(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kind": a.get("kind"),
        "summary": untrusted(a.get("summary", ""), SUMMARY_LIMIT),
        "text_in_image": untrusted(a.get("ocr_text", ""), OCR_LIMIT),
        "brands": [clip(x, LIST_ITEM_LIMIT) for x in a.get("brands", [])],
        "urls": [clip(x, LIST_ITEM_LIMIT) for x in a.get("urls", [])],
        "qr_present": a.get("qr_present"),
        "asks_for": a.get("asks_for", []),
        "deadlines": [clip(x, LIST_ITEM_LIMIT) for x in a.get("deadlines", [])],
        "language": a.get("language"),
    }


def _voice_facts(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "transcript": untrusted(a.get("transcript", ""), TRANSCRIPT_LIMIT),
        "language": a.get("language"),
        "duration_seconds": a.get("duration_seconds"),
        "tone": a.get("tone") or "not measured (speech-to-text gives words only)",
    }


def _ask(ctx: ToolContext, question: str) -> Dict[str, Any]:
    """One factual question about the image, answered by the vision model."""
    m = ctx.message
    if m.media_type != "image":
        return {"answer_note": "Voice notes only have a transcript; answer from it directly."}
    if ctx.client is None:
        return {"answer_note": "Follow-up questions are unavailable in this run."}
    prompt = QUESTION_PROMPT_PATH.read_text(encoding="utf-8")
    try:
        response = ctx.client.chat(
            model=vision_model(),
            messages=[{"role": "user", "content": [
                {"type": "text", "text": f"{prompt}\nQuestion: {question.strip()}"},
                {"type": "image_url", "image_url": {"url": image_data_url(ctx.store.media_path(m))}},
            ]}],
            max_tokens=QUESTION_MAX_TOKENS,
        )
    except GroqError as error:
        return {"answer_note": f"The follow-up question failed: {clip(str(error), 160)}"}
    answer = re.sub(r"<think>.*?</think>", "", ctx.client.message_text(response), flags=re.DOTALL)
    return {"answer": untrusted(answer, ANSWER_LIMIT)}
