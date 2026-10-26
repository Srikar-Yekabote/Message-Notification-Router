"""Turn images and voice notes into text the router can reason over, and cache the results.

Each media item gets one JSON file in cache/media/. The cache is shipped with the
code, so the router keeps working even if a hosted model is withdrawn, and reruns
spend no quota. An entry is rebuilt only when the file, the model or the prompt changes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from llm import GroqError, file_sha256

CODE_DIR = Path(__file__).resolve().parents[1]
MEDIA_CACHE_DIR = CODE_DIR.parent / "cache" / "media"
IMAGE_PROMPT_PATH = CODE_DIR / "prompts" / "image_perception.md"

DEFAULT_VISION_MODEL = "qwen/qwen3.8-27b"
DEFAULT_TRANSCRIBE_MODEL = "whisper-large-v3"
TRANSCRIBE_SETTINGS = "verbose_json|temperature=0"
# Groq's free tier caps the vision model at 1,000 output tokens per minute, and a request
# whose max_tokens exceeds that is rejected outright. A full reply needs about 500.
IMAGE_MAX_TOKENS = 700

IMAGE_KINDS = ("poster", "screenshot", "photo", "circular", "document", "other")
ASKS_FOR = ("payment", "otp", "login", "personal_details", "reply", "call", "visit_link", "scan_qr", "forward", "none")
MIME_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def vision_model() -> str:
    return os.environ.get("VISION_MODEL", DEFAULT_VISION_MODEL)


def transcribe_model() -> str:
    return os.environ.get("TRANSCRIBE_MODEL", DEFAULT_TRANSCRIBE_MODEL)


def load_media_entry(media_id: str, cache_dir: Path = MEDIA_CACHE_DIR) -> Optional[Dict[str, Any]]:
    path = Path(cache_dir) / f"{media_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def pending_media(
    store,
    cache_dir: Path = MEDIA_CACHE_DIR,
    force: bool = False,
    only: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Media items whose cache entry is missing or stale (file, model or prompt changed)."""
    prompt_version = _prompt_version(IMAGE_PROMPT_PATH.read_text(encoding="utf-8"))
    jobs = [("image", media_id, path) for media_id, path in sorted(store.data.images.items())]
    jobs += [("voice", media_id, path) for media_id, path in sorted(store.data.voice_notes.items())]
    pending = []
    for media_type, media_id, path in jobs:
        if only and media_id not in only:
            continue
        if media_type == "image":
            model, version = vision_model(), prompt_version
        else:
            model, version = transcribe_model(), _prompt_version(TRANSCRIBE_SETTINGS)
        sha = file_sha256(path)
        if force or not _is_current(load_media_entry(media_id, cache_dir), sha, model, version):
            pending.append({"media_type": media_type, "media_id": media_id, "path": path,
                            "sha": sha, "model": model, "version": version})
    return pending


def perceive_all(
    store,
    client,
    cache_dir: Path = MEDIA_CACHE_DIR,
    force: bool = False,
    only: Optional[List[str]] = None,
    log: Callable[[str], None] = print,
) -> Dict[str, List[str]]:
    """Analyse every image and voice note that is missing or stale in the cache.

    Returns the media IDs that were analysed and those that failed. A failure is
    logged and skipped, so one bad item never blocks the rest.
    """
    prompt = IMAGE_PROMPT_PATH.read_text(encoding="utf-8")
    result: Dict[str, List[str]] = {"analysed": [], "failed": []}
    for job in pending_media(store, cache_dir, force, only):
        media_type, media_id, path = job["media_type"], job["media_id"], job["path"]
        model, version, sha = job["model"], job["version"], job["sha"]
        try:
            if media_type == "image":
                analysis = analyse_image(client, path, model, prompt)
            else:
                analysis = transcribe_voice(client, path, model)
        except (GroqError, ValueError) as error:
            log(f"  {media_id}: FAILED ({error})")
            result["failed"].append(media_id)
            continue
        _write_entry(cache_dir, {
            "media_id": media_id,
            "media_type": media_type,
            "file": path.relative_to(store.data.root).as_posix(),
            "file_sha256": sha,
            "model": model,
            "prompt_version": version,
            "analysis": analysis,
        })
        log(f"  {media_id}: done")
        result["analysed"].append(media_id)
    return result


def analyse_image(client, path: Path, model: str, prompt: str) -> Dict[str, Any]:
    """Ask the vision model for a factual JSON description of one image."""
    mime = MIME_TYPES.get(Path(path).suffix.lower(), "image/jpeg")
    data_url = f"data:{mime};base64,{base64.b64encode(Path(path).read_bytes()).decode('ascii')}"
    instructions = [prompt, prompt + "\n\nYour previous reply was not valid JSON. Reply with the JSON object only."]
    last_error: Optional[ValueError] = None
    for text in instructions:
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]}],
            response_format={"type": "json_object"},
            max_tokens=IMAGE_MAX_TOKENS,
        )
        try:
            return normalise_image_analysis(parse_json_reply(client.message_text(response)))
        except ValueError as error:
            last_error = error
    raise ValueError(f"vision model did not return valid JSON: {last_error}")


def transcribe_voice(client, path: Path, model: str) -> Dict[str, Any]:
    """Speech-to-text for one voice note. Tone is not measured, so it is left as null."""
    response = client.transcribe(path, model=model, response_format="verbose_json", temperature=0.0)
    transcript = str(response.get("text", "")).strip()
    duration = response.get("duration")
    return {
        "transcript": transcript,
        "language": response.get("language"),
        "duration_seconds": round(float(duration), 2) if duration is not None else None,
        "tone": None,
    }


def parse_json_reply(text: str) -> Dict[str, Any]:
    """Extract the first JSON object from a model reply, ignoring reasoning tags and code fences."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in reply")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("reply JSON is not an object")
    return obj


def normalise_image_analysis(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce a model reply into the fixed schema, dropping unknown values."""
    kind = str(raw.get("kind", "other")).strip().lower()
    asks_for = [a for a in (_as_list(raw.get("asks_for")) or ["none"]) if a.lower() in ASKS_FOR]
    asks_for = [a.lower() for a in asks_for] or ["none"]
    if len(asks_for) > 1 and "none" in asks_for:
        asks_for.remove("none")
    return {
        "kind": kind if kind in IMAGE_KINDS else "other",
        "ocr_text": str(raw.get("ocr_text") or "").strip(),
        "summary": str(raw.get("summary") or "").strip(),
        "brands": _as_list(raw.get("brands")),
        "urls": _as_list(raw.get("urls")),
        "qr_present": _as_bool(raw.get("qr_present")),
        "asks_for": asks_for,
        "deadlines": _as_list(raw.get("deadlines")),
        "language": str(raw.get("language") or "").strip(),
    }


def _as_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return [str(value)]
    return [str(v).strip() for v in value if str(v).strip()]


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def _prompt_version(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _is_current(entry: Optional[Dict[str, Any]], sha: str, model: str, version: str) -> bool:
    return bool(entry) and (entry["file_sha256"], entry["model"], entry["prompt_version"]) == (sha, model, version)


def _write_entry(cache_dir: Path, entry: Dict[str, Any]) -> None:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{entry['media_id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
