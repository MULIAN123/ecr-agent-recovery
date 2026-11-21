"""Build the CONV+ / CONV+g unit table (ECR r9.6 Batch A, package cp1). Read-only on the 2b table.

Source: the frozen 2b cell table private/cells_p3.json (SHA-256 checked below). Its 64 P3-A
scenarios of the FULL / v2 units (52 historical + 12 F7) are taken with scenario and truth
byte-for-byte unchanged; R2-A and R2-B are both kept.
Arms (fixed before any run; see frozen/ARM_DEFINITIONS_cp1.md):
  CONV+g  classifier_arm CP_SEQ_DISP (FULL minus D1, D2), executor v2   (frozen src/, validator unchanged)
  CONV+   classifier_arm CP_SEQ_DISP (FULL minus D1, D2), executor v2cp (src_cp/: validator minus D3)
Family CPX: 64 scenarios x 2 modes x 2 arms = 256 units, all new runs.
Family CPQ: 8 runner-qualification units (never experiment results), covering the new executor key,
the new arm directory, both modes, and the F4 / F6 fence / F6 idempotent / F7 / clean paths.
Unit names and identifiers are fresh (CPX-nnnn / CPQ-nnnn), so no identifier of a 2b unit is reused.
Usage: python -I build_cp1_cells.py <cells_p3.json> <out cells_cp1.json>
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runner"))
from p3lib import attempt_dirname, attempt_id, unit_id  # noqa: E402

CELLS_P3_SHA256 = "c244199721f4982bbda5aacf2b09e837e19a6b1395ab62a6dcdbc7025aa190b6"  # frozen 2b table
ARMS = (("CONV+g", "CP_SEQ_DISP", "v2"), ("CONV+", "CP_SEQ_DISP", "v2cp"))
QUAL = (  # (scenario_id, arm label, mode)
    ("B1_P0", "CONV+g", "R2A"),
    ("B2_LATE_P3", "CONV+", "R2B"),
    ("F4_REORDERED_CALLBACK_P1", "CONV+g", "R2B"),
    ("F6_LOST_DISPATCH_FAILURE_P1", "CONV+g", "R2A"),
    ("F6_LOST_DISPATCH_FAILURE_P3", "CONV+", "R2B"),
    ("F6_LOST_DISPATCH_FAILURE_P2", "CONV+", "R2A"),
    ("F7_B4_P0", "CONV+", "R2B"),
    ("B5_P2", "CONV+g", "R2B"),
)


def ids(name: str) -> dict:
    return {"identity": {"artifactId": f"{name}-artifact", "artifactSetId": f"{name}-set", "attemptId": f"{name}-a1",
                         "bindingGeneration": 1, "deliverableId": f"{name}-deliverable", "queueBindingId": f"{name}-binding",
                         "taskId": f"{name}-task", "taskStateVersion": 1},
            "logical_operation_id": f"{name}-op", "queue_item_id": f"{name}-queue", "transport_request_id": f"{name}-t1"}


def main():
    src = Path(sys.argv[1])
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    if digest != CELLS_P3_SHA256:
        raise SystemExit(f"source table is not the frozen 2b cells_p3.json: {digest}")
    p3 = json.loads(src.read_text())
    base = {}
    for u in p3["units"]:
        if u["family"] == "P3A" and u["classifier_arm"] == "FULL" and u["executor"] == "v2":
            base[(u["scenario"]["scenario_id"], u["r2_mode"])] = u
    scenarios = sorted({s for s, _ in base})
    assert len(scenarios) == 64 and len(base) == 128, (len(scenarios), len(base))
    for sid in scenarios:  # scenario and truth must not depend on the mode
        a, b = base[(sid, "R2A")], base[(sid, "R2B")]
        assert a["scenario"] == b["scenario"] and a["truth"] == b["truth"] and a["endpoint_contract"] == b["endpoint_contract"]
    units, counters = [], {"CPX": 0, "CPQ": 0}

    def add(family, src_unit, label, classifier, executor, extra):
        counters[family] += 1
        name = f"{family}-{counters[family]:04d}"
        sid = src_unit["scenario"]["scenario_id"]
        uid = unit_id(family, sid, classifier, executor, src_unit["r2_mode"])
        attempt_dirname(attempt_id(uid, 1))
        units.append({"unit_id": uid, "name": name, "family": family, "arm_label": label,
                      "scenario": src_unit["scenario"], "truth": src_unit["truth"],
                      "classifier_arm": classifier, "executor": executor, "r2_mode": src_unit["r2_mode"],
                      "policy": "EBRQ2", "endpoint_contract": src_unit["endpoint_contract"], "ids": ids(name),
                      "group": src_unit["group"], "source_2b_unit": src_unit["unit_id"], **extra})

    for sid in scenarios:
        for mode in ("R2A", "R2B"):
            for label, classifier, executor in ARMS:
                add("CPX", base[(sid, mode)], label, classifier, executor,
                    {"exposure": "NEW: arm never run; scenario and truth SEEN in 2b (FULL/-C_disp/-U v2)"})
    arm_of = {label: (c, e) for label, c, e in ARMS}
    for sid, label, mode in QUAL:
        add("CPQ", base[(sid, mode)], label, *arm_of[label], {"exposure": "QUALIFICATION (not an experiment result)"})
    uids = [u["unit_id"] for u in units]
    assert len(uids) == len(set(uids))
    cpx = [u for u in units if u["family"] == "CPX"]
    summary = {"CPX_units": len(cpx), "CPQ_units": len(units) - len(cpx),
               "by_arm_mode_group": {}}
    for u in cpx:
        k = f"{u['arm_label']}|{u['r2_mode']}|{u['group']}"
        summary["by_arm_mode_group"][k] = summary["by_arm_mode_group"].get(k, 0) + 1
    assert len(cpx) == 256
    out = {"status": "CANDIDATE cp1 - frozen only by the cp1 freeze record", "source_cells_p3_sha256": digest,
           "summary": summary, "qualification_unit_ids": [u["unit_id"] for u in units if u["family"] == "CPQ"],
           "units": units}
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
