"""End-to-end development suite for async_sim_r2 (unprivileged, no OS isolation).

Groups (each writes results/<group>.json and exits non-zero on any failed assertion):
  parity   frozen v1 EBRQ (FULL) on the 52 historical scenarios; outcome codes must equal the
           historical EBRQ column of outcome-matrix.csv (harness qualification)
  v2-R2A   EBRQ-v2 (FULL) on the 52 historical scenarios, mode R2A
  v2-R2B   same, mode R2B
  f7       EBRQ-v2 (both modes) on F7 x {B2_DROP, B3_LATE, B4} x P0-P3; one v1 cell as a harness
           check that the frozen branch is reproduced
  crash    crash hooks at the new write points (first dispatch and recovery), anomaly injection
Assertions: safety only (no duplicate, no unauthorized effect, no false completion, no
contract-invalid send by scorer_v2, no integrity fault, recovery exit 0); recovery (L) is
recorded and compared with the historical EBRQ outcome, never asserted, except the parity
group. These are development tests, not experiment results.
Usage: python3 run_dev_suite.py <group> <outcome-matrix.csv> <work_dir>
"""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

from dev_harness import Cell, OVERLAY_BASE, run_cell, truth_for

BARRIERS = ["B1", "B2_DROP", "B2_LATE", "B2_ARRIVE", "B3_LATE", "B4", "B5", "B6"]
PROFILES = ["P0", "P1", "P2", "P3"]
OVERLAYS = ["F2_FOREIGN_ACCEPT", "F3_WEAK_ACK", "F4_REORDERED_CALLBACK", "F5_SUPERSEDED_VERSION", "F6_LOST_DISPATCH_FAILURE"]


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


def safety_failures(s: dict, exit_code: int) -> list[str]:
    f = []
    if exit_code != 0:
        f.append(f"recovery exit {exit_code}")
    for k in ("duplicate_effect", "unauthorized_effect", "false_completion"):
        if s[k]:
            f.append(k)
    if s["s4_invalid_sends"]:
        f.append("s4_invalid")
    if s["faults"]:
        f.append("faults " + ",".join(s["faults"]))
    return f


def scenarios():
    for b in BARRIERS:
        for p in PROFILES:
            yield f"{b}_{p}", b, p, None
    for o in OVERLAYS:
        for p in PROFILES:
            yield f"{o}_{p}", OVERLAY_BASE[o], p, o


def main() -> None:
    group, matrix_path, work = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    historical = {r["scenario"]: r for r in csv.DictReader(matrix_path.open())}
    work.mkdir(parents=True, exist_ok=True)
    results, failures = [], []
    t0 = time.time()

    def record(name, cell, s, rc, expect_hist=None, assert_safety=True, extra=None):
        c = code(s)
        row = {"cell": name, "code": c, "exit": rc, "score": s, **(extra or {})}
        if expect_hist is not None:
            row["historical_EBRQ"] = expect_hist
            row["matches_historical"] = c == expect_hist
        if assert_safety:
            fs = safety_failures(s, rc)
            if fs:
                failures.append(f"{name}: {fs}")
        results.append(row)
        print(f"{name:45s} {c:28s} hist={expect_hist}", flush=True)

    if group == "parity":
        for sid, b, p, o in scenarios():
            cell, s, rc = run_cell(work, f"par-{sid}", agent="v1", policy="EBRQ", mode=None, arm="FULL",
                                   barrier=b, profile=p, overlay=o)
            hist = historical[sid]["EBRQ"]
            record(sid, cell, s, rc, hist, assert_safety=False)
            if code(s) != hist:
                failures.append(f"{sid}: harness parity {code(s)} != historical {hist}")
    elif group in ("v2-R2A", "v2-R2B"):
        mode = group.split("-")[1]
        for sid, b, p, o in scenarios():
            cell, s, rc = run_cell(work, f"{mode}-{sid}", agent="v2", policy="EBRQ2", mode=mode, arm="FULL",
                                   barrier=b, profile=p, overlay=o)
            record(sid, cell, s, rc, historical[sid]["EBRQ"])
    elif group == "f7":
        for mode in ("R2A", "R2B"):
            for b in ("B2_DROP", "B3_LATE", "B4"):
                for p in PROFILES:
                    sid = f"F7_{b}_{p}"
                    cell, s, rc = run_cell(work, f"{mode}-{sid}", agent="v2", policy="EBRQ2", mode=mode, arm="FULL",
                                           barrier=b, profile=p, overlay="F7_LOCAL_FAILURE_AFTER_DISPATCH")
                    record(f"{mode}:{sid}", cell, s, rc)
        cell, s, rc = run_cell(work, "v1-F7_B4_P0", agent="v1", policy="EBRQ", mode=None, arm="FULL",
                               barrier="B4", profile="P0", overlay="F7_LOCAL_FAILURE_AFTER_DISPATCH")
        labels = [a.get("guarantee") for a in cell.recovery_actions() if a.get("action") == "AUTHORIZE_SEND"]
        record("v1:F7_B4_P0 (harness check)", cell, s, rc, assert_safety=False, extra={"v1_labels": labels})
        if "CLASSIFIED_DEFINITE_FAILURE" not in labels:
            failures.append("v1 F7 harness check: frozen branch not reproduced")
    elif group == "crash":
        for mode in ("R2A", "R2B"):
            for point in ("after_grant", "after_consumed", "after_dbc", "after_request", "after_response"):
                for p in PROFILES:
                    name = f"{mode}:first:{point}:{p}"
                    cell = Cell(work, name.replace(":", "-"), agent="v2", policy="EBRQ2", mode=mode, arm="FULL",
                                barrier=None, profile=p)
                    try:
                        crc = cell.run_crash_send(point)
                        rc = cell.recover()
                        cell.stop()
                        dispatched = point in ("after_request", "after_response")
                        opp = True if point == "after_grant" else p != "P0"
                        truth = {**truth_for("B2_DROP", p, None), "scenario_id": name, "dispatched": dispatched,
                                 "safe_opportunity": opp, "barrier_id": "hook"}
                        s = cell.score(truth)
                        voids = cell.authority_records("void")
                        consumed = {c["grant_id"] for c in cell.authority_records("consumed")}
                        extra = {"send_exit": crc, "voided": [v["grant_id"] for v in voids]}
                        if crc != 137:
                            failures.append(f"{name}: crash hook did not fire (exit {crc})")
                        if point == "after_grant" and not voids:
                            failures.append(f"{name}: orphan grant not voided")
                        if any(v["grant_id"] in consumed for v in voids):
                            failures.append(f"{name}: a void grant was consumed")
                        if s["effect_count"] > 1:
                            failures.append(f"{name}: more than one effect")
                        record(name, cell, s, rc, extra=extra)
                    finally:
                        cell.stop()
        # crash during recovery: the first recovery dies at a new write point, a second recovery follows
        for mode in ("R2A", "R2B"):
            for point in ("after_grant", "after_consumed", "after_request"):
                for p in ("P1", "P2"):
                    name = f"{mode}:recovery:{point}:{p}"
                    cell = Cell(work, name.replace(":", "-"), agent="v2", policy="EBRQ2", mode=mode, arm="FULL",
                                barrier="B2_DROP", profile=p)
                    try:
                        cell.run_to_barrier()
                        rc1 = cell.recover({"ECR_TEST_CRASH_AT": point, "ECR_TEST_CRASH_SCOPE": "recovery"}, label="recover-1")
                        rc2 = cell.recover(label="recover-2")
                        cell.finish()
                        s = cell.score()
                        grants = cell.authority_records("grants")
                        consumed = {c["grant_id"] for c in cell.authority_records("consumed")}
                        voided = {v["grant_id"] for v in cell.authority_records("void")}
                        fences = cell.authority_records("fences")
                        if rc1 != 137:
                            failures.append(f"{name}: recovery crash hook did not fire (exit {rc1})")
                        if voided & consumed:
                            failures.append(f"{name}: a void grant was consumed")
                        if s["effect_count"] > 1:
                            failures.append(f"{name}: more than one effect")
                        record(name, cell, s, rc2, extra={"first_recovery_exit": rc1, "grants": len(grants),
                                                         "consumed": len(consumed), "voided": len(voided),
                                                         "fences": len(fences)})
                    finally:
                        cell.stop()
        # anomaly injection: consumed marker without a grant -> key frozen, review, no fence, no send
        for mode in ("R2A", "R2B"):
            name = f"{mode}:anomaly:ghost-marker:P3"
            cell = Cell(work, name.replace(":", "-"), agent="v2", policy="EBRQ2", mode=mode, arm="FULL",
                        barrier="B5", profile="P3")
            try:
                cell.run_to_barrier()
                from protocol import durable_json
                durable_json(cell.sut / "store/authority/consumed/ghost.json",
                             {"kind": "consumed", "grant_id": "ghost", "logical_operation_id": cell.ids["logical_operation_id"],
                              "authority_seq": 999})
                rc = cell.recover()
                cell.finish()
                s = cell.score()
                endpoint_fences = list((cell.adapter / "events").glob("log-*-fence.json"))
                if s["final_state"] != "review" or s["recovery_sends"] or endpoint_fences:
                    failures.append(f"{name}: anomaly not handled fail-closed ({s['final_state']}, sends {s['recovery_sends']},"
                                    f" fences {len(endpoint_fences)})")
                record(name, cell, s, rc, extra={"endpoint_fences": len(endpoint_fences)})
            finally:
                cell.stop()
    else:
        raise SystemExit("unknown group")

    out = {"group": group, "cells": len(results), "failures": failures, "seconds": round(time.time() - t0, 1),
           "results": results}
    (work / f"{group}.json").write_text(json.dumps(out, indent=1))
    print(f"== {group}: {len(results)} cells, {len(failures)} failures, {out['seconds']} s")
    for f in failures:
        print("FAIL", f)
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
