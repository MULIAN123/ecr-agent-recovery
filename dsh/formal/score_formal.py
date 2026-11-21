#!/usr/bin/env python3
"""Independent scorer for ECR experiment 3 (`dsh-20261013-r1`).

Reads only retained raw artifacts per trial: the external effect ledger, the
durable session log at kill time and after resume, and the flush-latency log.
It never imports DSH or the probe plugins.
Usage: python score_formal.py <dsh_home> <out_dir>
"""
import collections
import csv
import json
import statistics
import sys
from pathlib import Path

HOME, OUT = Path(sys.argv[1]), Path(sys.argv[2])


def lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"type": "TORN_LINE"})
    return rows


def durable_state(events: list[dict]) -> str:
    types = [e.get("type") for e in events]
    request = any(e.get("type") == "user/message" and "Send the message" in json.dumps(e.get("data", {})) for e in events)
    if not request:
        return "REQUEST_NOT_DURABLE"
    if types.count("tool/call") == 0:
        return "CALL_NOT_DURABLE"
    if types.count("tool/result") < types.count("tool/call"):
        return "CALL_DURABLE_RESULT_NOT_DURABLE"
    return "CALL_AND_RESULT_DURABLE"


def score(trial: dict) -> dict:
    run = Path(trial["run_dir"])
    effects = lines(run / "effects.jsonl")
    at_kill = lines(run / "session-at-kill.jsonl")
    final = lines(run / "session-final.jsonl")
    results = [e for e in final if e.get("type") == "tool/result"]
    codes = [((e.get("data") or {}).get("error") or {}).get("code") for e in results]
    denials = sum("ECR guard" in json.dumps(e.get("data", {})) for e in results)
    effect_at_kill = trial["effects_at_kill"]
    n = len(effects)
    return {"trial_id": trial["trial_id"], "arm": trial["arm"], "policy": trial["policy"], "tool": trial["tool"], "kill": trial["kill"],
            "effect_before_crash": effect_at_kill > 0, "durable_state_at_kill": durable_state(at_kill),
            "dsh_recovery_codes": [c for c in codes if c in ("TOOL_OUTCOME_UNKNOWN", "TOOL_NOT_STARTED")],
            "guard_denials": denials, "effects_final": n, "duplicate": n > 1,
            "completed": n == 1, "missed_completion": n == 0,
            "flush_latency_us": [r.get("micros") for r in lines(run / "signals/flush-latency.jsonl")]}


def main() -> None:
    summary = json.loads((HOME / "batch-summary.json").read_text(encoding="utf-8"))
    rows = [score(t) for t in summary["trials"] if t["status"] == "OBSERVED"]
    OUT.mkdir(parents=True, exist_ok=True)
    table = collections.defaultdict(lambda: collections.Counter())
    for r in rows:
        key = (r["arm"], r["policy"], "pre-effect" if r["kill"] == "pre" else "post-effect")
        c = table[key]
        c["trials"] += 1
        c["duplicate"] += r["duplicate"]
        c["completed"] += r["completed"]
        c["missed_completion"] += r["missed_completion"]
        c["guard_denials"] += r["guard_denials"] > 0
        c["call_record_lost"] += r["durable_state_at_kill"] in ("CALL_NOT_DURABLE", "REQUEST_NOT_DURABLE")
        c["outcome_unknown_signal"] += "TOOL_OUTCOME_UNKNOWN" in r["dsh_recovery_codes"]
    latency = [x for r in rows for x in r["flush_latency_us"] if x is not None]
    report = {"batch_id": summary["batch_id"], "batch_status": summary["status"], "observed": len(rows),
              "by_arm_policy_phase": {"|".join(k): dict(v) for k, v in sorted(table.items())},
              "flush_latency_us": {"n": len(latency), "median": statistics.median(latency) if latency else None,
                                   "p95": sorted(latency)[int(0.95 * (len(latency) - 1))] if latency else None,
                                   "max": max(latency) if latency else None},
              "by_kill_point": {}}
    for kill in sorted({r["kill"] for r in rows}, key=lambda k: -1 if k == "pre" else int(k)):
        report["by_kill_point"][kill] = {arm: {pol: sum(r["duplicate"] for r in rows if r["kill"] == kill and r["arm"] == arm and r["policy"] == pol)
                                               for pol in ("obedient", "naive")} for arm in ("D0", "D1", "D2")}
    (OUT / "score-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    with (OUT / "per-trial-scores.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v) if isinstance(v, list) else v) for k, v in r.items()})
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
