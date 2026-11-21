"""Build the phase-3 candidate unit list (P3-A, X1 subset, CONV x F7, P3-C). Read-only on r1.

Historical scenarios and their truth rows are copied from the frozen r1 cell table
(async_sim_r1/pkg_r1/private/cells.json) and verified identical across r1 arms. F7 and P3-C
truth follow the rules stated in the phase-3 plan. Every unit carries an exposure label telling
whether the same classifier x executor x scenario combination was already run before
(r1 formal Experiment 2, or phase-2 development tests), so that previously seen combinations are
not described as unseen tests.
Usage: python -I build_p3_cells.py <r1 cells.json> <out.json>
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runner"))
from p3lib import attempt_dirname, attempt_id, unit_id  # noqa: E402

PROFILES = ["P0", "P1", "P2", "P3"]
CONTRACT = {"P0": {"query": False, "idempotent": False}, "P1": {"query": True, "idempotent": False},
            "P2": {"query": False, "idempotent": True}, "P3": {"query": True, "idempotent": True}}
CLASSIFIERS = {"FULL": "FULL", "A_OUTCOME_BOUNDARY": "-C_disp", "A_UNKNOWN_PRESERVATION": "-U"}
F7_BASES = ["B2_DROP", "B3_LATE", "B4"]
CRASH_FIRST = ["after_grant", "after_consumed", "after_dbc", "after_request", "after_response"]
CRASH_RECOVERY = ["after_grant", "after_consumed", "after_request"]
# The historical scorer compares endpoint sends/fences with the agent's recovery records; a recovery
# process killed by the crash hook cannot finish its records, so these two faults of the SECONDARY
# scorer are recorded but do not gate P3-C recovery-crash units. Primary (scorer_v2) faults always gate.
SECONDARY_NOT_GATING = ["FENCE_RECORD_MISMATCH", "UNRECORDED_SEND"]


def ids(name: str) -> dict:
    return {"identity": {"artifactId": f"{name}-artifact", "artifactSetId": f"{name}-set", "attemptId": f"{name}-a1",
                         "bindingGeneration": 1, "deliverableId": f"{name}-deliverable", "queueBindingId": f"{name}-binding",
                         "taskId": f"{name}-task", "taskStateVersion": 1},
            "logical_operation_id": f"{name}-op", "queue_item_id": f"{name}-queue", "transport_request_id": f"{name}-t1"}


def main():
    r1 = json.loads(Path(sys.argv[1]).read_text())
    hist = {}
    for c in r1["formal_cells"]:
        sid = c["scenario"]["scenario_id"]
        row = (c["scenario"], c["truth"])
        if sid in hist and hist[sid] != row:
            raise SystemExit(f"r1 truth differs across arms for {sid}")
        hist[sid] = row
    assert len(hist) == 52
    units, n = [], 0

    def add(family, scenario, truth, classifier, executor, mode, policy, extra=None):
        nonlocal n
        n += 1
        name = f"{family}-{n:04d}"
        uid = unit_id(family, scenario["scenario_id"], CLASSIFIERS.get(classifier, classifier), executor, mode)
        attempt_dirname(attempt_id(uid, 1))  # validates the id
        units.append({"unit_id": uid, "name": name, "family": family, "scenario": scenario, "truth": truth,
                      "classifier_arm": classifier, "executor": executor, "r2_mode": mode, "policy": policy,
                      "endpoint_contract": CONTRACT[scenario["profile"]], "ids": ids(name), **(extra or {})})

    def exposure(classifier, executor, group, sid=None):
        if executor == "v1" and group == "hist":
            return "SEEN: r1 formal Experiment 2" + ("; phase-2 dev parity" if classifier == "FULL" else "")
        if executor == "v1" and group == "F7":
            return "SEEN: phase-2 dev check (one cell)" if (classifier == "FULL" and sid == "F7_B4_P0") else "NEW"
        if executor == "v2" and classifier == "FULL":
            return "SEEN: phase-2 dev tests"
        if executor == "CONV":
            return "NEW"
        return "NEW"

    f7 = []
    for base in F7_BASES:
        for p in PROFILES:
            sid = f"F7_{base}_{p}"
            barrier_index = {"B2_DROP": "B2", "B3_LATE": "B3", "B4": "B4"}[base]
            f7.append(({"barrier": base, "overlay": "F7_LOCAL_FAILURE_AFTER_DISPATCH", "profile": p, "scenario_id": sid},
                       {"scenario_id": sid, "barrier_id": barrier_index, "base": base, "overlay": "F7_LOCAL_FAILURE_AFTER_DISPATCH",
                        "profile": p, "dispatched": True, "max_allowed_effects": 1, "send_permitted": True,
                        "original_request_effect": {"B2_DROP": 0, "B3_LATE": "commits late unless fenced", "B4": 1}[base],
                        "safe_opportunity": p != "P0",
                        "note": "valid dispatch followed only by a locally recorded definite failure"}))
    for classifier in CLASSIFIERS:
        for executor, mode, policy in (("v1", None, "EBRQ"), ("v2", "R2A", "EBRQ2"), ("v2", "R2B", "EBRQ2")):
            for sid in sorted(hist):
                sc, tr = hist[sid]
                add("P3A", sc, tr, classifier, executor, mode, policy, {"group": "hist", "exposure": exposure(classifier, executor, "hist")})
            for sc, tr in f7:
                x1 = classifier == "FULL"
                add("P3A", sc, tr, classifier, executor, mode, policy,
                    {"group": "F7", "x1": x1, "exposure": exposure(classifier, executor, "F7", sc["scenario_id"])})
    for sc, tr in f7:
        add("P3A", sc, tr, "-", "CONV", None, "CONV", {"group": "F7", "x1": True, "conv_extra": True, "exposure": "NEW"})
    for mode in ("R2A", "R2B"):
        for point in CRASH_FIRST:
            for p in PROFILES:
                sid = f"CRASH_FIRST_{point}_{p}"
                dispatched = point in ("after_request", "after_response")
                truth = {"scenario_id": sid, "barrier_id": "hook", "base": "first_dispatch_hook", "overlay": None, "profile": p,
                         "dispatched": dispatched, "max_allowed_effects": 1, "send_permitted": True,
                         "original_request_effect": 1 if dispatched else 0,
                         "safe_opportunity": True if point == "after_grant" else p != "P0", "crash_point": point}
                add("P3C", {"barrier": None, "crash_scope": "first", "crash_at": point, "profile": p, "scenario_id": sid},
                    truth, "FULL", "v2", mode, "EBRQ2", {"group": "crash_first", "exposure": "SEEN: phase-2 dev tests"})
        for point in CRASH_RECOVERY:
            for p in ("P1", "P2"):
                sid = f"CRASH_RECOVERY_{point}_{p}"
                sc, tr = hist[f"B2_DROP_{p}"]
                add("P3C", {**sc, "scenario_id": sid, "crash_scope": "recovery", "crash_at": point},
                    {**tr, "scenario_id": sid, "crash_point": point}, "FULL", "v2", mode, "EBRQ2",
                    {"group": "crash_recovery", "exposure": "SEEN: phase-2 dev tests",
                     "secondary_faults_not_gating": SECONDARY_NOT_GATING})
    # P3-Q (qualification of the runner): its own family, so no qualification attempt can ever count as
    # an attempt of a formal unit. Fixed list of 20 units covering every runner path:
    #   13 x FULL/v1 historical (one per base or overlay, profile rotating P0..P3; compared with r1 results),
    #   2 x CONV/F7, 4 x v2 (R2A/R2B, historical and F7), 1 x P3-C recovery-time crash (R2B, after_request, P2).
    bases = []
    for sid in sorted(hist):
        b = sid.rsplit("_", 1)[0]
        if b not in bases:
            bases.append(b)
    assert len(bases) == 13
    qual = []
    for i, b in enumerate(bases):
        sc, tr = hist[f"{b}_{PROFILES[i % 4]}"]
        qual.append((sc, tr, "FULL", "v1", None, "EBRQ", {"group": "hist", "compare_with_r1": True}))
    f7_by_id = {sc["scenario_id"]: (sc, tr) for sc, tr in f7}
    for sid in ("F7_B4_P0", "F7_B2_DROP_P1"):
        qual.append((*f7_by_id[sid], "-", "CONV", None, "CONV", {"group": "F7"}))
    for sid, mode in (("B2_LATE_P3", "R2A"), ("F6_LOST_DISPATCH_FAILURE_P0", "R2B")):
        qual.append((*hist[sid], "FULL", "v2", mode, "EBRQ2", {"group": "hist"}))
    for sid, mode in (("F7_B3_LATE_P1", "R2A"), ("F7_B4_P2", "R2B")):
        qual.append((*f7_by_id[sid], "FULL", "v2", mode, "EBRQ2", {"group": "F7"}))
    sc, tr = hist["B2_DROP_P2"]
    qual.append(({**sc, "scenario_id": "CRASH_RECOVERY_after_request_P2", "crash_scope": "recovery", "crash_at": "after_request"},
                 {**tr, "scenario_id": "CRASH_RECOVERY_after_request_P2", "crash_point": "after_request"},
                 "FULL", "v2", "R2B", "EBRQ2", {"group": "crash_recovery",
                                                 "secondary_faults_not_gating": SECONDARY_NOT_GATING}))
    for sc, tr, cl, ex, mode, pol, extra in qual:
        add("P3Q", sc, tr, cl, ex, mode, pol, {**extra, "exposure": "QUALIFICATION (not an experiment result)"})
    uids = [u["unit_id"] for u in units]
    assert len(uids) == len(set(uids)), "unit ids must be unique"
    summary = {
        "P3A_units": sum(u["family"] == "P3A" for u in units),
        "P3A_conv_extra": sum(u.get("conv_extra", False) for u in units),
        "X1_units": sum(u.get("x1", False) for u in units),
        "P3C_units": sum(u["family"] == "P3C" for u in units),
        "P3C_runnable_now": sum(u["family"] == "P3C" and u["group"] != "crash_first" for u in units),
        "P3Q_units": sum(u["family"] == "P3Q" for u in units),
        "exposure": {},
    }
    for u in units:
        key = f"{u['family']}|{CLASSIFIERS.get(u['classifier_arm'], u['classifier_arm'])}|{u['executor']}|{u['r2_mode'] or '-'}|{u['group']}|{u['exposure']}"
        summary["exposure"][key] = summary["exposure"].get(key, 0) + 1
    out = {"status": "CANDIDATE - not frozen", "r1_cells_sha256": hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest(),
           "summary": summary, "qualification_unit_ids": [u["unit_id"] for u in units if u["family"] == "P3Q"],
           "units": units}
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
