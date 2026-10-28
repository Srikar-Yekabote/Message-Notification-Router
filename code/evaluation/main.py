"""Score routed samples against their labels, from the traces the router wrote.

Evaluation only: the labels are read here and never reach the agents.

Usage (from the repository root):
    python3 code/main.py route --blind                         # route the blind set first
    python3 code/evaluation/main.py                            # then score it (or pass sample IDs)
    python3 code/evaluation/main.py --record "what changed"    # also append the scores to runs.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from context import load_sample_labels, load_store  # noqa: E402
from llm import REPO_ROOT  # noqa: E402

SPLIT_PATH = CODE_DIR / "evaluation" / "sample_split.json"
RUNS_LOG = CODE_DIR / "evaluation" / "runs.jsonl"
TRACES_DIR = REPO_ROOT / "traces"
PROMPT_FILES = ("router_system.md", "policy.md", "types.md", "style_examples.md", "critic_system.md")
RISK_TYPES = {"scam", "spam"}
CONFIDENCE_BANDS = (("0.86+", 0.86), ("0.78-0.85", 0.78), ("below 0.78", 0.0))
_WORD = re.compile(r"[a-z0-9]+")


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


def evidence_ids(value: str) -> Set[str]:
    return {i for i in str(value or "").split(";") if i and i != "none"}


def token_f1(predicted: str, expected: str) -> float:
    """Plain word overlap between two reasons, as an F1 score from 0 to 1."""
    a, b = Counter(_WORD.findall(predicted.lower())), Counter(_WORD.findall(expected.lower()))
    overlap = sum((a & b).values())
    if not overlap:
        return 0.0
    precision, recall = overlap / sum(a.values()), overlap / sum(b.values())
    return round(2 * precision * recall / (precision + recall), 3)


def error_bucket(row: Dict[str, str], label, evidence_hit: bool) -> Optional[str]:
    """The first thing wrong with a row, in the order of the error buckets in AGENTIC_WORKFLOW.md section 9."""
    risky = bool({label.message_type, row["message_type"]} & RISK_TYPES)
    if row["action"] != label.action:
        if risky:
            return "safety"
        return "urgency" if "notify" in (label.action, row["action"]) else "personalization"
    if row["message_type"] != label.message_type:
        return "type confusion"
    if not evidence_hit:
        return "evidence"
    return None


def score(traces: Dict[str, Dict[str, Any]], labels: Dict[str, Any], media_ids: Set[str] = frozenset()) -> Dict[str, Any]:
    """Every section 9 metric for the given traces, plus each row's details and what the critic changed."""
    report: Dict[str, Any] = {"scored": len(traces), "outcomes": {}, "changes": [], "rows": [], "buckets": {},
                              "by_model": {}, "calibration": {name: [0, 0] for name, _ in CONFIDENCE_BANDS}}
    hits = {"router_action": 0, "router_type": 0, "final_action": 0, "final_type": 0, "final_both": 0}
    evidence = {"true_positive": 0, "predicted": 0, "expected": 0, "message_hits": 0}
    reason_scores, tokens = [], {"router": 0, "critic": 0}
    for message_id, trace in sorted(traces.items()):
        label, final = labels[message_id], trace["row"]
        review = trace.get("critic") or {}
        before = review.get("before", final)
        action_ok, type_ok = final["action"] == label.action, final["message_type"] == label.message_type
        hits["router_action"] += before["action"] == label.action
        hits["router_type"] += before["message_type"] == label.message_type
        hits["final_action"] += action_ok
        hits["final_type"] += type_ok
        hits["final_both"] += action_ok and type_ok

        predicted, expected = evidence_ids(final["evidence_message_ids"]), set(label.evidence_message_ids)
        evidence["true_positive"] += len(predicted & expected)
        evidence["predicted"] += len(predicted)
        evidence["expected"] += len(expected)
        evidence_hit = bool(predicted & expected) or (not predicted and not expected)
        evidence["message_hits"] += evidence_hit

        reason_scores.append(token_f1(final["reason"], label.reason))
        confidence = float(final["confidence"])
        band = next(name for name, floor in CONFIDENCE_BANDS if confidence >= floor)
        report["calibration"][band][0] += action_ok
        report["calibration"][band][1] += 1

        model = trace["models"][-1] if trace.get("models") else "none"
        stats = report["by_model"].setdefault(model, {"n": 0, "action": 0, "type": 0})
        stats["n"] += 1
        stats["action"] += action_ok
        stats["type"] += type_ok
        tokens["router"] += trace.get("usage", {}).get("counted_tokens", 0)
        tokens["critic"] += review.get("usage", {}).get("counted_tokens", 0)

        bucket = error_bucket(final, label, evidence_hit)
        if bucket:
            report["buckets"].setdefault(bucket, []).append(message_id)
        report["rows"].append({
            "message_id": message_id, "bucket": bucket, "media": message_id in media_ids, "model": model,
            "got": f"{final['action']}/{final['message_type']}", "expected": f"{label.action}/{label.message_type}",
            "evidence_got": sorted(predicted), "evidence_expected": sorted(expected),
            "confidence": confidence, "reason_f1": reason_scores[-1],
        })

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
    report["evidence"] = {
        "precision": round(evidence["true_positive"] / evidence["predicted"], 3) if evidence["predicted"] else None,
        "recall": round(evidence["true_positive"] / evidence["expected"], 3) if evidence["expected"] else None,
        "message_hits": evidence["message_hits"],
    }
    report["reason_f1"] = round(sum(reason_scores) / len(reason_scores), 3) if reason_scores else None
    report["tokens"] = tokens
    return report


def prompt_versions() -> Dict[str, str]:
    """Short hashes of the prompt files, so each recorded run says which prompts produced it."""
    return {name: hashlib.sha256((CODE_DIR / "prompts" / name).read_bytes()).hexdigest()[:10] for name in PROMPT_FILES}


def summary_record(report: Dict[str, Any], note: str) -> Dict[str, Any]:
    n, acc = report["scored"], report["accuracy"]
    return {
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "note": note,
        "scored": n,
        "action": f"{acc['final_action']}/{n}",
        "type": f"{acc['final_type']}/{n}",
        "both": f"{acc['final_both']}/{n}",
        "router_before_critic": {"action": f"{acc['router_action']}/{n}", "type": f"{acc['router_type']}/{n}"},
        "evidence": report["evidence"],
        "reason_f1": report["reason_f1"],
        "buckets": {k: len(v) for k, v in report["buckets"].items()},
        "critic": report["outcomes"],
        "by_model": report["by_model"],
        "tokens": report["tokens"],
        "prompts": prompt_versions(),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Score routed samples from their traces")
    parser.add_argument("message_ids", nargs="*", help="sample IDs to score (default: the blind set)")
    parser.add_argument("--record", metavar="NOTE", help="append this run's scores to code/evaluation/runs.jsonl")
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

    store = load_store()
    media_ids = {m for m in traces if store.message(m).media_type}
    report = score(traces, labels, media_ids)
    n, acc, ev = report["scored"], report["accuracy"], report["evidence"]
    print(f"scored {n} sample(s)" + (f"; no trace or label for: {', '.join(missing)}" if missing else ""))
    print(f"  action accuracy:   router {acc['router_action']}/{n}, after critic {acc['final_action']}/{n}")
    print(f"  type accuracy:     router {acc['router_type']}/{n}, after critic {acc['final_type']}/{n}")
    print(f"  action and type:   {acc['final_both']}/{n}")
    print(f"  evidence:          precision {ev['precision']}, recall {ev['recall']}, "
          f"rows with a matching ID (or both none) {ev['message_hits']}/{n}")
    print(f"  reason word F1:    {report['reason_f1']}")
    print("  calibration:       " + ", ".join(f"{band} {right}/{total} right" for band, (right, total)
                                               in report["calibration"].items() if total))
    print("  by router model:   " + ", ".join(f"{m} action {s['action']}/{s['n']}, type {s['type']}/{s['n']}"
                                               for m, s in report["by_model"].items()))
    print(f"  tokens counted:    router {report['tokens']['router']:,}, critic {report['tokens']['critic']:,}")
    print("  critic outcomes:   " + ", ".join(f"{k} {v}" for k, v in sorted(report["outcomes"].items())))
    for field in ("action", "message_type"):
        effects = [c["effect"][field] for c in report["changes"]]
        print(f"  critic on {field + ':':<13} fixed {effects.count('fixed')}, broke {effects.count('broke')}, "
              f"changed but still wrong {effects.count('changed, still wrong')}")
    print("\nerror buckets (first problem per row):")
    for bucket, members in sorted(report["buckets"].items(), key=lambda kv: -len(kv[1])):
        print(f"  {bucket:<16} {len(members):>2}  {', '.join(members)}")
    print("\nrows with a problem:")
    for r in report["rows"]:
        if r["bucket"]:
            print(f"  {r['message_id']:<15} [{r['bucket']}{', media' if r['media'] else ''}] got {r['got']}, "
                  f"expected {r['expected']}; evidence {r['evidence_got'] or 'none'} vs {r['evidence_expected'] or 'none'}"
                  f" ({r['model'].split('/')[-1]})")
    for c in report["changes"]:
        print(f"\ncritic {c['outcome']} {c['message_id']}: {c['before']} -> {c['after']}, expected {c['expected']}")
        for issue in c["issues"]:
            print(f"    - {issue}")

    if args.record:
        with RUNS_LOG.open("a", encoding="utf-8") as log:
            log.write(json.dumps(summary_record(report, args.record), ensure_ascii=False) + "\n")
        print(f"\nrecorded in {RUNS_LOG.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
