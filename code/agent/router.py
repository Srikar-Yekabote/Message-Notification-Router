"""The router agent: a ReAct loop over the tools on Groq, ending in one routing decision.

The model thinks, asks for facts, reads them, and repeats until it can decide.
Each turn it calls `gather` with the tools it wants, so one turn can fetch several
facts. Code only runs the loop, caps the number of turns, and checks the final
answer's format; every routing choice is the model's.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from context import ContextStore
from llm import REPO_ROOT, GroqError, RateLimitError
from perception import MEDIA_CACHE_DIR
from perception.media import parse_json_reply
from tools import GATHER, GATHER_DEFINITION, TOOL_NAMES, ToolContext, render, run_gather, run_tool
from tools.base import clip

from .guard import InvalidDecision, check_decision, fallback_row
from .prompts import build_system_prompt, intake

DEFAULT_ROUTER_MODEL = "openai/gpt-oss-120b"
DEFAULT_FALLBACK_MODEL = "openai/gpt-oss-20b"
DEFAULT_REASONING_EFFORT = "low"
MAX_TOOL_TURNS = 3
ROUTER_MAX_TOKENS = 2000
TRACES_DIR = REPO_ROOT / "traces"
JSON_NUDGE = "Reply now with your final decision: the JSON object only, in the format from your instructions."


class QuotaExhausted(Exception):
    """Every router model hit a lasting rate limit. Stop the run and resume later; finished work is cached."""


@dataclass
class RouteResult:
    row: Dict[str, str]
    trace: Dict[str, Any]

    @property
    def fell_back(self) -> bool:
        return self.trace.get("error") is not None


class Router:
    def __init__(
        self,
        store: ContextStore,
        client,
        model: Optional[str] = None,
        fallback_model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        max_tool_turns: int = MAX_TOOL_TURNS,
        traces_dir: Optional[Path] = TRACES_DIR,
        media_cache_dir: Path = MEDIA_CACHE_DIR,
    ):
        self.store = store
        self.client = client
        primary = model or os.environ.get("ROUTER_MODEL") or DEFAULT_ROUTER_MODEL
        fallback = fallback_model if fallback_model is not None else os.environ.get("ROUTER_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL)
        self.models = list(dict.fromkeys(m for m in (primary, fallback) if m))
        self.reasoning_effort = reasoning_effort or os.environ.get("ROUTER_REASONING_EFFORT") or DEFAULT_REASONING_EFFORT
        self.max_tool_turns = max_tool_turns
        self.traces_dir = Path(traces_dir) if traces_dir else None
        self.media_cache_dir = media_cache_dir
        self.system_prompt = build_system_prompt()
        self._model_index = 0  # moves to the fallback model once the primary's quota is spent

    @property
    def model(self) -> str:
        return self.models[self._model_index]

    def route(self, message_id: str) -> RouteResult:
        """Route one incoming message. Always returns a valid row, falling back if the agent fails."""
        message = self.store.message(message_id)
        ctx = ToolContext(self.store, message, self.client, self.media_cache_dir)
        trace: Dict[str, Any] = {"message_id": message_id, "models": [], "steps": [], "calls": [],
                                 "guard_notes": [], "error": None}
        started = time.monotonic()
        try:
            draft = self._run_loop(ctx, trace)
            trace["draft"] = draft
            row, trace["guard_notes"] = check_decision(draft, message, self.store)
        except QuotaExhausted as error:
            trace["error"] = f"QuotaExhausted: {error}"
            self._finish(trace, fallback_row(message_id), started)
            raise
        except (GroqError, InvalidDecision, ValueError) as error:
            trace["error"] = f"{type(error).__name__}: {clip(str(error), 300)}"
            row = fallback_row(message_id)
        self._finish(trace, row, started)
        return RouteResult(row, trace)

    # ---- the ReAct loop ------------------------------------------------

    def _run_loop(self, ctx: ToolContext, trace: Dict[str, Any]) -> Dict[str, Any]:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": intake(ctx.message, self.store)},
        ]
        turns, nudged = 0, False
        while True:
            final_turn = turns >= self.max_tool_turns
            try:
                reply = self._chat(messages, final_turn, trace)
            except GroqError as error:
                if final_turn or "tool call validation failed" not in str(error).lower():
                    raise
                # gpt-oss sometimes sends its answer as a call to a tool that does not exist, which
                # Groq rejects. Ask again with tools switched off so the answer comes back as text.
                trace.setdefault("retries", []).append(clip(str(error), 200))
                turns = self.max_tool_turns
                continue
            calls = reply.get("tool_calls") or []
            if calls and not final_turn:
                turns += 1
                messages.append(_assistant_turn(reply))
                for call in calls:
                    function = call.get("function") or {}
                    name, arguments = function.get("name", ""), function.get("arguments") or "{}"
                    result = _run_call(ctx, name, arguments)
                    trace["steps"].append({"turn": turns, "tool": name, "arguments": _loads(arguments), "result": result})
                    messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": render(result)})
                continue

            text = reply.get("content") or ""
            try:
                return parse_json_reply(text)
            except ValueError:
                if nudged:
                    raise ValueError(f"no JSON decision in the reply: {clip(text, 200)!r}")
                nudged, turns = True, self.max_tool_turns  # the next turn must answer
                messages.append({"role": "assistant", "content": text or "(no answer)"})
                messages.append({"role": "user", "content": JSON_NUDGE})

    def _chat(self, messages: List[Dict[str, Any]], final_turn: bool, trace: Dict[str, Any]) -> Dict[str, Any]:
        """One model call. On a lasting rate limit, switch to the fallback model for the rest of the run."""
        while True:
            model = self.model
            try:
                response = self.client.chat(
                    model=model,
                    messages=messages,
                    tools=[GATHER_DEFINITION],
                    tool_choice="none" if final_turn else "auto",
                    reasoning_effort=self.reasoning_effort,
                    max_tokens=ROUTER_MAX_TOKENS,
                )
            except RateLimitError as error:
                if self._model_index + 1 < len(self.models):
                    self._model_index += 1
                    trace.setdefault("model_switches", []).append(f"{model} -> {self.model}: {clip(str(error), 200)}")
                    continue
                raise QuotaExhausted(clip(str(error), 300)) from error
            if model not in trace["models"]:
                trace["models"].append(model)
            reply = response["choices"][0]["message"]
            trace["calls"].append({
                "model": model,
                "from_cache": bool(getattr(self.client, "last_from_cache", False)),
                "usage": response.get("usage") or {},
                "reasoning": clip(reply.get("reasoning") or "", 1500),
            })
            return reply

    # ---- traces --------------------------------------------------------

    def _finish(self, trace: Dict[str, Any], row: Dict[str, str], started: float) -> None:
        trace["row"] = row
        trace["usage"] = usage_totals(trace["calls"])
        trace["seconds"] = round(time.monotonic() - started, 1)
        if self.traces_dir:
            self.traces_dir.mkdir(parents=True, exist_ok=True)
            path = self.traces_dir / f"{trace['message_id']}.json"
            path.write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def usage_totals(calls: List[Dict[str, Any]]) -> Dict[str, int]:
    """Token use across a message's model calls. `counted_tokens` is what the free tier counts:
    new (uncached) prompt tokens plus all output, reasoning included."""
    totals = {"model_calls": len(calls), "live_calls": sum(1 for c in calls if not c["from_cache"]),
              "prompt_tokens": 0, "cached_prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
    for call in calls:
        usage = call["usage"]
        totals["prompt_tokens"] += usage.get("prompt_tokens", 0)
        totals["cached_prompt_tokens"] += (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        totals["completion_tokens"] += usage.get("completion_tokens", 0)
        totals["reasoning_tokens"] += (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
    totals["counted_tokens"] = totals["prompt_tokens"] - totals["cached_prompt_tokens"] + totals["completion_tokens"]
    return totals


def _run_call(ctx: ToolContext, name: str, arguments: str) -> Dict[str, Any]:
    """Run a gather call, or a single tool if the model names one directly."""
    if name == GATHER:
        return run_gather(ctx, arguments)
    if name in TOOL_NAMES:
        return run_tool(ctx, name, arguments)
    return {"error": f"unknown tool {name!r}; call {GATHER} with a list of requests"}


def _assistant_turn(reply: Dict[str, Any]) -> Dict[str, Any]:
    """The assistant's tool-calling turn, as it is sent back to the model."""
    turn: Dict[str, Any] = {"role": "assistant", "tool_calls": reply["tool_calls"]}
    if reply.get("content"):
        turn["content"] = reply["content"]
    return turn


def _loads(arguments: str) -> Any:
    try:
        return json.loads(arguments)
    except (TypeError, ValueError):
        return arguments
