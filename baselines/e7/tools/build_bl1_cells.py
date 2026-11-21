"""Build the CCF-A rule-level baseline unit table (package bl1). Read-only on the 2b table.

Source: the frozen 2b cell table private/cells_p3.json (SHA-256 checked below). Its 64 P3-A
scenarios of the FULL / v1 units (52 historical + 12 F7) are taken with scenario, truth and
endpoint contract byte-for-byte unchanged.
Arms (fixed before any run; see frozen/ARM_DEFINITIONS_bl1.md):
  INTENT-LOG  executor BL, policy INTENT_LOG  (Beldi OSDI'20 / Olive OSDI'16 rules)
  RIFL-C      executor BL, policy RIFL_C      (RIFL SOSP'15 client-side rules)
Both run on the frozen v1 send path, without classifier, planner or send gate (like CONV);
the v1 executor has no R2-A / R2-B key mode.
Family BLX: 64 scenarios x 2 arms = 128 units, all new runs.
Family BLQ: 7 runner-qualification units (never experiment results).
Unit names and identifiers are fresh (BLX-nnnn / BLQ-nnnn); no identifier of a 2b unit is reused.
Usage: python -I build_bl1_cells.py <cells_p3.json> <out cells_bl1.json>
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runner"))
from p3lib import attempt_dirname, attempt_id, unit_id  # noqa: E402

CELLS_P3_SHA256 = "c244199721f4982bbda5aacf2b09e837e19a6b1395ab62a6dcdbc7025aa190b6"  # frozen 2b table
ARMS = (("INTENT-LOG", "INTENT_LOG"), ("RIFL-C", "RIFL_C"))
QUAL = (  # (scenario_id, arm label): pre-dispatch, re-execution, logged failure, ambiguous + late
    ("B1_P0", "INTENT-LOG"),          # original, logged accept, superseded version, keyed lookup
    ("B4_P2", "INTENT-LOG"),
    ("F7_B4_P1", "INTENT-LOG"),
    ("F2_FOREIGN_ACCEPT_P2", "INTENT-LOG"),
    ("B2_LATE_P0", "RIFL-C"),
    ("B6_P3", "RIFL-C"),
    ("F5_SUPERSEDED_VERSION_P1", "RIFL-C"),
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
        if u["family"] == "P3A" and u["classifier_arm"] == "FULL" and u["executor"] == "v1":
            base[u["scenario"]["scenario_id"]] = u
    scenarios = sorted(base)
    assert len(scenarios) == 64, len(scenarios)
    units, counters = [], {"BLX": 0, "BLQ": 0}

    def add(family, src_unit, label, policy, extra):
        counters[family] += 1
        name = f"{family}-{counters[family]:04d}"
        sid = src_unit["scenario"]["scenario_id"]
        uid = unit_id(family, sid, policy, "BL", None)
        attempt_dirname(attempt_id(uid, 1))
        units.append({"unit_id": uid, "name": name, "family": family, "arm_label": label,
                      "scenario": src_unit["scenario"], "truth": src_unit["truth"],
                      "classifier_arm": "-", "executor": "BL", "r2_mode": None,
                      "policy": policy, "endpoint_contract": src_unit["endpoint_contract"], "ids": ids(name),
                      "group": src_unit["group"], "source_2b_unit": src_unit["unit_id"], **extra})

    for sid in scenarios:
        for label, policy in ARMS:
            add("BLX", base[sid], label, policy,
                {"exposure": "NEW: arm never run; scenario and truth SEEN in 2b (FULL/-C_disp/-U v1, CONV on F7)"})
    policy_of = dict(ARMS)
    for sid, label in QUAL:
        add("BLQ", base[sid], label, policy_of[label], {"exposure": "QUALIFICATION (not an experiment result)"})
    uids = [u["unit_id"] for u in units]
    assert len(uids) == len(set(uids))
    blx = [u for u in units if u["family"] == "BLX"]
    summary = {"BLX_units": len(blx), "BLQ_units": len(units) - len(blx), "by_arm_group": {}}
    for u in blx:
        k = f"{u['arm_label']}|{u['group']}"
        summary["by_arm_group"][k] = summary["by_arm_group"].get(k, 0) + 1
    assert len(blx) == 128
    out = {"status": "CANDIDATE bl1 - frozen only by the bl1 freeze record", "source_cells_p3_sha256": digest,
           "summary": summary, "qualification_unit_ids": [u["unit_id"] for u in units if u["family"] == "BLQ"],
           "units": units}
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
