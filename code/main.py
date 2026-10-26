"""Command-line entry point for the message notification router.
just update from my side

Usage (from the repository root):
    python3 code/main.py check                 validate the dataset and print counts
    python3 code/main.py inspect MESSAGE_ID    print the context bundle for one message
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from context import load_store


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="WhatsApp message notification router")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="validate the dataset and print counts")
    inspect = commands.add_parser("inspect", help="print the context bundle for one incoming message")
    inspect.add_argument("message_id", help="an ID from messages.csv or sample_messages.csv")
    inspect.add_argument("--history", type=int, default=10, help="past messages to list per section (default 10)")
    args = parser.parse_args(argv)

    store = load_store()
    if args.command == "check":
        return check(store)
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


def inspect_message(store, message_id: str, history_limit: int) -> int:
    if message_id not in store.incoming:
        print(f"Unknown message ID: {message_id}", file=sys.stderr)
        return 2
    print(json.dumps(store.bundle(message_id, history_limit=history_limit), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
