"""Score routed samples against their labels, from the traces the router wrote.

Evaluation only: the labels are read here and never reach the agents.

Usage (from the repository root):
    python3 code/main.py route --blind     # route the blind set first
    python3 code/evaluation/main.py        # then score it (or pass sample IDs)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from context import load_sample_labels  # noqa: E402
from llm import REPO_ROOT  # noqa: E402

SPLIT_PATH = CODE_DIR / "evaluation" / "sample_split.json"
TRACES_DIR = REPO_ROOT / "traces"


def critic_effect(before: Dict[str, str], after: Dict[str, str], label) -> Dict[str, str]:
    """How the critic changed one decision, judged separately on action and on type."""
    effect = {}
    for field, expected in (("action", label.action), ("message_type", label.message_type)):
        was, now = before[field] == expected, after[field] == expected
        if before[field] == after[field]:
            effect[field] = "unchanged"
        elif now and not was:
            effect[field] = "fixed"
        elif was and not now:
            effect[field] = "broke"
        else:
            effect[field] = "changed, still wrong"
    return effect


def score(traces: Dict[str, Dict[str, Any]], labels: Dict[str, Any]) -> Dict[str, Any]:
    """Accuracy before and after the critic, plus every decision the critic changed."""
    report: Dict[str, Any] = {"scored": len(traces), "outcomes": {}, "changes": []}
    hits = {"router_action": 0, "router_type": 0, "final_action": 0, "final_type": 0}
    for message_id, trace in sorted(traces.items()):
        label, final = labels[message_id], trace["row"]
        review = trace.get("critic") or {}
        before = review.get("before", final)
        hits["router_action"] += before["action"] == label.action
        hits["router_type"] += before["message_type"] == label.message_type
        hits["final_action"] += final["action"] == label.action
        hits["final_type"] += final["message_type"] == label.message_type
        outcome = review.get("outcome", "no critic")
        report["outcomes"][outcome] = report["outcomes"].get(outcome, 0) + 1
        if review and outcome != "accepted":
            report["changes"].append({
                "message_id": message_id, "outcome": outcome,
                "before": f"{before['action']}/{before['message_type']}",
                "after": f"{final['action']}/{final['message_type']}",
                "expected": f"{label.action}/{label.message_type}",
                "effect": critic_effect(before, final, label),
                "issues": [i for r in review.get("reviews", []) for i in r.get("issues", [])],
            })
    report["accuracy"] = hits
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Score routed samples from their traces")
    parser.add_argument("message_ids", nargs="*", help="sample IDs to score (default: the blind set)")
    args = parser.parse_args(argv)
    ids = args.message_ids or json.loads(SPLIT_PATH.read_text(encoding="utf-8"))["blind_test"]
    labels = load_sample_labels()
    traces = {}
    for message_id in ids:
        path = TRACES_DIR / f"{message_id}.json"
        if message_id in labels and path.exists():
            traces[message_id] = json.loads(path.read_text(encoding="utf-8"))
    missing = [m for m in ids if m not in traces]
    if not traces:
        print("No traces to score. Route the samples first: python3 code/main.py route --blind", file=sys.stderr)
        return 2

    report = score(traces, labels)
    n, acc = report["scored"], report["accuracy"]
    print(f"scored {n} sample(s)" + (f"; no trace or label for: {', '.join(missing)}" if missing else ""))
    print(f"  action accuracy: router {acc['router_action']}/{n}, after critic {acc['final_action']}/{n}")
    print(f"  type accuracy:   router {acc['router_type']}/{n}, after critic {acc['final_type']}/{n}")
    print("  critic outcomes: " + ", ".join(f"{k} {v}" for k, v in sorted(report["outcomes"].items())))
    for field in ("action", "message_type"):
        effects = [c["effect"][field] for c in report["changes"]]
        print(f"  critic effect on {field}: fixed {effects.count('fixed')}, broke {effects.count('broke')}, "
              f"changed but still wrong {effects.count('changed, still wrong')}")
    for c in report["changes"]:
        print(f"\n  {c['message_id']} ({c['outcome']}): {c['before']} -> {c['after']}, expected {c['expected']}")
        for issue in c["issues"]:
            print(f"    - {issue}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
