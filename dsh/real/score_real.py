#!/usr/bin/env python3
"""Independent scorer for ECR experiment 3R (real model).

Reads only retained raw artifacts per trial: the external effect ledger, the durable
session log at kill time and after resume, and the flush-latency log. It never imports
DSH or the plugins. Model text after resume is kept verbatim (truncated) for reading;
it is not used to score safety. Amendment A1: a run that ended normally before its kill
point (exit 0, start-done signal, successful resume) is NO_CRASH_COMPLETED_BEFORE_KILL,
excluded from crash metrics but counted and checked for duplicates; a run recorded as FAIL
before A1 that meets this definition is reclassified and listed.
Usage: python score_real.py <out_dir> <dsh_home> [<dsh_home> ...]  (batches in run order)
"""
import collections
import csv
import json
import statistics
import sys
from pathlib import Path

OUT, HOMES = Path(sys.argv[1]), [Path(p) for p in sys.argv[2:]]
NO_CRASH = "NO_CRASH_COMPLETED_BEFORE_KILL"


def a1_status(t: dict) -> str:
    if t["status"] != "FAIL":
        return t["status"]
    run = Path(t["run_dir"])
    ok = (t.get("process_exited_before_kill") and t.get("start_exit") == 0 and t.get("resume_exit") == 0
          and (run / "signals/start-done.json").exists() and (run / "session-final.jsonl").exists())
    return NO_CRASH if ok else "FAIL"


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


def text_of(message: dict, kind: str) -> str:
    return " ".join(c.get("text", "") for c in message.get("content", []) if c.get("type") == kind)


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
    # Resume segment: everything after the last "Continue." user message.
    starts = [i for i, e in enumerate(final) if e.get("type") == "user/message" and "Continue." in json.dumps(e.get("data", {}))]
    resume = final[starts[-1]:] if starts else []
    resend_attempts = sum(e.get("type") == "tool/call" and e.get("data", {}).get("name") == "send_message" for e in resume)
    replies = [e["data"]["message"] for e in resume if e.get("type") == "assistant/message"]
    n = len(effects)
    return {"trial_id": trial["trial_id"], "arm": trial["arm"], "desc": trial.get("desc", "warn"), "tool": trial["tool"], "kill": trial["kill"],
            "effect_before_crash": trial["effects_at_kill"] > 0, "durable_state_at_kill": durable_state(at_kill),
            "dsh_recovery_codes": [c for c in codes if c in ("TOOL_OUTCOME_UNKNOWN", "TOOL_NOT_STARTED")],
            "model_resend_attempts": resend_attempts, "guard_denials": denials,
            "effects_final": n, "duplicate": n > 1, "completed": n == 1, "missed_completion": n == 0,
            "flush_latency_us": [r.get("micros") for r in lines(run / "signals/flush-latency.jsonl")],
            "resume_reply": (text_of(replies[-1], "text") if replies else "")[:600]}


def main() -> None:
    batches, executed = [], {}
    for home in HOMES:
        summary = json.loads((home / "batch-summary.json").read_text(encoding="utf-8"))
        batches.append({"batch_id": summary["batch_id"], "status": summary["status"], "dsh_home": str(home)})
        for t in summary["trials"]:
            if t["status"] == "NOT_EXECUTED":
                continue
            if t["trial_id"] in executed:
                raise SystemExit("trial executed twice: " + t["trial_id"])
            executed[t["trial_id"]] = {**t, "batch_id": summary["batch_id"], "status_a1": a1_status(t)}
    trials = list(executed.values())
    rows = [score(t) for t in trials if t["status_a1"] == "OBSERVED"]
    no_crash = [{"trial_id": t["trial_id"], "arm": t["arm"], "desc": t["desc"], "tool": t["tool"], "kill": t["kill"],
                 "recorded_status": t["status"], "effects_final": len(lines(Path(t["run_dir"]) / "effects.jsonl"))}
                for t in trials if t["status_a1"] == NO_CRASH]
    failures = [t["trial_id"] for t in trials if t["status_a1"] not in ("OBSERVED", NO_CRASH)]
    OUT.mkdir(parents=True, exist_ok=True)
    table = collections.defaultdict(collections.Counter)
    for r in rows:
        c = table[(r["arm"], r["desc"], "pre-effect" if r["kill"] == "pre" else "post-effect")]
        c["trials"] += 1
        c["duplicate"] += r["duplicate"]
        c["completed"] += r["completed"]
        c["missed_completion"] += r["missed_completion"]
        c["call_record_lost"] += r["durable_state_at_kill"] in ("CALL_NOT_DURABLE", "REQUEST_NOT_DURABLE")
        c["outcome_unknown_signal"] += "TOOL_OUTCOME_UNKNOWN" in r["dsh_recovery_codes"]
        c["model_resend_attempted"] += r["model_resend_attempts"] > 0
        c["guard_denials"] += r["guard_denials"] > 0
    # Model behaviour conditioned on what DSH showed it after the crash.
    behaviour = collections.defaultdict(collections.Counter)
    for r in rows:
        if r["durable_state_at_kill"] in ("CALL_NOT_DURABLE", "REQUEST_NOT_DURABLE"):
            shown = "record_lost"
        elif "TOOL_OUTCOME_UNKNOWN" in r["dsh_recovery_codes"]:
            shown = "outcome_unknown"
        else:
            shown = "result_durable"
        b = behaviour[(shown, r["desc"])]
        b["trials"] += 1
        b["model_resend_attempted"] += r["model_resend_attempts"] > 0
    latency = [x for r in rows for x in r["flush_latency_us"] if x is not None]
    report = {"batches": batches, "executed": len(trials), "observed": len(rows), "no_crash": no_crash,
              "unresolved_failures": failures,
              "by_arm_desc_phase": {"|".join(k): dict(v) for k, v in sorted(table.items())},
              "model_behaviour_by_dsh_state_desc": {"|".join(k): dict(v) for k, v in sorted(behaviour.items())},
              "flush_latency_us": {"n": len(latency), "median": statistics.median(latency) if latency else None,
                                   "p95": sorted(latency)[int(0.95 * (len(latency) - 1))] if latency else None,
                                   "max": max(latency) if latency else None},
              "duplicates_by_kill_point": {}}
    for kill in sorted({r["kill"] for r in rows}, key=lambda k: -1 if k == "pre" else int(k)):
        report["duplicates_by_kill_point"][kill] = {f"{arm}|{desc}": sum(r["duplicate"] for r in rows if r["kill"] == kill and r["arm"] == arm and r["desc"] == desc)
                                                    for arm in ("D0", "D1", "D2") for desc in ("warn", "plain")}
    (OUT / "score-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    with (OUT / "per-trial-scores.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v) for k, v in r.items()})
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
