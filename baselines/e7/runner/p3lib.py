"""Platform-independent protocol logic for the phase-3 runner (identifiers, attempt states,
retry and stop rules). Imported by run_r2.py; self-tested offline.

Unit states: NOT_STARTED, VALID_COMPLETED, INFRA_FAILED, INTEGRITY_UNKNOWN
(+ attempt sub-state OBSERVED_PENDING_REPLAY: observed and complete, replay not yet done;
 it never enters the main analysis).
Rules (phase-3 plan v2, section 4):
  * no automatic retry; the first attempt that is not OBSERVED stops the batch;
  * a continuation batch may run NOT_STARTED units and, only if a continuation authorization
    file lists them, add exactly one extra attempt to units whose single attempt is INFRA_FAILED
    (or INTEGRITY_UNKNOWN after an investigation explicitly authorizes it);
  * attempts are never deleted or overwritten; attempt numbers are global per unit.
"""
from __future__ import annotations

import re

MAX_ATTEMPTS = 2
STATES = ("NOT_STARTED", "VALID_COMPLETED", "INFRA_FAILED", "INTEGRITY_UNKNOWN")


def unit_id(family: str, scenario_id: str, classifier: str, executor: str, mode: str | None) -> str:
    return f"{family}:{scenario_id}:{classifier}:{executor}:{mode or '-'}"


def attempt_id(uid: str, n: int) -> str:
    if n < 1:
        raise ValueError("attempt numbers start at 1")
    return f"{uid}#a{n}"


def attempt_dirname(aid: str) -> str:
    """Filesystem-safe, injective name for an attempt (':' and '#' replaced, never reused)."""
    if "__" in aid or "--" in aid:
        raise ValueError("attempt id must not contain '__' or '--' (keeps the mapping injective)")
    name = aid.replace(":", "__").replace("#", "--")
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", name):
        raise ValueError("unsafe attempt id " + aid)
    return name


def evaluate_attempt(f: dict) -> tuple[str, list[str]]:
    """Classify one attempt from its observed facts.

    f keys (all required):
      harness_integrity_ok   barrier/injection receipts, deployed hashes, identities all verified
      recovery_exit          int | None (None: recovery never started)
      final_state_present    bool
      planner_records        "COMPLETE" | "INCOMPLETE"
      planner_failures       int  (calls with non-zero exit, signal or timeout)
      infra_events           list[str] (endpoint/scorer process failure, timeout, VM interruption)
      scorer_faults          list[str] (from either scorer)
      replay                 "PENDING" | "CONSISTENT" | "STOP_INVESTIGATE"
    """
    reasons = []
    if not f["harness_integrity_ok"]:
        return "INTEGRITY_UNKNOWN", ["harness integrity check failed"]
    if f["planner_records"] != "COMPLETE":
        return "INTEGRITY_UNKNOWN", ["planner-call records incomplete or inconsistent"]
    if f["scorer_faults"]:
        return "INTEGRITY_UNKNOWN", ["scorer integrity faults: " + ",".join(f["scorer_faults"])]
    failed_run = f["recovery_exit"] != 0 or not f["final_state_present"]
    if failed_run:
        if f["planner_failures"] > 0:
            reasons.append(f"planner infrastructure failure ({f['planner_failures']} call(s))")
        reasons += f["infra_events"]
        if reasons:
            return "INFRA_FAILED", reasons
        return "INTEGRITY_UNKNOWN", ["recovery failed without a recorded infrastructure cause (investigate: possible implementation defect)"]
    if f["planner_failures"] > 0 or f["infra_events"]:
        return "INTEGRITY_UNKNOWN", ["run completed although an infrastructure failure was recorded"]
    if f["replay"] == "PENDING":
        return "OBSERVED_PENDING_REPLAY", []
    if f["replay"] == "CONSISTENT":
        return "VALID_COMPLETED", []
    return "INTEGRITY_UNKNOWN", ["planner replay not consistent"]


def unit_state(attempt_states: list[str]) -> str:
    """attempt_states in attempt order. A unit is VALID_COMPLETED if any attempt is; otherwise the
    state of its latest attempt (OBSERVED_PENDING_REPLAY counts as not yet valid)."""
    if not attempt_states:
        return "NOT_STARTED"
    if "VALID_COMPLETED" in attempt_states:
        return "VALID_COMPLETED"
    last = attempt_states[-1]
    return last if last in STATES else "INTEGRITY_UNKNOWN"


def may_add_attempt(attempt_states: list[str], authorized: bool) -> tuple[bool, str]:
    if not attempt_states:
        return True, "first attempt"
    if len(attempt_states) >= MAX_ATTEMPTS:
        return False, "attempt limit reached"
    last = attempt_states[-1]
    if last == "VALID_COMPLETED" or last == "OBSERVED_PENDING_REPLAY":
        return False, "unit already observed"
    if not authorized:
        return False, "no continuation authorization for this unit (no automatic retry)"
    if last in ("INFRA_FAILED", "INTEGRITY_UNKNOWN"):
        return True, f"authorized extra attempt after {last}"
    return False, "unexpected state"


def batch_should_stop(attempt_state: str) -> bool:
    return attempt_state not in ("OBSERVED_PENDING_REPLAY", "VALID_COMPLETED")


SAME_OK = {"IDENTICAL_BYTES"}
CROSS_OK = {"IDENTICAL_BYTES", "EQUAL_EXCEPT_RUNTIME_VERSION"}


def finalize_attempt(state: str, calls_recorded: int, same_rows: list[dict], cross_rows: list[dict],
                     unattributed_problem: bool) -> tuple[str, list[str]]:
    """Turn OBSERVED_PENDING_REPLAY into VALID_COMPLETED or INTEGRITY_UNKNOWN from the replay rows of
    this attempt (same runtime and cross runtime). Other states are returned unchanged.
    Every recorded call must appear exactly once in each replay; same-runtime replay must be
    byte-identical; cross-runtime replay may differ only in the runtime version field."""
    if state != "OBSERVED_PENDING_REPLAY":
        return state, []
    reasons = []
    if unattributed_problem:
        reasons.append("a replay report has incomplete records that cannot be attributed to an attempt")
    for name, rows, ok in (("same-runtime", same_rows, SAME_OK), ("cross-runtime", cross_rows, CROSS_OK)):
        if len(rows) != calls_recorded or len({r["call_id"] for r in rows}) != len(rows):
            reasons.append(f"{name} replay covers {len(rows)} of {calls_recorded} recorded calls")
        bad = sorted({r["verdict"] for r in rows if r["verdict"] not in ok})
        if bad:
            reasons.append(f"{name} replay verdicts {bad}")
    return ("INTEGRITY_UNKNOWN", reasons) if reasons else ("VALID_COMPLETED", [])
