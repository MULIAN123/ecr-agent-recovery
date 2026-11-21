"""Build the ac1 unit table (plan r2, sections 4-8): 102 units, of which 40 form the first batch,
plus a separate ACQ qualification family. Read-only on the frozen cell table; writes one new file.

Base scenarios and truth rows (B2_DROP, B4, B5 on P0-P3) are copied from the frozen Experiment 2b
table private/cells_p3.json (FULL / v2 / R2B rows), verified identical across modes. Pause-window
truth follows plan r2, section 5. Every unit carries:
  window, layer (M main / B boundary / S stress / C control / Q qualification), opportunity_rule
  (TRUTH or F3P0_CONDITIONAL), a registered prediction (never a result), expected_primary_faults
  (recorded, not gating), secondary_faults_not_gating, scorer_failure_tolerated, and an exposure label.
Usage: python -I build_ac_cells.py <frozen cells_p3.json> <out cells_ac.json>
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runner"))
from p3lib import attempt_dirname, attempt_id  # noqa: E402

PROFILES = ["P0", "P1", "P2", "P3"]
CONTRACT = {"P0": {"query": False, "idempotent": False}, "P1": {"query": True, "idempotent": False},
            "P2": {"query": False, "idempotent": True}, "P3": {"query": True, "idempotent": True}}
MODES = ["R2A", "R2B"]
SECONDARY_NOT_GATING_RECOVERY = ["FENCE_RECORD_MISMATCH", "UNRECORDED_SEND"]
SAFE = {"S1": "SATISFIED", "S2": "SATISFIED", "S3": "SATISFIED", "S4": "SATISFIED"}

# plan r2, section 4.2: first-dispatch pause windows
F_POINT = {"F1": "before_grant", "F2": "after_grant", "F3": "after_consumed", "F4": "after_dbc"}
# plan r2, section 4.3: recovery pause windows (base B2_DROP)
R_POINT = {"R1": "after_fence_post", "R2": "after_fence_record", "R3": "after_grant", "R4": "after_consumed",
           "R5": "after_request", "R6": "after_response", "R7": "after_result_log"}


def ids(name: str) -> dict:
    return {"identity": {"artifactId": f"{name}-artifact", "artifactSetId": f"{name}-set", "attemptId": f"{name}-a1",
                         "bindingGeneration": 1, "deliverableId": f"{name}-deliverable", "queueBindingId": f"{name}-binding",
                         "taskId": f"{name}-task", "taskStateVersion": 1},
            "logical_operation_id": f"{name}-op", "queue_item_id": f"{name}-queue", "transport_request_id": f"{name}-t1"}


def f_truth(window: str, p: str) -> dict:
    point = F_POINT[window]
    opp = True
    if window == "F4" and p == "P0":
        opp = False
    return {"scenario_id": f"{window}_{p}", "barrier_id": point, "base": "first_dispatch_pause", "overlay": None, "profile": p,
            "dispatched": False, "max_allowed_effects": 1, "send_permitted": True, "original_request_effect": 0,
            "safe_opportunity": opp if not (window == "F3" and p == "P0") else None,
            "opportunity_note": ("plan r2 5.1: conditional; decided per cell by the diagnostic scorer on pre-recovery-1"
                                 if window == "F3" and p == "P0" else "plan r2 section 5")}


def f_prediction(window: str, p: str) -> dict:
    if window in ("F1", "F2"):
        return {**SAFE, "L": "COMPLETED"}
    if window == "F3":
        return {**SAFE, "L": "COMPLETED" if p == "P2" else "NOT_COMPLETED"}
    return {**SAFE, "L": "NO_OPPORTUNITY" if p == "P0" else "COMPLETED"}


def main():
    frozen = json.loads(Path(sys.argv[1]).read_text())
    base = {}
    for u in frozen["units"]:
        if u["family"] == "P3A" and u["classifier_arm"] == "FULL" and u["executor"] == "v2" and u.get("group") == "hist":
            sid = u["scenario"]["scenario_id"]
            row = (u["scenario"], u["truth"])
            if sid in base and base[sid] != row:
                raise SystemExit(f"frozen truth differs across modes for {sid}")
            base[sid] = row
    for b in ("B2_DROP", "B4", "B5"):
        for p in PROFILES:
            assert f"{b}_{p}" in base, f"missing frozen base row {b}_{p}"
    units, n = [], 0

    def add(uid, window, layer, mode, profile, scenario, truth, prediction, *, rule="TRUTH", declared=None,
            expected_primary=(), secondary_not_gating=(), tolerated=(), exposure="NEW (first execution; not a held-out test)",
            first_batch=False, family="AC"):
        nonlocal n
        n += 1
        name = f"{family}-{n:04d}"
        attempt_dirname(attempt_id(uid, 1))
        units.append({"unit_id": uid, "name": name, "family": family, "window": window, "layer": layer,
                      "classifier_arm": "FULL", "executor": "v2", "r2_mode": mode, "policy": "EBRQ2",
                      "endpoint_contract": declared or CONTRACT[profile], "scenario": {**scenario, "profile": profile},
                      "truth": truth, "prediction": prediction, "opportunity_rule": rule,
                      "expected_primary_faults": list(expected_primary),
                      "secondary_faults_not_gating": list(secondary_not_gating),
                      "scorer_failure_tolerated": list(tolerated), "exposure": exposure,
                      "first_batch": first_batch, "ids": ids(name)})

    def rec(*pauses):
        steps = [{"label": f"agent-recover-{i}", "pause": pt} for i, pt in enumerate(pauses, 1)]
        steps.append({"label": f"agent-recover-{len(pauses) + 1}", "pause": None})
        return steps

    def hist(b, p, sid):
        sc, tr = base[f"{b}_{p}"]
        return {**sc, "scenario_id": sid}, {**tr, "scenario_id": sid}

    # ---------------- F1-F4 (32)
    dev_seen = {"F2": "after_grant", "F3": "after_consumed", "F4": "after_dbc"}
    for window in ("F1", "F2", "F3", "F4"):
        for mode in MODES:
            for p in PROFILES:
                uid = f"AC:{window}:{mode}:{p}"
                scenario = {"scenario_id": f"{window}_{p}", "barrier": None, "send_pause": F_POINT[window],
                            "recoveries": rec(), "overlays": []}
                exposure = ("SEEN: phase-2 dev crash group (os._exit(137) hook, unfrozen)" if window in dev_seen
                            else "NEW (first execution; not a held-out test)")
                add(uid, window, "M", mode, p, scenario, f_truth(window, p), f_prediction(window, p),
                    rule="F3P0_CONDITIONAL" if (window == "F3" and p == "P0") else "TRUTH", exposure=exposure)
    # ---------------- R1-R7 (base B2_DROP)
    r_profiles = {"R1": ["P1"], "R2": ["P1"], "R3": ["P1", "P2"], "R4": ["P1", "P2"], "R5": ["P1", "P2"],
                  "R6": ["P1", "P2"], "R7": ["P1", "P2"]}
    dev_r = {("R3", "P1"), ("R3", "P2"), ("R4", "P1"), ("R4", "P2"), ("R5", "P1"), ("R5", "P2")}
    for window, profiles in r_profiles.items():
        for mode in MODES:
            for p in profiles:
                sc, tr = hist("B2_DROP", p, f"{window}_{p}")
                scenario = {**sc, "send_pause": None, "recoveries": rec(R_POINT[window]), "overlays": []}
                pred = {**SAFE, "L": "NOT_COMPLETED" if window == "R7" else "COMPLETED"}
                add(f"AC:{window}:{mode}:{p}", window, "M", mode, p, scenario, {**tr, "recovery_pause": R_POINT[window]}, pred,
                    secondary_not_gating=SECONDARY_NOT_GATING_RECOVERY,
                    exposure=("SEEN: phase-2 dev crash group (os._exit(137) hook, unfrozen)" if (window, p) in dev_r
                              else "NEW (first execution; not a held-out test)"))
    # ---------------- consecutive crashes (plan r2 4.4: only reachable combinations)
    sequences = {"SA": (("R1", "R1"), ["P1"]), "SB": (("R4", "R5"), ["P1", "P2"]), "SC": (("F3", "R3"), ["P2"]),
                 "SD": (("R2", "R7"), ["P1"])}
    for seq, (windows, profiles) in sequences.items():
        for mode in MODES:
            for p in profiles:
                if windows[0] == "F3":
                    scenario = {"scenario_id": f"{seq}_{p}", "barrier": None, "send_pause": F_POINT["F3"],
                                "recoveries": rec(R_POINT[windows[1]]), "overlays": []}
                    truth = f_truth("F3", p)
                else:
                    sc, truth = hist("B2_DROP", p, f"{seq}_{p}")
                    scenario = {**sc, "send_pause": None, "recoveries": rec(R_POINT[windows[0]], R_POINT[windows[1]]),
                                "overlays": []}
                pred = {**SAFE, "L": "NOT_COMPLETED" if seq == "SD" else "COMPLETED"}
                add(f"AC:{seq}:{mode}:{p}", seq, "M", mode, p, scenario, truth, pred,
                    secondary_not_gating=SECONDARY_NOT_GATING_RECOVERY)
    # ---------------- identity detection: I1 (R2-B only), I2 (both modes)
    for p in ("P1", "P2"):
        sc, tr = hist("B2_DROP", p, f"I1_{p}")
        add(f"AC:I1:R2B:{p}", "I1", "M", "R2B", p, {**sc, "send_pause": None, "recoveries": rec(), "overlays": [],
            "recovery_key": "ALT"}, tr, {**SAFE, "L": "NOT_COMPLETED"})
        for mode in MODES:
            sc, tr = hist("B2_DROP", p, f"I2_{p}")
            truth = {**tr, "max_allowed_effects": 0, "send_permitted": False, "safe_opportunity": False,
                     "overlay": "F5_SUPERSEDED_VERSION"}
            add(f"AC:I2:{mode}:{p}", "I2", "M", mode, p, {**sc, "send_pause": None, "recoveries": rec(),
                "overlays": [{"before_recovery": 1, "name": "F5_SUPERSEDED_VERSION"}]}, truth,
                {**SAFE, "L": "NO_OPPORTUNITY"})
    # ---------------- controls (no fault)
    for mode in MODES:
        for p in PROFILES:
            sid = f"CONTROL_{p}"
            truth = {"scenario_id": sid, "barrier_id": "NONE", "base": "no_fault", "overlay": None, "profile": p,
                     "dispatched": True, "max_allowed_effects": 1, "send_permitted": True, "original_request_effect": 1,
                     "safe_opportunity": True}
            add(f"AC:CTRL:{mode}:{p}", "CTRL", "C", mode, p, {"scenario_id": sid, "barrier": None, "send_pause": None,
                "no_fault": True, "recoveries": rec(), "overlays": []}, truth, {**SAFE, "L": "COMPLETED"},
                expected_primary=["INJECTION_RECEIPT_MISMATCH"], secondary_not_gating=["INJECTION_RECEIPT_MISMATCH"])
    # ---------------- B1a (main: R2-A detection), B2 (boundary: H-EP)
    for p in ("P1", "P2"):
        sc, tr = hist("B4", p, f"B1A_{p}")
        add(f"AC:B1A:R2A:{p}", "B1A", "M", "R2A", p, {**sc, "send_pause": None, "recoveries": rec(), "overlays": [],
            "recovery_key": "ALT"}, tr, {**SAFE, "L": "NOT_COMPLETED"})
    for mode in MODES:
        sc, tr = hist("B4", "P0", "B2HEP_P0")
        add(f"AC:B2HEP:{mode}:P0", "B2HEP", "B", mode, "P0", {**sc, "send_pause": None, "recoveries": rec(), "overlays": []},
            tr, {"S1": "VIOLATED", "S2": "VIOLATED", "S3": "SATISFIED", "S4": "VIOLATED", "L": "NO_OPPORTUNITY"},
            declared={"query": False, "idempotent": True})
    # ---------------- stress (18)
    stress_tol = dict(expected_primary=["MISSING_FINAL_STATE"],
                      secondary_not_gating=SECONDARY_NOT_GATING_RECOVERY + ["MISSING_FINAL_STATE"],
                      tolerated=["primary", "secondary"])
    for mode in MODES:
        for ov in ("TRUNCATE_GRANT", "TRUNCATE_CONSUMED"):
            sc, tr = hist("B2_DROP", "P1", f"{ov}_P1")
            add(f"AC:{ov}:{mode}:P1", ov, "S", mode, "P1", {**sc, "send_pause": None, "recoveries": rec(),
                "overlays": [{"before_recovery": 1, "name": ov}]}, tr, {**SAFE, "L": "NOT_COMPLETED"}, **stress_tol)
        sc, tr = hist("B2_DROP", "P1", "TRUNCATE_FENCE_P1")
        add(f"AC:TRUNCATE_FENCE:{mode}:P1", "TRUNCATE_FENCE", "S", mode, "P1",
            {**sc, "send_pause": None, "recoveries": rec(R_POINT["R2"]), "overlays": [{"before_recovery": 2, "name": "TRUNCATE_FENCE"}]},
            tr, {**SAFE, "L": "NOT_COMPLETED"}, **stress_tol)
        sc, tr = hist("B5", "P3", "GHOST_P3")
        add(f"AC:GHOST:{mode}:P3", "GHOST", "S", mode, "P3", {**sc, "send_pause": None, "recoveries": rec(),
            "overlays": [{"before_recovery": 1, "name": "GHOST_CONSUMED"}]}, tr, {**SAFE, "L": "NOT_COMPLETED"},
            exposure="SEEN: phase-2 dev anomaly injection (unfrozen)")
        for ov in ("VOID_AND_CONSUMED", "CONSUMED_KEY_MISMATCH", "FOREIGN_KEY_FENCE"):
            sc, tr = hist("B2_DROP", "P1", f"{ov}_P1")
            pred = {**SAFE, "L": "COMPLETED" if ov == "FOREIGN_KEY_FENCE" else "NOT_COMPLETED"}
            add(f"AC:{ov}:{mode}:P1", ov, "S", mode, "P1", {**sc, "send_pause": None, "recoveries": rec(),
                "overlays": [{"before_recovery": 1, "name": ov}]}, tr, pred)
        sc, tr = hist("B2_DROP", "P1", "STALE_FENCE_P1")
        add(f"AC:STALE_FENCE:{mode}:P1", "STALE_FENCE", "S", mode, "P1",
            {**sc, "send_pause": None, "recoveries": rec(R_POINT["R2"], R_POINT["R2"]),
             "overlays": [{"before_recovery": 3, "name": "STALE_FENCE_COPY"}],
             "tool_steps": [{"before_recovery": 3, "tool": "direct_fenced_send", "args": {"generation": "stale"}}]},
            tr, {"S1": "SATISFIED", "S2": "SATISFIED", "S3": "SATISFIED", "S4": "SATISFIED", "L": "COMPLETED",
                 "note": "design record DN1: scorer_v2 fenced_ok does not require the matched fence to be the latest"},
            secondary_not_gating=SECONDARY_NOT_GATING_RECOVERY)
    for p in ("P1", "P2"):
        sc, tr = hist("B4", p, f"B1B_{p}")
        add(f"AC:B1B:R2A:{p}", "B1B", "S", "R2A", p, {**sc, "send_pause": None, "recoveries": rec(),
            "overlays": [{"before_recovery": 1, "name": "REWRITE_AUTHORITY_KEYS"}], "recovery_key": "ALT"}, tr,
            {"S1": "VIOLATED", "S2": "VIOLATED", "S3": "SATISFIED", "S4": "VIOLATED", "L": "NOT_COMPLETED",
             "note": "record-tampering stress; cannot show that A4 is indispensable under the normal fault model"},
            expected_primary=["FOREIGN_EFFECT_IN_LEDGER"], secondary_not_gating=["FOREIGN_EFFECT_IN_LEDGER"])

    # ---------------- first batch: plan r2, section 8 (40 units, in plan order)
    first = ["AC:F1:R2B:P0", "AC:F1:R2B:P1", "AC:F1:R2B:P2", "AC:F2:R2B:P0", "AC:F2:R2B:P1", "AC:F2:R2B:P2",
             "AC:F3:R2B:P0", "AC:F3:R2B:P1", "AC:F3:R2B:P2", "AC:F4:R2B:P0", "AC:F4:R2B:P1", "AC:F4:R2B:P2",
             "AC:F2:R2A:P1", "AC:F3:R2A:P1", "AC:F4:R2A:P1", "AC:R1:R2B:P1", "AC:R2:R2B:P1", "AC:R3:R2B:P1",
             "AC:R3:R2B:P2", "AC:R4:R2B:P1", "AC:R4:R2B:P2", "AC:R5:R2B:P1", "AC:R5:R2B:P2", "AC:R6:R2B:P1",
             "AC:R6:R2B:P2", "AC:R7:R2B:P1", "AC:R7:R2B:P2", "AC:R4:R2A:P1", "AC:SA:R2B:P1", "AC:SB:R2B:P1",
             "AC:SB:R2B:P2", "AC:SC:R2B:P2", "AC:SD:R2B:P1", "AC:CTRL:R2B:P0", "AC:CTRL:R2B:P1", "AC:I1:R2B:P1",
             "AC:I2:R2B:P1", "AC:B1A:R2A:P1", "AC:TRUNCATE_CONSUMED:R2B:P1", "AC:CTRL:R2B:P2"]
    by_id = {u["unit_id"]: u for u in units}
    missing = [u for u in first if u not in by_id]
    if missing or len(first) != 40 or len(set(first)) != 40:
        raise SystemExit(f"first batch definition error: missing {missing}")
    for i, uid in enumerate(first, 1):
        by_id[uid]["first_batch"] = True
        by_id[uid]["first_batch_index"] = i
    # ---------------- ACQ qualification family (never counts as an attempt of an AC unit)
    qual_src = ["AC:F2:R2B:P1", "AC:F3:R2B:P2", "AC:R1:R2B:P1", "AC:SB:R2B:P1", "AC:CTRL:R2B:P1", "AC:TRUNCATE_CONSUMED:R2B:P1"]
    qual_ids = []
    for src in qual_src:
        u = by_id[src]
        add("ACQ" + src[2:], u["window"], "Q", u["r2_mode"], u["scenario"]["profile"], u["scenario"], u["truth"],
            u["prediction"], rule=u["opportunity_rule"], declared=u["endpoint_contract"],
            expected_primary=u["expected_primary_faults"], secondary_not_gating=u["secondary_faults_not_gating"],
            tolerated=u["scorer_failure_tolerated"], exposure="QUALIFICATION (not an experiment result)", family="ACQ")
        qual_ids.append("ACQ" + src[2:])
    ac = [u for u in units if u["family"] == "AC"]
    uids = [u["unit_id"] for u in units]
    assert len(uids) == len(set(uids))
    summary = {"AC_units": len(ac), "AC_first_batch": sum(u["first_batch"] for u in ac),
               "ACQ_units": len(qual_ids),
               "by_layer": {k: sum(u["layer"] == k for u in ac) for k in ("M", "B", "S", "C")}}
    if summary["AC_units"] != 102 or summary["AC_first_batch"] != 40:
        raise SystemExit(f"unit count differs from plan r2: {summary}")
    out = {"status": "CANDIDATE - not frozen", "plan": "AUTH_CRASH_PREEXEC_PLAN_r2",
           "frozen_cells_p3_sha256": hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest(),
           "summary": summary, "qualification_unit_ids": qual_ids, "units": units}
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
