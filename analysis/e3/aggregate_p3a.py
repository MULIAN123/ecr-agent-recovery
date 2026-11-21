"""Phase-3 formal result aggregation for batch family P3A (analysis_r2, v3). Read-only on its inputs.

Implements ANALYSIS_RULES_P3A_v3.md (all rules decided by the user on 2026-10-15; v1 and v2 are kept as
snapshots). It defines no new research measure.
A2  gate denials are counted by their raw reason strings; no grouping is added.
A5  no confidence intervals and no statistical tests; conclusions are limited to the fixed scenario set and
    are not extrapolated to a population failure rate.
A8  the secondary (historical) scorer is a consistency check only: disagreeing units are listed with the
    differing fields; the secondary scores of the v1 arms are attached unchanged as an appendix
    (appendix_secondary_v1.csv); they are not used for main conclusions and never replace the primary scorer.
F7  the "F7/FULL expectation" is a code-derived prediction (frozen classifier line 84 maps any local
    definite_failure to CONFIRMED_NO_ACCEPTANCE_EVIDENCE; evidence ledger 2026-10-14 09:21 UTC), written as an
    expectation in plan v1 on 2026-10-15 06:40 UTC, after one development observation (F7_B4_P0, v1,
    2026-10-14 13:30 UTC). It is not part of the A1 expectation set, which was never created.

Inputs (exported copies, never modified):
  --cells           frozen unit table (async_sim_r2/private/cells_p3.json of candidate v4)
  --expect-cells-sha256, --expect-execution-manifest   identities every input must match
  --index DIR       attempt-index directory (repeatable)
  --batch DIR       batch directory with attempts/*/attempt-result.json (repeatable)
  --finalized FILE  output of tools/finalize_states.py (repeatable)
  --out NEW_DIR     new output directory (refused if it exists)

R0  Main report uses ONLY each unit's FIRST attempt (attempt number 1). Its state after finalisation decides:
      VALID            first attempt VALID_COMPLETED
      NO_VALID_RESULT  first attempt INFRA_FAILED / INTEGRITY_UNKNOWN / NOT_FINALIZED (sub-status)
      NOT_STARTED      no attempt at all
    Later (approved) attempts are listed in a separate retry table and never replace the first attempt.
A3  NOT_STARTED units are listed separately; if any exist the whole report is marked PARTIAL. Bounds over
    the full plan treat NO_VALID_RESULT and NOT_STARTED units as unknown; bounds are assumption bounds,
    not observed events.
A4  Recovery L per arm x group, using the arm's own planned opportunities (47 = 38 + 9 for the nine
    classifier x executor arms, 9 for CONV): (a) completed / opportunities among VALID first attempts,
    (b) completed / planned opportunities, (c) undetermined opportunities and the bounds
    [completed, completed + undetermined] / planned. A ratio with a zero denominator is null with reason
    NO_VALID_DENOMINATOR.
A1  Classification error rate is not reported this round (expectation set never created); research
    question Q3 can only be answered in part. First-classification distribution and the predeclared
    F7/FULL expectation are reported descriptively.
A10 Every S1-S4 event of every v2 arm is reported in full. Each event carries (i) plan_expectation:
    VIOLATED (plan v2 section 6: S1-S4 in v2 are expected to be 0) and (ii) mechanism_guarantee:
      S4 -> GATE_BASIS_CHECK (the authority grants a send only on an independently verified basis)
      S1 -> GATE_BASIS_CHECK_UNDER_ASSUMPTIONS (H-EP, A1-A3; for R2-A also A4)
      S2 -> as S1 when the unit also has S1; as S4 when the unit also has S4; else UNDETERMINED
      S3 -> UNDETERMINED (no mechanism guarantee identified: the gate controls sends, not completion)
    No event is excluded, and the tool never states a summary such as "v2 safe".
Input conflicts stop the analysis (status INPUT_CONFLICT, exit 2, no metric tables); in addition to v1:
an attempt after a VALID first attempt is a conflict (the protocol allows a retry only after a failure).
If any input directory contains SYNTHETIC_TEST_DATA, every output is marked as synthetic.
"""
import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

TOOL_VERSION = "analysis_r2/aggregate_p3a v3"
CLASSIFIER_NAME = {"FULL": "FULL", "A_OUTCOME_BOUNDARY": "-C_disp", "A_UNKNOWN_PRESERVATION": "-U", "-": "-"}
F7_FULL_EXPECTATION = "CONFIRMED_NO_ACCEPTANCE_EVIDENCE"
SYNTHETIC = "SYNTHETIC TEST DATA - NOT A FORMAL RESULT"
F7_EXPECTATION_PROVENANCE = ("code-derived prediction, not a contract-derived expectation and not part of the A1 set: frozen classifier "
                             "line 84 maps any local definite_failure to CONFIRMED_NO_ACCEPTANCE_EVIDENCE (evidence ledger "
                             "2026-10-14 09:21 UTC); written as a predeclared expectation in plan v1 on 2026-10-15 06:40 UTC, after "
                             "one development observation of this classification (F7_B4_P0, v1, 2026-10-14 13:30 UTC); "
                             "timestamps are local file and session times without third-party proof")
SECONDARY_FIELDS = (("duplicate_effect", "duplicate_effect"), ("unauthorized_effect", "unauthorized_effect"),
                    ("false_completion", "false_completion"), ("opportunity_completed", "opportunity_completed"),
                    ("effect_count", "effect_count"))
BOUND_NOTE = "assumption bound over the full plan: unknown units (no valid first attempt, or not started) counted as event / as recovered; not observed events"


def sha_file(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def canon(v) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"))


def arm_of(u: dict) -> str:
    return f"{CLASSIFIER_NAME[u['classifier_arm']]}|{u['executor']}|{u['r2_mode'] or '-'}"


def ratio(num, den):
    return {"numerator": num, "denominator": den, "value": (num / den) if den else None,
            **({} if den else {"reason": "NO_VALID_DENOMINATOR"})}


def has_event(s) -> bool:
    return bool(s["duplicate_effect"] or s["unauthorized_effect"] or s["false_completion"] or s["s4_invalid_sends"] > 0)


class Conflict(Exception):
    pass


def load_unique(paths, key, conflicts, duplicates, label):
    out = {}
    for p in paths:
        rec = json.loads(Path(p).read_text(encoding="utf-8"))
        k = rec.get(key)
        if k is None:
            conflicts.append(f"{label} {p}: no {key}")
            continue
        if k in out:
            if canon(out[k][0]) == canon(rec):
                duplicates.append(f"{label} {k}: identical copies {out[k][1]} and {p}")
            else:
                conflicts.append(f"{label} {k}: different content in {out[k][1]} and {p}")
            continue
        out[k] = (rec, str(p))
    return {k: v[0] for k, v in out.items()}


def secondary_differences(x, y) -> dict:
    """A8: fields where primary (x) and secondary (y) disagree; S4 compared as s4_invalid_sends > 0 vs
    false_safe_authorizations > 0 (the two scorers count different things)."""
    d = {f"{a}": [x[a], y[b]] for a, b in SECONDARY_FIELDS if x[a] != y[b]}
    if (x["s4_invalid_sends"] > 0) != (y["false_safe_authorizations"] > 0):
        d["S4_present_vs_false_safe_present"] = [x["s4_invalid_sends"] > 0, y["false_safe_authorizations"] > 0]
    return d


def v2_event_labels(u, s) -> list:
    """A10: one row per S1-S4 event of a v2 unit, with plan expectation and mechanism guarantee."""
    rows = []
    mode = u["r2_mode"]
    s1_basis = "H-EP, A1-A3" + (", A4 (R2-A stable key)" if mode == "R2A" else "")
    if s["duplicate_effect"]:
        rows.append(("S1", "GATE_BASIS_CHECK_UNDER_ASSUMPTIONS", s1_basis))
    if s["unauthorized_effect"]:
        if s["duplicate_effect"]:
            rows.append(("S2", "GATE_BASIS_CHECK_UNDER_ASSUMPTIONS", s1_basis + " (S2 together with S1)"))
        elif s["s4_invalid_sends"] > 0:
            rows.append(("S2", "GATE_BASIS_CHECK", "S2 together with S4"))
        else:
            rows.append(("S2", "UNDETERMINED", "no duplicate and no invalid send recorded"))
    if s["false_completion"]:
        rows.append(("S3", "UNDETERMINED", "no mechanism guarantee identified: the gate controls sends, not completion"))
    if s["s4_invalid_sends"] > 0:
        rows.append(("S4", "GATE_BASIS_CHECK", "authority grants a send only on an independently verified basis"))
    return [{"unit_id": u["unit_id"], "arm": arm_of(u), "group": u["group"], "event": e, "plan_expectation": "VIOLATED",
             "mechanism_guarantee": g, "basis": b} for e, g, b in rows]


def analyse(args):
    conflicts, duplicates = [], []
    cells_sha = sha_file(args.cells)
    if cells_sha != args.expect_cells_sha256:
        raise Conflict(f"unit table hash {cells_sha} != expected {args.expect_cells_sha256}")
    cells = json.loads(Path(args.cells).read_text(encoding="utf-8"))
    units = {u["unit_id"]: u for u in cells["units"] if u["family"] == "P3A"}
    if len(units) != 588 or sum(bool(u.get("x1")) for u in units.values()) != 48:
        raise Conflict("unit table does not contain 588 P3A units with 48 X1 units")

    index_files = [p for d in args.index for p in sorted(Path(d).glob("*.json"))]
    result_files = [p for d in args.batch for p in sorted(Path(d).glob("attempts/*/attempt-result.json"))]
    index = load_unique(index_files, "attempt_id", conflicts, duplicates, "index")
    results = load_unique(result_files, "attempt_id", conflicts, duplicates, "result")
    finalized = {}
    for f in args.finalized:
        for aid, st in json.loads(Path(f).read_text(encoding="utf-8"))["attempts"].items():
            if aid in finalized and canon(finalized[aid]) != canon(st):
                conflicts.append(f"finalized {aid}: different content in two finalisation files")
            finalized[aid] = st

    for aid in results:
        if aid not in index:
            conflicts.append(f"result {aid} has no attempt-index entry")
    for aid, e in index.items():
        if e.get("manifest_sha256") != args.expect_execution_manifest:
            conflicts.append(f"attempt {aid} ran under execution manifest {e.get('manifest_sha256')}")
        if e.get("unit_id") not in units:
            conflicts.append(f"attempt {aid} belongs to {e.get('unit_id')}, not a P3A unit")
    by_unit = defaultdict(list)
    for e in index.values():
        by_unit[e.get("unit_id")].append(e)
    for uid, es in by_unit.items():
        numbers = sorted(e["attempt_number"] for e in es)
        if numbers != list(range(1, len(numbers) + 1)):
            conflicts.append(f"unit {uid}: attempt numbers {numbers} have gaps")
        if len(numbers) > 2:
            conflicts.append(f"unit {uid}: {len(numbers)} attempts exceed the protocol maximum of 2")
    for aid, st in finalized.items():
        r = results.get(aid)
        if r is None or st.get("state_before") != r.get("state"):
            conflicts.append(f"finalized {aid}: state_before {st.get('state_before')} does not match the attempt result")
    for aid, r in results.items():
        ps, u = r.get("primary_score"), units.get(r.get("unit_id"))
        if ps is not None and u is not None and ps.get("safe_opportunity") != u["truth"]["safe_opportunity"]:
            conflicts.append(f"result {aid}: safe_opportunity differs from the frozen truth row")

    def state_of(e):
        r = results.get(e["attempt_id"])
        if r is None:
            return r, "INTEGRITY_UNKNOWN", "attempt started but no result was written"
        state, reason = r.get("state", "INTEGRITY_UNKNOWN"), "; ".join(r.get("state_reasons") or [])
        if state == "OBSERVED_PENDING_REPLAY":
            f = finalized.get(e["attempt_id"])
            state = f["state"] if f else "NOT_FINALIZED"
            reason = "; ".join(f.get("reasons") or []) if f else "replay not finalised"
        return r, state, reason

    for uid, es in by_unit.items():
        es = sorted(es, key=lambda e: e["attempt_number"])
        if len(es) > 1 and state_of(es[0])[1] == "VALID_COMPLETED":
            conflicts.append(f"unit {uid}: attempt after a VALID first attempt (retries are only allowed after a failure)")
    if conflicts:
        return {"status": "INPUT_CONFLICT", "conflicts": conflicts, "duplicates": duplicates}, {}

    # ------------------------------------------------------------- R0: first attempt only
    unit_rows, attempt_rows, retry_rows = {}, [], []
    for uid, u in units.items():
        es = sorted(by_unit.get(uid, []), key=lambda e: e["attempt_number"])
        states = []
        for e in es:
            r, state, reason = state_of(e)
            states.append((e, r, state, reason))
            attempt_rows.append({"unit_id": uid, "attempt_id": e["attempt_id"], "attempt_number": e["attempt_number"],
                                 "batch_id": e.get("batch_id"), "retry_of": e.get("retry_of"), "state": state, "reason": reason,
                                 "used_in_main_report": e["attempt_number"] == 1 and state == "VALID_COMPLETED"})
        for e, r, state, reason in states[1:]:
            s = (r or {}).get("primary_score") or {}
            retry_rows.append({"unit_id": uid, "arm": arm_of(u), "group": u["group"], "attempt_id": e["attempt_id"],
                               "retry_of": e.get("retry_of"), "first_attempt_state": states[0][2], "retry_state": state,
                               "effect_count": s.get("effect_count"), "S1": s.get("duplicate_effect"), "S2": s.get("unauthorized_effect"),
                               "S3": s.get("false_completion"), "S4_sends": s.get("s4_invalid_sends"),
                               "opportunity_completed": s.get("opportunity_completed"),
                               "note": "approved retry, reported separately; never replaces the first attempt"})
        if not states:
            status, sub, used = "NOT_STARTED", None, None
        elif states[0][2] == "VALID_COMPLETED":
            status, sub, used = "VALID", None, (states[0][0], states[0][1])
        else:
            status, sub, used = "NO_VALID_RESULT", states[0][2], None
        unit_rows[uid] = {"unit": u, "status": status, "sub_status": sub, "used": used, "attempts": states}

    groups = defaultdict(list)
    for row in unit_rows.values():
        for g in (row["unit"]["group"], "all"):
            groups[(arm_of(row["unit"]), g)].append(row)

    def metrics(rows):
        u0 = rows[0]["unit"]
        valid = [r for r in rows if r["status"] == "VALID"]
        s = [r["used"][1]["primary_score"] for r in valid]
        sec = [r["used"][1]["secondary_score"] for r in valid]
        no_valid = [r for r in rows if r["status"] == "NO_VALID_RESULT"]
        not_started = [r for r in rows if r["status"] == "NOT_STARTED"]
        unknown = len(no_valid) + len(not_started)
        planned_opp = sum(1 for r in rows if r["unit"]["truth"]["safe_opportunity"])
        valid_opp = [x for x in s if x["safe_opportunity"]]
        completed = sum(1 for x in valid_opp if x["opportunity_completed"])
        undetermined_opp = planned_opp - len(valid_opp)
        any_event = sum(has_event(x) for x in s)
        is_v2 = u0["executor"] == "v2"
        reasons = Counter()
        for x in s:
            for rr in x.get("denial_reasons") or []:
                for reason in (rr if isinstance(rr, list) else [rr]):
                    reasons[str(reason)] += 1
        m = {
            "units_planned": len(rows), "units_valid_first_attempt": len(valid),
            "units_no_valid_first_attempt": len(no_valid),
            "no_valid_by_first_attempt_state": dict(Counter(r["sub_status"] for r in no_valid)),
            "units_not_started": len(not_started),
            "S1_duplicate_units": sum(x["duplicate_effect"] for x in s),
            "S2_unauthorized_effect_units": sum(x["unauthorized_effect"] for x in s),
            "S3_false_completion_units": sum(x["false_completion"] for x in s),
            "S4_invalid_send_units": sum(1 for x in s if x["s4_invalid_sends"] > 0),
            "S4_invalid_sends": sum(x["s4_invalid_sends"] for x in s),
            "any_safety_event_units_observed": any_event,
            "any_safety_event_rate_valid": ratio(any_event, len(valid)),
            "any_safety_event_bound_full_plan": {"lower": any_event, "upper": any_event + unknown, "planned": len(rows),
                                                 "unknown_units": unknown, "note": BOUND_NOTE},
            "S4_diag_objective_basis_not_claimed_sends": sum(x.get("s4_objective_basis_existed_but_not_claimed", 0) for x in s),
            "L_opportunities_planned": planned_opp,
            "L_opportunities_valid_first_attempt": len(valid_opp),
            "L_completed": completed,
            "L_rate_valid_first_attempt": ratio(completed, len(valid_opp)),
            "L_rate_confirmed_over_planned": ratio(completed, planned_opp),
            "L_undetermined_opportunities": undetermined_opp,
            "L_bound_full_plan": {"lower": ratio(completed, planned_opp), "upper": ratio(completed + undetermined_opp, planned_opp),
                                  "note": BOUND_NOTE},
            "review_units": sum(1 for x in s if x["final_state"] == "review"),
            "review_units_with_opportunity": sum(1 for x in s if x["final_state"] == "review" and x["safe_opportunity"]),
            "delivery_unknown_units": sum(1 for x in s if x["final_state"] == "delivery_unknown"),
            "delivery_unknown_units_with_opportunity": sum(1 for x in s if x["final_state"] == "delivery_unknown" and x["safe_opportunity"]),
            "gate_denials": sum(x.get("authority_denials", 0) for x in s) if is_v2 else "NOT_APPLICABLE",
            "gate_denial_units": sum(1 for x in s if x.get("authority_denials", 0) > 0) if is_v2 else "NOT_APPLICABLE",
            "gate_denial_reasons_raw": dict(reasons) if is_v2 else "NOT_APPLICABLE",
            "first_classification_distribution": dict(Counter(str(x.get("first_classification")) for x in s)),
            "classification_error": "NOT_REPORTED_THIS_ROUND (A1: expectation set never created; Q3 answered only in part)",
            "secondary_scorer_disagreement_units": sum(1 for x, y in zip(s, sec) if secondary_differences(x, y)),
        }
        if u0["classifier_arm"] == "FULL" and all(r["unit"]["group"] == "F7" for r in rows):
            m["F7_FULL_expectation_met_units"] = sum(1 for x in s if x.get("first_classification") == F7_FULL_EXPECTATION)
            m["F7_FULL_expectation_note"] = f"{F7_FULL_EXPECTATION}: {F7_EXPECTATION_PROVENANCE}; not a truth label"
        return m

    arm_metrics = {f"{a}|{g}": metrics(rows) for (a, g), rows in sorted(groups.items())}

    x1_rows, v2_events, disagreements, appendix_v1 = [], [], [], []
    for uid, row in sorted(unit_rows.items()):
        u = row["unit"]
        s = row["used"][1]["primary_score"] if row["used"] else {}
        if row["used"]:
            sec = row["used"][1]["secondary_score"]
            diff = secondary_differences(s, sec)
            if diff:
                disagreements.append({"unit_id": uid, "arm": arm_of(u), "group": u["group"],
                                      "differences_primary_vs_secondary": json.dumps(diff, sort_keys=True)})
            if u["executor"] == "v1":
                appendix_v1.append({"unit_id": uid, "arm": arm_of(u), "group": u["group"],
                                    "secondary_score_unchanged": json.dumps(sec, sort_keys=True)})
        if u.get("x1"):
            x1_rows.append({"unit_id": uid, "arm": arm_of(u), "scenario": u["scenario"]["scenario_id"], "profile": u["scenario"]["profile"],
                            "status": row["status"], "first_attempt_state": row["attempts"][0][2] if row["attempts"] else None,
                            "effect_count": s.get("effect_count"), "S1": s.get("duplicate_effect"), "S2": s.get("unauthorized_effect"),
                            "S3": s.get("false_completion"), "S4_sends": s.get("s4_invalid_sends"), "final_state": s.get("final_state"),
                            "safe_opportunity": u["truth"]["safe_opportunity"], "opportunity_completed": s.get("opportunity_completed"),
                            "first_classification": s.get("first_classification")})
        if u["executor"] == "v2" and row["used"]:
            v2_events.extend(v2_event_labels(u, s))
    exposure = defaultdict(Counter)
    for row in unit_rows.values():
        u = row["unit"]
        key = f"{arm_of(u)}|{u['group']}|{u['exposure'].split(':')[0]}"
        exposure[key]["units"] += 1
        if row["used"]:
            exposure[key]["valid_first_attempt"] += 1
            exposure[key]["any_safety_event"] += int(has_event(row["used"][1]["primary_score"]))
    failed_obs = []
    for uid, row in sorted(unit_rows.items()):
        for e, r, state, reason in row["attempts"]:
            if state in ("INFRA_FAILED", "INTEGRITY_UNKNOWN") and r is not None:
                obs = r.get("failed_attempt_observation") or {}
                ps = obs.get("primary_score") if isinstance(obs, dict) else None
                failed_obs.append({"attempt_id": e["attempt_id"], "attempt_number": e["attempt_number"], "state": state, "reason": reason,
                                   "observation": None if ps is None else {k: ps.get(k) for k in (
                                       "effect_count", "duplicate_effect", "unauthorized_effect", "false_completion", "s4_invalid_sends")}})
    coverage = Counter(r["status"] for r in unit_rows.values())
    not_started = sorted(uid for uid, r in unit_rows.items() if r["status"] == "NOT_STARTED")
    summary = {
        "status": "OK", "report_completeness": "PARTIAL" if not_started else "COMPLETE",
        "tool": TOOL_VERSION, "tool_sha256": sha_file(__file__),
        "cells_sha256": cells_sha, "execution_manifest_expected": args.expect_execution_manifest,
        "inputs": {"index_files": len(index_files), "result_files": len(result_files), "finalized_files": [str(f) for f in args.finalized]},
        "duplicates_counted_once": duplicates,
        "rule_R0": "main report uses each unit's first attempt only; approved retries are in retries.csv and never replace it",
        "coverage_first_attempt": dict(coverage),
        "no_valid_by_first_attempt_state": dict(Counter(r["sub_status"] for r in unit_rows.values() if r["status"] == "NO_VALID_RESULT")),
        "not_started_units": not_started,
        "attempts_per_unit": dict(Counter(len(r["attempts"]) for r in unit_rows.values())),
        "retries_reported_separately": len(retry_rows),
        "arm_metrics": arm_metrics,
        "X1": {"units": len(x1_rows), "rows": x1_rows,
               "note": "X1 = F7 x {FULL/v1, CONV, FULL/v2-R2A, FULL/v2-R2B}; 48 units contained in P3A; first attempts only"},
        "v2_events": v2_events,
        "v2_events_note": "A10: all S1-S4 events of all v2 arms; plan_expectation VIOLATED for each; mechanism_guarantee per event; nothing excluded",
        "v2_event_counts": dict(Counter(f"{e['event']}|{e['mechanism_guarantee']}" for e in v2_events)),
        "exposure_breakdown": {k: dict(v) for k, v in sorted(exposure.items())},
        "failed_attempt_safety_observations": failed_obs,
        "secondary_scorer_disagreements": disagreements,
        "decided_rules": {"A2": "gate denials counted by raw reason strings; no grouping added",
                          "A5": "no confidence intervals or statistical tests; conclusions limited to the fixed scenario set, "
                                "no extrapolation to a population failure rate",
                          "A8": "secondary scorer is a consistency check only: disagreeing units listed with differences; "
                                "v1-arm secondary scores attached unchanged (appendix_secondary_v1.csv); not used for main "
                                "conclusions; never replaces the primary scorer"},
        "pending_decisions": {},
        "F7_expectation_provenance": F7_EXPECTATION_PROVENANCE,
        "reporting_notes": ["R2-A and R2-B are reported separately; no significance test between them (plan v2 section 1)",
                            "A1: classification error rate not reported; research question Q3 can only be answered in part",
                            "bounds are assumption bounds over unknown units, not observed events",
                            "A5: results describe the fixed scenario set only; no population failure rate is estimated"],
    }
    tables = {"units": [{"unit_id": uid, "arm": arm_of(r["unit"]), "group": r["unit"]["group"], "status": r["status"],
                         "first_attempt_state": r["attempts"][0][2] if r["attempts"] else None,
                         "used_attempt": r["used"][0]["attempt_id"] if r["used"] else None}
                        for uid, r in sorted(unit_rows.items())],
              "attempts": attempt_rows, "retries": retry_rows, "x1": x1_rows, "v2_events": v2_events,
              "secondary_disagreements": disagreements, "appendix_secondary_v1": appendix_v1}
    return summary, tables


def write_csv(path: Path, rows: list, banner):
    with open(path, "x", newline="", encoding="utf-8") as f:
        if banner:
            f.write(f"# {banner}\n")
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", required=True)
    ap.add_argument("--expect-cells-sha256", required=True)
    ap.add_argument("--expect-execution-manifest", required=True)
    ap.add_argument("--index", action="append", required=True)
    ap.add_argument("--batch", action="append", required=True)
    ap.add_argument("--finalized", action="append", default=[])
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir()  # must be new
    synthetic = any((Path(d) / "SYNTHETIC_TEST_DATA").exists() for d in args.index + args.batch)
    banner = SYNTHETIC if synthetic else None
    try:
        summary, tables = analyse(args)
    except Conflict as error:
        summary, tables = {"status": "INPUT_CONFLICT", "conflicts": [str(error)]}, {}
    summary["data_provenance"] = SYNTHETIC if synthetic else "as given (not checked for formality by this tool)"
    with open(out / "summary.json", "x", encoding="utf-8") as f:
        f.write(json.dumps(summary, indent=1, ensure_ascii=False) + "\n")
    for name, rows in tables.items():
        write_csv(out / f"{name}.csv", rows, banner)
    print(json.dumps({"status": summary["status"], "completeness": summary.get("report_completeness"),
                      "provenance": summary["data_provenance"], "coverage": summary.get("coverage_first_attempt"),
                      "conflicts": len(summary.get("conflicts", []))}))
    sys.exit(0 if summary["status"] == "OK" else 2)


if __name__ == "__main__":
    main()
