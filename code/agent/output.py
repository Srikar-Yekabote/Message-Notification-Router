"""Write and check dataset/output.csv, the file that is submitted.

Rows follow the order of the blank template shipped in dataset/output.csv, or the
order of messages.csv when there is no usable template. `check_output` runs the
file part of the submission checklist (AGENTIC_WORKFLOW.md section 12).
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Dict, List

from context import ContextStore

from .guard import ACTIONS, MESSAGE_TYPES, OUTPUT_COLUMNS


def output_path(store: ContextStore) -> Path:
    return store.data.root / "output.csv"


def row_order(store: ContextStore, path: Path) -> List[str]:
    """The template's row order if it lists exactly the messages to predict, else messages.csv order."""
    wanted = store.data.message_ids
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            template = [row.get("message_id", "") for row in csv.DictReader(f)]
        if len(template) == len(set(template)) and set(template) == set(wanted):
            return template
    return list(wanted)


def write_output(rows: Dict[str, Dict[str, str]], order: List[str], path: Path) -> None:
    """Write one row per message in `order`, with standard CSV quoting. Written atomically."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for message_id in order:
            writer.writerow({column: rows[message_id][column] for column in OUTPUT_COLUMNS})
    tmp.replace(path)


def check_output(path: Path, store: ContextStore) -> List[str]:
    """Every way the file breaks the output contract. An empty list means it is ready to submit."""
    with path.open(newline="", encoding="utf-8") as f:
        lines = list(csv.reader(f))
    if not lines:
        return ["the file is empty"]
    problems = []
    if tuple(lines[0]) != OUTPUT_COLUMNS:
        problems.append(f"columns are {lines[0]}, expected {list(OUTPUT_COLUMNS)}")
    wanted, seen = set(store.data.message_ids), Counter()
    for number, values in enumerate(lines[1:], start=2):
        if len(values) != len(OUTPUT_COLUMNS):
            problems.append(f"line {number}: {len(values)} fields, expected {len(OUTPUT_COLUMNS)}")
            continue
        row = dict(zip(OUTPUT_COLUMNS, values))
        message_id = row["message_id"]
        seen[message_id] += 1
        if message_id not in wanted:
            problems.append(f"line {number}: {message_id!r} is not in messages.csv")
            continue
        problems += [f"{message_id}: {p}" for p in _row_problems(row, store)]
    missing = sorted(wanted - set(seen))
    if missing:
        problems.append(f"{len(missing)} message(s) have no row: {', '.join(missing[:10])}")
    duplicated = sorted(m for m, count in seen.items() if count > 1)
    if duplicated:
        problems.append(f"duplicate rows for: {', '.join(duplicated)}")
    return problems


def _row_problems(row: Dict[str, str], store: ContextStore) -> List[str]:
    problems = []
    if row["action"] not in ACTIONS:
        problems.append(f"action {row['action']!r} is not allowed")
    if row["message_type"] not in MESSAGE_TYPES:
        problems.append(f"message_type {row['message_type']!r} is not allowed")
    if not row["reason"].strip() or "\n" in row["reason"]:
        problems.append("reason is empty or spans several lines")
    try:
        if not 0.0 <= float(row["confidence"]) <= 1.0:
            problems.append(f"confidence {row['confidence']} is outside 0-1")
    except ValueError:
        problems.append(f"confidence {row['confidence']!r} is not a number")
    if row["evidence_message_ids"] != "none":
        message = store.message(row["message_id"])
        for evidence_id in row["evidence_message_ids"].split(";"):
            past = store.history.get(evidence_id)
            if past is None or past.user_id != message.user_id or past.created_at >= message.created_at:
                problems.append(f"evidence {evidence_id!r} is not an earlier message in this user's history")
    return problems
