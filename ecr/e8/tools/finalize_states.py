"""Finalize attempt states of one batch from its replay reports (run on a read-only copy of the batch).

Inputs: the batch directory (attempts/*/attempt-result.json), the same-runtime replay report
(written by run_r2.py replay on the guest) and the cross-runtime replay report (replay.py on the
host, with --map for the guest paths). Each OBSERVED_PENDING_REPLAY attempt becomes
VALID_COMPLETED or INTEGRITY_UNKNOWN (p3lib.finalize_attempt); other states are unchanged.
The output is a new file; it can be passed to a continuation authorization as
"finalized_attempt_states". Historical or earlier outputs are never modified.
Usage: python -I finalize_states.py --batch DIR --same REPORT --cross REPORT --out NEW.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runner"))
import p3lib  # noqa: E402


def rows_by_attempt(report: dict) -> tuple[dict, bool]:
    grouped, unattributed = {}, False
    for r in report["rows"]:
        if not r.get("attempt_id"):  # e.g. an incomplete record whose started file is unreadable
            unattributed = True
            continue
        grouped.setdefault(r["attempt_id"], []).append(r)
    return grouped, unattributed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True)
    ap.add_argument("--same", required=True)
    ap.add_argument("--cross", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    if out.exists():
        sys.exit(f"refusing to overwrite {out}")
    same, same_bad = rows_by_attempt(json.loads(Path(args.same).read_text()))
    cross, cross_bad = rows_by_attempt(json.loads(Path(args.cross).read_text()))
    result = {}
    for path in sorted(Path(args.batch, "attempts").glob("*/attempt-result.json")):
        facts = json.loads(path.read_text())
        aid = facts["attempt_id"]
        state, reasons = p3lib.finalize_attempt(facts.get("state", "INTEGRITY_UNKNOWN"), facts.get("planner_calls", 0),
                                                same.get(aid, []), cross.get(aid, []), same_bad or cross_bad)
        result[aid] = {"state_before": facts.get("state"), "state": state, "reasons": reasons}
    summary = {"batch": str(args.batch), "same_report": args.same, "cross_report": args.cross,
               "counts": {s: sum(r["state"] == s for r in result.values()) for s in sorted({r["state"] for r in result.values()})},
               "attempts": result,
               "finalized_attempt_states": {a: r["state"] for a, r in result.items() if r["state_before"] == "OBSERVED_PENDING_REPLAY"},
               "note": "cross-runtime consistency is not an independent proof of correctness"}
    out.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary["counts"]))


if __name__ == "__main__":
    main()
