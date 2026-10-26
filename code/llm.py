"""Small client for Groq's OpenAI-compatible API, using only the standard library.

Configuration comes from environment variables, optionally loaded from a `.env`
file at the repository root or in code/ (see `.env.example`):
    GROQ_API_KEY    required for live calls; never logged or cached
    LLM_BASE_URL    default https://api.groq.com/openai/v1

Every successful call is cached on disk under cache/llm/, keyed by a hash of the
request. Reruns therefore return identical results, use no quota, and work
without an API key once the cache is filled.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_CACHE_DIR = REPO_ROOT / "cache" / "llm"
USER_AGENT = "message-notification-router/0.1"

# (method, url, headers, body) -> (status, response headers, response body)
Transport = Callable[[str, str, Dict[str, str], bytes], Tuple[int, Dict[str, str], bytes]]


class GroqError(Exception):
    """A request failed for a reason retrying will not fix, or retries ran out."""


ENV_FILES = (REPO_ROOT / ".env", REPO_ROOT / "code" / ".env")


def load_env_file(path: Optional[Path] = None) -> None:
    """Load KEY=VALUE lines from .env without overriding variables already set.

    With no path, reads <repo>/.env and then <repo>/code/.env, whichever exist.
    """
    for candidate in [Path(path)] if path else ENV_FILES:
        if candidate.exists():
            _load_env_lines(candidate)


def _load_env_lines(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class GroqClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        cache_dir: Optional[Path] = DEFAULT_CACHE_DIR,
        max_retries: int = 6,
        timeout: float = 120.0,
        transport: Optional[Transport] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get("GROQ_API_KEY", "")
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.max_retries = max_retries
        self.timeout = timeout
        self._transport = transport or self._urllib_transport
        self._sleep = sleep
        self.live_calls = 0
        self.last_rate_limits: Dict[str, str] = {}

    # ---- public calls --------------------------------------------------

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        response_format: Optional[Dict[str, Any]] = None,
        temperature: float = 0.0,
        seed: Optional[int] = 7,
        max_tokens: Optional[int] = None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """POST /chat/completions and return the parsed JSON response."""
        payload: Dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
        if seed is not None:
            payload["seed"] = seed
        if response_format is not None:
            payload["response_format"] = response_format
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        key = _hash("chat", payload)
        return self._cached(key, use_cache, lambda: self._post_json("/chat/completions", payload))

    def transcribe(
        self,
        path: Path,
        model: str,
        response_format: str = "verbose_json",
        temperature: float = 0.0,
        language: Optional[str] = None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """POST /audio/transcriptions with the audio file and return the parsed JSON response."""
        path = Path(path)
        fields = {"model": model, "response_format": response_format, "temperature": str(temperature)}
        if language:
            fields["language"] = language
        key = _hash("transcribe", {"fields": fields, "file_sha256": file_sha256(path)})
        return self._cached(key, use_cache, lambda: self._post_multipart("/audio/transcriptions", fields, path))

    @staticmethod
    def message_text(response: Dict[str, Any]) -> str:
        """The assistant text from a chat completion response."""
        return response["choices"][0]["message"].get("content") or ""

    # ---- caching -------------------------------------------------------

    def _cached(self, key: str, use_cache: bool, call: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if use_cache and path and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        response = call()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        return response

    # ---- HTTP ----------------------------------------------------------

    def _post_json(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        return self._request(endpoint, {"Content-Type": "application/json"}, body)

    def _post_multipart(self, endpoint: str, fields: Dict[str, str], path: Path) -> Dict[str, Any]:
        content_type, body = encode_multipart(fields, "file", path.name, path.read_bytes())
        return self._request(endpoint, {"Content-Type": content_type}, body)

    def _request(self, endpoint: str, headers: Dict[str, str], body: bytes) -> Dict[str, Any]:
        if not self.api_key:
            raise GroqError("GROQ_API_KEY is not set. Add it to .env (see .env.example) or the environment.")
        headers = {**headers, "Authorization": f"Bearer {self.api_key}", "User-Agent": USER_AGENT}
        url = self.base_url + endpoint
        for attempt in range(self.max_retries + 1):
            try:
                status, response_headers, data = self._transport("POST", url, headers, body)
            except OSError as error:  # URLError, connection resets and socket timeouts on Python 3.9+
                if attempt == self.max_retries:
                    raise GroqError(f"network error calling {endpoint}: {error}") from error
                self._sleep(min(2 ** attempt, 30))
                continue
            self.live_calls += 1
            self.last_rate_limits = {k: v for k, v in response_headers.items() if k.lower().startswith("x-ratelimit")}
            if 200 <= status < 300:
                return json.loads(data.decode("utf-8"))
            retryable = status >= 500 or (status == 429 and not _too_large(data))
            if retryable and attempt < self.max_retries:
                self._sleep(_retry_after(response_headers) or min(2 ** attempt, 30))
                continue
            raise GroqError(f"{endpoint} returned HTTP {status}: {_error_message(data)}")
        raise GroqError(f"{endpoint}: retries exhausted")

    def _urllib_transport(self, method: str, url: str, headers: Dict[str, str], body: bytes):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, dict(response.headers.items()), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers.items()), error.read()


def encode_multipart(fields: Dict[str, str], file_field: str, filename: str, content: bytes) -> Tuple[str, bytes]:
    """Build a multipart/form-data body. Returns (content type header, body)."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return f"multipart/form-data; boundary={boundary}", b"".join(parts)


def _hash(kind: str, payload: Dict[str, Any]) -> str:
    canonical = json.dumps({"kind": kind, "payload": payload}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _retry_after(headers: Dict[str, str]) -> Optional[float]:
    for key, value in headers.items():
        if key.lower() == "retry-after":
            try:
                return max(float(value), 0.5)
            except ValueError:
                return None
    return None


def _too_large(data: bytes) -> bool:
    """A 429 for a single request that exceeds a per-minute limit can never succeed by waiting."""
    return "request too large" in _error_message(data).lower()


def _error_message(data: bytes) -> str:
    try:
        error = json.loads(data.decode("utf-8")).get("error", {})
        return str(error.get("message", error))[:300]
    except (ValueError, AttributeError):
        return data[:300].decode("utf-8", "replace")
