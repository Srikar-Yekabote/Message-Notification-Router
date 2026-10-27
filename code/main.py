"""Command-line entry point for the message notification router.
just update from my side

Usage (from the repository root):
    python3 code/main.py check                 validate the dataset and print counts
    python3 code/main.py inspect MESSAGE_ID    print the context bundle for one message
    python3 code/main.py perceive              analyse images and voice notes into cache/media/
    python3 code/main.py tool MESSAGE_ID NAME  run one of the agent's tools ('all' runs every tool)
    python3 code/main.py route MESSAGE_ID ...  route messages with the router agent (--blind: the blind samples)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from agent import Critic, QuotaExhausted, Router
from context import load_store
from llm import REPO_ROOT, GroqClient, load_env_file
from perception import MEDIA_CACHE_DIR, pending_media, perceive_all
from tools import TOOL_NAMES, ToolContext, render, run_tool


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="WhatsApp message notification router")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="validate the dataset and print counts")
    inspect = commands.add_parser("inspect", help="print the context bundle for one incoming message")
    inspect.add_argument("message_id", help="an ID from messages.csv or sample_messages.csv")
    inspect.add_argument("--history", type=int, default=10, help="past messages to list per section (default 10)")
    perceive = commands.add_parser("perceive", help="analyse images and voice notes into cache/media/ (uses Groq)")
    perceive.add_argument("--force", action="store_true", help="redo items that are already cached")
    perceive.add_argument("--only", nargs="+", metavar="MEDIA_ID", help="limit to these media IDs")
    tool = commands.add_parser("tool", help="run one of the agent's tools for a message, as the agent would")
    tool.add_argument("message_id", help="an ID from messages.csv or sample_messages.csv")
    tool.add_argument("name", choices=TOOL_NAMES + ["all"], help="the tool to run, or 'all' for every tool")
    tool.add_argument("arguments", nargs="?", default="{}",
                      help="""the tool's arguments as JSON, e.g. '{"query": "refund", "scope": "all"}'""")
    route = commands.add_parser("route", help="route messages with the router agent (uses Groq); traces go to traces/")
    route.add_argument("message_ids", nargs="*", metavar="MESSAGE_ID", help="IDs from messages.csv or sample_messages.csv")
    route.add_argument("--blind", action="store_true", help="also route every blind-test sample")
    route.add_argument("--no-critic", action="store_true", help="skip the critic review (router only)")
    args = parser.parse_args(argv)

    load_env_file()
    store = load_store()
    if args.command == "check":
        return check(store)
    if args.command == "perceive":
        return perceive_media(store, args.force, args.only)
    if args.command == "tool":
        return run_tools(store, args.message_id, args.name, args.arguments)
    if args.command == "route":
        return route_messages(store, args.message_ids, args.blind, critic=not args.no_critic)
    return inspect_message(store, args.message_id, args.history)


def check(store) -> int:
    data = store.data
    print(f"dataset: {data.root}")
    for name, count in [
        ("users", len(data.users)),
        ("groups", len(data.groups)),
        ("group memberships", len(data.members)),
        ("businesses", len(data.businesses)),
        ("user-business relationships", len(data.relationships)),
        ("history messages", len(data.history)),
        ("  with a reaction", sum(1 for m in data.history if m.reaction)),
        ("incoming messages (messages + samples)", len(data.incoming)),
        ("daily load rows", len(data.daily_load)),
        ("images", len(data.images)),
        ("voice notes", len(data.voice_notes)),
    ]:
        print(f"  {name:<40} {count:>5}")
    problems = store.validate()
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nOK: all references resolve and every history message has a reaction.")
    return 0


def perceive_media(store, force: bool, only: Optional[List[str]]) -> int:
    total = len(store.data.images) + len(store.data.voice_notes)
    cached = total - len(pending_media(store))
    pending = pending_media(store, force=force, only=only)
    print(f"media cache: {MEDIA_CACHE_DIR}")
    print(f"{cached} of {total} items cached; {len(pending)} to analyse now")
    if not pending:
        return 0
    client = GroqClient()
    if not client.api_key:
        print("GROQ_API_KEY is not set. Copy .env.example to .env and add your key.", file=sys.stderr)
        return 2
    result = perceive_all(store, client, force=force, only=only)
    print(f"\nanalysed {len(result['analysed'])}, failed {len(result['failed'])}, live API calls {client.live_calls}")
    if result["failed"]:
        print("failed: " + ", ".join(result["failed"]) + " (rerun to retry just these)")
        return 1
    return 0


def run_tools(store, message_id: str, name: str, arguments: str) -> int:
    if message_id not in store.incoming:
        print(f"Unknown message ID: {message_id}", file=sys.stderr)
        return 2
    if name == "all" and arguments.strip() not in ("", "{}"):
        print("'all' runs every tool with its default arguments; name one tool to pass arguments.", file=sys.stderr)
        return 2
    ctx = ToolContext.for_message(store, message_id, client=GroqClient())
    failed = False
    for tool_name in TOOL_NAMES if name == "all" else [name]:
        result = run_tool(ctx, tool_name, {} if name == "all" else arguments)
        failed = failed or "error" in result
        size = len(render(result))
        print(f"== {tool_name}: {size} characters as the agent sees it (about {size // 4} tokens)")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if failed else 0


def route_messages(store, message_ids: List[str], blind: bool, critic: bool = True) -> int:
    if blind:
        split = json.loads((REPO_ROOT / "code" / "evaluation" / "sample_split.json").read_text(encoding="utf-8"))
        message_ids = list(dict.fromkeys(message_ids + split["blind_test"]))
    unknown = [m for m in message_ids if m not in store.incoming]
    if unknown or not message_ids:
        print(f"Unknown message ID(s): {', '.join(unknown)}" if unknown else "Name message IDs or pass --blind.",
              file=sys.stderr)
        return 2
    client = GroqClient()
    if not client.api_key:
        print("GROQ_API_KEY is not set, so only decisions already in cache/llm/ can be replayed.", file=sys.stderr)
    router = Router(store, client, critic=Critic(store, client) if critic else None)
    fallbacks, counted, critic_counted = 0, 0, 0
    for message_id in message_ids:
        try:
            result = router.route(message_id)
        except QuotaExhausted as error:
            print(f"\nStopped at {message_id}: every model for that role is rate limited ({error}).", file=sys.stderr)
            print("Rerun the same command later; finished messages come from the cache.", file=sys.stderr)
            return 3
        row, trace = result.row, result.trace
        usage = trace["usage"]
        counted += usage["counted_tokens"]
        fallbacks += result.fell_back
        review = trace.get("critic")
        critic_note = ""
        if review:
            critic_counted += review["usage"]["counted_tokens"]
            before, after = review["before"], review["after"]
            change = ("" if (before["action"], before["message_type"]) == (after["action"], after["message_type"])
                      else f": {before['action']}/{before['message_type']} -> {after['action']}/{after['message_type']}")
            critic_note = f", critic {review['outcome']}{change}"
        tools = sum(len((s["arguments"] or {}).get("requests", [])) if isinstance(s["arguments"], dict) else 1
                    for s in trace["steps"])
        source = "cached" if usage["live_calls"] == 0 else f"{usage['live_calls']} live call(s)"
        print(f"{message_id:<16} {row['action']:<7} {row['message_type']:<16} {row['confidence']}  "
              f"evidence={row['evidence_message_ids']}")
        print(f"{'':<16} {row['reason']}")
        print(f"{'':<16} [{len(trace['steps'])} gather turn(s), {tools} tool(s), {usage['counted_tokens']:,} tokens, "
              f"{source}, {', '.join(trace['models']) or 'no model'}{critic_note}]"
              + (f"  FALLBACK: {trace['error']}" if result.fell_back else ""))
    per_message = max(len(message_ids), 1)
    print(f"\nrouted {len(message_ids)}, fallbacks {fallbacks}, router tokens {counted:,} "
          f"(about {counted // per_message:,} per message)"
          + (f", critic tokens {critic_counted:,} (about {critic_counted // per_message:,} per message)" if critic else ""))
    return 1 if fallbacks else 0


def inspect_message(store, message_id: str, history_limit: int) -> int:
    if message_id not in store.incoming:
        print(f"Unknown message ID: {message_id}", file=sys.stderr)
        return 2
    print(json.dumps(store.bundle(message_id, history_limit=history_limit), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
