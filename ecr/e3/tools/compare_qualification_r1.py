"""Q5 check: the 13 v1 historical qualification units (P3Q, compare_with_r1) must reproduce the r1
formal Experiment 2 outcome of the same scenario. Read-only; run on a copy of the batch.

Method = phase-2 errata E3: outcome code built from the scorer_v2 result (same definition as
tests/run_dev_suite.code, copied here so that a formal tool does not import test code; the test
suite checks both give identical codes) compared by exact string equality with the EBRQ column of
the r1 outcome-matrix.csv, whose SHA-256 must equal --matrix-sha256. Only the first attempt of each
unit is compared; a unit without an OBSERVED attempt is a failure of this check, not a skip.
Usage: python -I compare_qualification_r1.py --batch DIR --cells cells_p3.json --matrix outcome-matrix.csv
                                           --matrix-sha256 HEX --out NEW.json
"""
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path


def code(s: dict) -> str:
    parts = []
    if s["duplicate_effect"]:
        parts.append("DUP")
    elif s["unauthorized_effect"]:
        parts.append("UNAUTH")
    if s["false_completion"]:
        parts.append("FALSE_DONE")
    if s["s4_invalid_sends"]:
        parts.append(f"UNSAFE_AUTH{s['s4_invalid_sends']}")
    if s["true_completion"] and not s["duplicate_effect"]:
        parts.append("DONE" if s["safe_opportunity"] else "DONE_NO_OPP")
    if not parts:
        parts.append("UNRESOLVED(missed)" if s["safe_opportunity"] else "UNRESOLVED")
    return "+".join(parts) + f" e={s['effect_count']}"


def compare(batch: Path, cells: dict, matrix_rows: dict) -> dict:
    units = [u for u in cells["units"] if u["family"] == "P3Q" and u.get("compare_with_r1")]
    results = {}
    for path in sorted(Path(batch, "attempts").glob("*/attempt-result.json")):
        facts = json.loads(path.read_text())
        results.setdefault(facts.get("unit_id"), []).append(facts)
    rows = []
    for u in units:
        sid = u["scenario"]["scenario_id"]
        expected = matrix_rows[sid]["EBRQ"]
        attempts = sorted(results.get(u["unit_id"], []), key=lambda f: f["attempt_id"])
        first = attempts[0] if attempts else None
        if first is None or "primary_score" not in first or first.get("state") not in ("OBSERVED_PENDING_REPLAY", "VALID_COMPLETED"):
            rows.append({"unit_id": u["unit_id"], "scenario": sid, "expected": expected, "observed": None,
                         "match": False, "reason": "no observed first attempt"})
            continue
        observed = code(first["primary_score"])
        rows.append({"unit_id": u["unit_id"], "attempt_id": first["attempt_id"], "scenario": sid,
                     "expected": expected, "observed": observed, "match": observed == expected})
    return {"units": len(units), "matched": sum(r["match"] for r in rows),
            "status": "PASS" if units and all(r["match"] for r in rows) else "FAIL", "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True)
    ap.add_argument("--cells", required=True)
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--matrix-sha256", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    if out.exists():
        sys.exit(f"refusing to overwrite {out}")
    if hashlib.sha256(Path(args.matrix).read_bytes()).hexdigest() != args.matrix_sha256:
        sys.exit("outcome matrix hash differs from --matrix-sha256")
    with open(args.matrix, newline="", encoding="utf-8") as f:
        matrix = {r["scenario"]: r for r in csv.DictReader(f)}
    report = compare(Path(args.batch), json.loads(Path(args.cells).read_text()), matrix)
    report.update(matrix=args.matrix, matrix_sha256=args.matrix_sha256, batch=args.batch)
    with open(out, "x", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: report[k] for k in ("units", "matched", "status")}))
    sys.exit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
