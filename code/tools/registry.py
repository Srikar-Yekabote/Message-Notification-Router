"""The tool definitions the router agent sees, and one entry point that runs a tool call.

Definitions use the OpenAI function-calling format that Groq accepts. Their
parameter schemas are also used to check the agent's arguments, so a bad call
comes back to the agent as an error it can correct instead of crashing the run.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Union

from .base import ToolContext, ToolInputError
from .history import MAX_LIMIT, SCOPES, search_history
from .media import QUESTION_LIMIT, inspect_media
from .profiles import business_relationship, group_context, sender_profile, user_profile
from .safety import safety_scan

_TOOLS: Dict[str, tuple] = {
    "user_profile": (
        user_profile,
        "Facts about the recipient: quiet hours and whether this message falls inside them, "
        "30-day activity, reactions to all past messages, and recent notification load.",
        {},
    ),
    "sender_profile": (
        sender_profile,
        "Facts about who sent this message and how the recipient reacted to them before. "
        "A person: past messages to the recipient and their group role. A business: verified flag, "
        "official and sender domains, domain and account age, 30-day volume and reports.",
        {},
    ),
    "group_context": (
        group_context,
        "For a group message: the group's type, size and activity, the recipient's role, mute setting "
        "and reactions in it, the sender's role, and who the message @mentions.",
        {},
    ),
    "business_relationship": (
        business_relationship,
        "For a business message: whether and why the recipient knows this business, last activity, "
        "promotion opt-in or opt-out, and 30-day opens, dismissals and replies.",
        {},
    ),
    "search_history": (
        search_history,
        "Search the recipient's own past messages (all dated before this one) and see how the recipient "
        "reacted to each. Result IDs are the only valid evidence IDs.",
        {
            "query": {"type": "string", "description": "Keywords to match. Empty lists the most recent messages."},
            "scope": {"type": "string", "enum": list(SCOPES),
                      "description": "same_sender (default), same_group, or all of the recipient's history."},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "description": "Results to return (default 5)."},
        },
    ),
    "inspect_media": (
        inspect_media,
        "The attached image's analysis (text in the image, summary, brands, links, QR code, what it asks for) "
        "or the voice note's transcript. Optionally ask one factual question about the image.",
        {"question": {"type": "string", "maxLength": QUESTION_LIMIT,
                      "description": "Optional factual question about the image."}},
    ),
    "safety_scan": (
        safety_scan,
        "Risk-related facts from the message text, image text and transcript: links and how their domains "
        "compare with official ones, mentions of codes, passwords, card details or payment, deadline pressure, "
        "requests to forward, and text that addresses the router.",
        {},
    ),
}

TOOL_NAMES: List[str] = list(_TOOLS)

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": params, "additionalProperties": False},
        },
    }
    for name, (_, description, params) in _TOOLS.items()
]


def run_tool(ctx: ToolContext, name: str, arguments: Union[str, Dict[str, Any], None] = None) -> Dict[str, Any]:
    """Run one tool call. Bad input comes back as {"error": ...} for the agent to read."""
    if name not in _TOOLS:
        return {"error": f"unknown tool {name!r}; available: {', '.join(TOOL_NAMES)}"}
    function, _, params = _TOOLS[name]
    try:
        args = _parse_arguments(arguments)
        _check_arguments(args, params)
        return function(ctx, **args)
    except ToolInputError as error:
        return {"error": f"{name}: {error}"}


def _parse_arguments(arguments: Union[str, Dict[str, Any], None]) -> Dict[str, Any]:
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            raise ToolInputError("arguments are not valid JSON")
    if not isinstance(arguments, dict):
        raise ToolInputError("arguments must be a JSON object")
    return arguments


def _check_arguments(args: Dict[str, Any], params: Dict[str, Dict[str, Any]]) -> None:
    for key, value in args.items():
        spec = params.get(key)
        if spec is None:
            allowed = ", ".join(params) or "none"
            raise ToolInputError(f"unexpected argument {key!r}; allowed: {allowed}")
        if spec["type"] == "string" and not isinstance(value, str):
            raise ToolInputError(f"{key} must be a string")
        if spec["type"] == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            raise ToolInputError(f"{key} must be an integer")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolInputError(f"{key} must be one of {', '.join(spec['enum'])}")
