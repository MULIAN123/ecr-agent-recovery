"""Planted-fixture scorer controls for the phase-3 qualification (platform-independent part).

Port of the negative controls of async_sim_r1/run.py, extended for scorer_v2. Works only on new
copies of one completed attempt's evidence; originals are never modified. These are controls of
the scorers, not extra trials.
  duplicate          a second ledger effect of the logical operation   -> duplicate_effect (both scorers)
  false_completion   ledger emptied                                    -> false_completion (both scorers)
  unsafe_auth        historical scorer: an AUTHORIZE_SEND record with no valid basis
                     -> false_safe_authorizations >= 1 (the r1 control)
  unsafe_send        scorer_v2: an endpoint send of the logical operation with no label and no grant
                     -> s4_invalid_sends larger than on the unplanted evidence (scorer_v2 judges
                        sends from the endpoint log, not from executor labels)
The runner (run_r2.negative_controls) adds ownership changes and runs each scorer as ecr-scorer;
tests/test_phase3_tools.py runs plant() and judge() on a synthetic attempt with the scorers in-process.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path


def _write_new(path: Path, value: dict) -> None:
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(value, sort_keys=True) + "\n")


def plant(ctx: dict, out: Path) -> dict:
    """ctx: the scorer_v2 context of the attempt (ledger, endpoint_events, sut_copy, logical_operation_id).
    out must not exist. Returns the planted paths."""
    out.mkdir()
    duplicate = out / "duplicate.sqlite3"
    shutil.copyfile(ctx["ledger"], duplicate)
    with sqlite3.connect(duplicate) as db:
        db.execute("DROP INDEX IF EXISTS effect_once")
        db.execute("INSERT INTO effects(logical_operation_id,attempt_id,transport_request_id,committed) VALUES(?,?,?,1)",
                   (ctx["logical_operation_id"], "planted-a", "planted-t"))
    db.close()
    empty = out / "false-completion.sqlite3"
    shutil.copyfile(ctx["ledger"], empty)
    with sqlite3.connect(empty) as db:
        db.execute("DELETE FROM effects")
    db.close()
    store = out / "unsafe-auth-sut-copy"
    shutil.copytree(ctx["sut_copy"], store)
    _write_new(store / "store/recovery/999.json", {"index": 999, "action": "AUTHORIZE_SEND", "guarantee": "NONE",
               "basis": "planted", "request": {"transport_request_id": "planted-t", "generation": 0}})
    events = out / "unsafe-send-endpoint-events"
    shutil.copytree(ctx["endpoint_events"], events)
    _write_new(events / "log-9999-send.json", {"index": 9999, "kind": "send", "utc_ns": 0,
               "request": {"logical_operation_id": ctx["logical_operation_id"], "attempt_id": "planted-a",
                           "transport_request_id": "planted-t", "generation": 0},
               "result": {"status": "COMMITTED"}})
    return {"duplicate": duplicate, "false_completion": empty, "unsafe_auth_sut_copy": store, "unsafe_send_events": events}


def contexts(ctx_v2: dict, ctx_r1: dict, planted: dict) -> list[tuple[str, str, dict]]:
    """(name, scorer file, context) for every control run."""
    runs = []
    for scorer, label, c in (("scorer_v2.py", "v2", ctx_v2), ("scorer.py", "r1", ctx_r1)):
        runs.append((f"duplicate-{label}", scorer, {**c, "ledger": str(planted["duplicate"])}))
        runs.append((f"false-completion-{label}", scorer, {**c, "ledger": str(planted["false_completion"])}))
    runs.append(("unsafe-auth-r1", "scorer.py", {**ctx_r1, "sut_copy": str(planted["unsafe_auth_sut_copy"])}))
    runs.append(("unsafe-send-v2", "scorer_v2.py", {**ctx_v2, "endpoint_events": str(planted["unsafe_send_events"])}))
    return runs


def judge(baseline_v2: dict, scores: dict) -> dict:
    """scores: {control name: scorer output}. Every control must be detected."""
    missed = []
    for label in ("v2", "r1"):
        if not scores[f"duplicate-{label}"]["duplicate_effect"]:
            missed.append(f"duplicate-{label}")
        if not scores[f"false-completion-{label}"]["false_completion"]:
            missed.append(f"false-completion-{label}")
    if scores["unsafe-auth-r1"]["false_safe_authorizations"] < 1:
        missed.append("unsafe-auth-r1")
    if scores["unsafe-send-v2"]["s4_invalid_sends"] <= baseline_v2["s4_invalid_sends"]:
        missed.append("unsafe-send-v2")
    return {"status": "PASS" if not missed else "CONTROL_MISSED", "missed": missed,
            "unsafe_send_v2_s4_before_after": [baseline_v2["s4_invalid_sends"], scores["unsafe-send-v2"]["s4_invalid_sends"]],
            "unsafe_send_v2_faults": scores["unsafe-send-v2"]["faults"]}
