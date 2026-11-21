"""Build the bl6 unit table (ECR-G v3 + held-out-2). Read-only on the frozen bl5 table cells_bl3.json.

Scenarios (356)
  hist 52, F7 12, extended_dev 92  : from cells_bl3.json, unchanged
  heldout1 96                      : the bl5 'heldout' group, unchanged (renamed: it is SEEN - v3 was designed
                                     after its bl5 results, so it is development evidence for v3, not validation)
  heldout2 104 (new, never run)    : fixed grammar, written before any run of any arm on it:
     C2_FOREIGN_FAILURE_AFTER_<X>, X in {WEAK_ACK, REORDERED_CALLBACK, LOST_DISPATCH_FAILURE, LOCAL_FAILURE_AFTER_DISPATCH}
        x barriers B2_DROP, B2_ARRIVE, B2_LATE, B3_LATE, B4, B5 x profiles P0-P3            -> 96
     C2_SUPERSEDED_WITH_FOREIGN_ACCEPT / C2_SUPERSEDED_WITH_FOREIGN_FAILURE at B1 x P0-P3   ->  8
     Truth rule: the truth row of the base scenario (barrier/profile for C2_FOREIGN_FAILURE_*, and the frozen
     F5_SUPERSEDED_VERSION_<profile> row for the superseded composites), with scenario_id, overlay and note replaced.
Arms (8): the six bl5 arms unchanged + ECR-G v3 R2A / R2B (classifier FULL, executor v3, policy EBRQ3).
Family VX: 356 x 8 = 2848 units. Family VQ: 10 runner-qualification units (none in heldout2).
Usage: python -I build_bl6_cells.py <cells_bl3.json> <out cells_bl6.json>
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runner"))
from p3lib import attempt_dirname, attempt_id, unit_id  # noqa: E402

CELLS_BL3_SHA256 = "cc13210b598868145818fff743106f482ca463594a9543d1c3531fa514f9c66d"
ARMS = (  # label, classifier slot in unit id, classifier_arm, executor, mode, policy
    ("ECR-G R2A", "FULL", "FULL", "v2", "R2A", "EBRQ2"),
    ("ECR-G R2B", "FULL", "FULL", "v2", "R2B", "EBRQ2"),
    ("ECR-G3 R2A", "FULL", "FULL", "v3", "R2A", "EBRQ3"),
    ("ECR-G3 R2B", "FULL", "FULL", "v3", "R2B", "EBRQ3"),
    ("CONV", "-", "-", "CONV", None, "CONV"),
    ("INTENT-LOG", "INTENT_LOG", "-", "BL2", None, "INTENT_LOG"),
    ("RIFL-C", "RIFL_C", "-", "BL2", None, "RIFL_C"),
    ("CA-LOG", "CA_LOG", "-", "BL2", None, "CA_LOG"),
)
OWN = ("WEAK_ACK", "REORDERED_CALLBACK", "LOST_DISPATCH_FAILURE", "LOCAL_FAILURE_AFTER_DISPATCH")
BARRIERS = ("B2_DROP", "B2_ARRIVE", "B2_LATE", "B3_LATE", "B4", "B5")
PROFILES = ("P0", "P1", "P2", "P3")
QUAL = (  # scenario_id, arm label  (v3 quarantine paths on SEEN scenarios + new-overlay wiring on qualification-only scenarios)
    ("NEW_WEAK_ACK_B4_P1", "ECR-G3 R2A"),                    # fence -> COMMITTED
    ("NEW_REORDERED_CALLBACK_B2_DROP_P3", "ECR-G3 R2B"),     # fence -> DEFINITELY_NOT_COMMITTED -> FENCED_NEGATIVE send
    ("NEW_LOCAL_FAILURE_AFTER_DISPATCH_B4_P2", "ECR-G3 R2A"),  # quarantined IDEMPOTENT
    ("NEW_LOST_DISPATCH_FAILURE_B2_ARRIVE_P0", "ECR-G3 R2B"),  # P0 -> review
    ("B2_LATE_P3", "ECR-G3 R2A"),                            # non-corrupt: v2 path
    ("F5_SUPERSEDED_VERSION_P1", "ECR-G3 R2B"),              # superseded work stays review
    ("Q2_C2_FOREIGN_FAILURE_AFTER_WEAK_ACK_B1_P3", "ECR-G3 R2B"),  # C2 overlay wiring, qualification only
    ("Q2_C2_FOREIGN_FAILURE_AFTER_REORDERED_CALLBACK_B1_P1", "CA-LOG"),
    ("B4_P2", "ECR-G R2A"),
    ("H_F3_B2_LATE_P0", "CONV"),
)


def ids(name: str) -> dict:
    return {"identity": {"artifactId": f"{name}-artifact", "artifactSetId": f"{name}-set", "attemptId": f"{name}-a1",
                         "bindingGeneration": 1, "deliverableId": f"{name}-deliverable", "queueBindingId": f"{name}-binding",
                         "taskId": f"{name}-task", "taskStateVersion": 1},
            "logical_operation_id": f"{name}-op", "queue_item_id": f"{name}-queue", "transport_request_id": f"{name}-t1"}


def main():
    src = Path(sys.argv[1])
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    if digest != CELLS_BL3_SHA256:
        raise SystemExit(f"source table is not the frozen cells_bl3.json: {digest}")
    bl3 = json.loads(src.read_text())
    scen = {}
    for u in bl3["units"]:
        if u["family"] == "VX" and u["executor"] == "v2" and u["r2_mode"] == "R2A":
            g = "heldout1" if u["group"] == "heldout" else u["group"]
            scen[u["scenario"]["scenario_id"]] = (u["scenario"], u["truth"], u["endpoint_contract"], g)
    assert len(scen) == 252, len(scen)
    base = {(s["barrier"], s["profile"]): (s, t, c) for s, t, c, g in scen.values() if not s.get("overlay")}
    f5 = {s["profile"]: (s, t, c) for s, t, c, g in scen.values() if s.get("overlay") == "F5_SUPERSEDED_VERSION"}
    new = []
    for x in OWN:
        for b in BARRIERS:
            for p in PROFILES:
                s0, t0, c0 = base[(b, p)]
                ov = f"C2_FOREIGN_FAILURE_AFTER_{x}"
                sid = f"H2_FF_{x}_{b}_{p}"
                new.append(({"barrier": b, "overlay": ov, "profile": p, "scenario_id": sid},
                            {**t0, "scenario_id": sid, "overlay": ov,
                             "note": "held-out-2: local fault then an unrelated attempt's failure record; truth of base barrier/profile"},
                            c0))
    for kind in ("ACCEPT", "FAILURE"):
        for p in PROFILES:
            s0, t0, c0 = f5[p]
            ov = f"C2_SUPERSEDED_WITH_FOREIGN_{kind}"
            sid = f"H2_SUP_FOREIGN_{kind}_{p}"
            new.append(({"barrier": "B1", "overlay": ov, "profile": p, "scenario_id": sid},
                        {**t0, "scenario_id": sid, "overlay": ov,
                         "note": "held-out-2: superseded task version plus an unrelated attempt's record; truth of F5"}, c0))
    assert len(new) == 104
    for s, t, c in new:
        scen[s["scenario_id"]] = (s, t, c, "heldout2")
    qual_only = {}
    for sid, ov, b, p in (("Q2_C2_FOREIGN_FAILURE_AFTER_WEAK_ACK_B1_P3", "C2_FOREIGN_FAILURE_AFTER_WEAK_ACK", "B1", "P3"),
                          ("Q2_C2_FOREIGN_FAILURE_AFTER_REORDERED_CALLBACK_B1_P1", "C2_FOREIGN_FAILURE_AFTER_REORDERED_CALLBACK", "B1", "P1")):
        s0, t0, c0 = base[(b, p)]
        qual_only[sid] = ({"barrier": b, "overlay": ov, "profile": p, "scenario_id": sid},
                          {**t0, "scenario_id": sid, "overlay": ov, "note": "qualification only"}, c0, "qualification")
    units, counters = [], {"VX": 0, "VQ": 0}

    def add(family, sc, tr, contract, group, arm, extra):
        label, slot, clf, ex, mode, policy = arm
        counters[family] += 1
        name = f"{family}-{counters[family]:04d}"
        uid = unit_id(family, sc["scenario_id"], slot, ex, mode)
        attempt_dirname(attempt_id(uid, 1))
        units.append({"unit_id": uid, "name": name, "family": family, "arm_label": label, "scenario": sc, "truth": tr,
                      "classifier_arm": clf, "executor": ex, "r2_mode": mode, "policy": policy,
                      "endpoint_contract": contract, "ids": ids(name), "group": group, **extra})

    exposure = {"hist": "DEV", "F7": "DEV", "extended_dev": "DEV (bl2/bl3)", "heldout1": "SEEN in bl5; v3 designed after it",
                "heldout2": "HELD-OUT-2: never run before bl6"}
    for sid in sorted(scen):
        sc, tr, c, g = scen[sid]
        for arm in ARMS:
            add("VX", sc, tr, c, g, arm, {"exposure": exposure[g]})
    arm_of = {a[0]: a for a in ARMS}
    for sid, label in QUAL:
        sc, tr, c, g = qual_only.get(sid) or scen[sid]
        if g == "heldout2":
            raise SystemExit("qualification must not use held-out-2")
        add("VQ", sc, tr, c, g, arm_of[label], {"exposure": "QUALIFICATION (not an experiment result)"})
    uids = [u["unit_id"] for u in units]
    assert len(uids) == len(set(uids))
    vx = [u for u in units if u["family"] == "VX"]
    assert len(vx) == 2848, len(vx)
    summary = {"VX_units": len(vx), "VQ_units": len(units) - len(vx), "scenarios": len(scen), "by_group_scenarios": {}}
    for s, t, c, g in scen.values():
        summary["by_group_scenarios"][g] = summary["by_group_scenarios"].get(g, 0) + 1
    out = {"status": "CANDIDATE bl6 - frozen only by the bl6 freeze record", "source_cells_bl3_sha256": digest,
           "summary": summary, "qualification_unit_ids": [u["unit_id"] for u in units if u["family"] == "VQ"], "units": units}
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
