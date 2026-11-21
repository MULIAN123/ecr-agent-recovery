"""Attempt classification for the ac1 runner (platform independent, tested offline).

Plan r2, section 1 and section 10: three separate judgements are recorded per unit (execution
validity, S1-S4 each, recovery); this module decides only EXECUTION VALIDITY and whether the batch
stops. It never looks at S1-S4 or L: a safety violation or a recovery loss is an observation.

States (compatible with p3lib.batch_should_stop and tools/finalize_states.py):
  OBSERVED_PENDING_REPLAY  execution valid; observations may include abnormal exits, safety
                           violations, recovery losses (recorded by the diagnostic scorer)
  INFRA_FAILED             infrastructure failure (planner bridge, timeouts, endpoint)  -> stop
  INTEGRITY_UNKNOWN        execution invalid: receipts, snapshot timing, provenance, pause not
                           reached, unexpected scorer faults                           -> stop
Difference from the frozen p3lib.evaluate_attempt (design record DC1): a non-zero exit of the
LAST recovery with no planner or infrastructure failure, and a missing final.json in that case,
are observations (abnormal exit), not a stop. Primary-scorer faults listed in the unit's
expected_primary_faults are recorded, not gating.
"""
from __future__ import annotations


def evaluate_attempt(f: dict, unit: dict) -> tuple[str, list[str]]:
    if not f["harness_integrity_ok"]:
        detail = "; ".join(f.get("errors", [])) or "harness integrity check failed"
        return "INTEGRITY_UNKNOWN", ["execution invalid: " + detail]
    if f.get("pause_not_reached"):
        return "INTEGRITY_UNKNOWN", [f"pause point not reached: {f['pause_not_reached']}"]
    if any(not s.get("valid") for s in f.get("snapshots", [])):
        return "INTEGRITY_UNKNOWN", ["pre-recovery snapshot timing invalid"]
    if f["planner_failures"] > 0:
        return "INFRA_FAILED", [f"planner infrastructure failure ({f['planner_failures']} call(s))"] + list(f["infra_events"])
    if f["infra_events"]:
        return "INFRA_FAILED", list(f["infra_events"])
    if f["planner_records"] != "COMPLETE":
        return "INTEGRITY_UNKNOWN", ["planner-call records incomplete or inconsistent"]
    if f["scorer_faults"]:
        return "INTEGRITY_UNKNOWN", ["unexpected scorer faults: " + ",".join(f["scorer_faults"])]
    if f["recovery_exit"] is None:
        return "INTEGRITY_UNKNOWN", ["last recovery has no exit status"]
    if f.get("replay", "PENDING") == "PENDING":
        return "OBSERVED_PENDING_REPLAY", (["abnormal exit observed: " + str(f["abnormal_exits"])] if f.get("abnormal_exits") else [])
    if f["replay"] == "CONSISTENT":
        return "VALID_COMPLETED", []
    return "INTEGRITY_UNKNOWN", ["planner replay not consistent"]
