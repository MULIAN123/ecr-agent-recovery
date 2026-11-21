"""Diagnostic scorer for the authorization-crash experiment (async_sim_ac1, plan r2).

Independent of the agent, authority, evidence, planner and classifier code: it never imports them.
It reads the cell ledger, the endpoint log, the agent store copies (final and pre-recovery), the
runner's kill/snapshot records and the PRIMARY scorer's output, and reports three separate
judgements per unit, which are never merged into one verdict:

  A. execution validity   VALID / INVALID (kill receipts, snapshot timing)
  B. safety, per metric   S1, S2, S3, S4 each SATISFIED / VIOLATED / UNKNOWN
  C. recovery (L)         COMPLETED / NOT_COMPLETED / NO_OPPORTUNITY / UNKNOWN
plus parallel labels: abnormal_exit, evidence_incomplete, and the comparison with the unit's
registered prediction. Observing a predicted defect is reported as "prediction matched", never as
a pass of the mechanism.

Differences from scorer_v2 (which stays the unchanged primary scorer):
  * effects are counted per DELIVERABLE: every row of the cell's own ledger, whatever its
    logical-operation key (scorer_v2 counts the scenario key only and reports other keys as a
    FOREIGN_EFFECT_IN_LEDGER fault);
  * the task state is read from store/task.json (atomically replaced), not from final.json, so a
    recovery that exits abnormally does not by itself make S3 or L unknown;
  * UNKNOWN rules of plan r2, section 3;
  * the conditional F3/P0 opportunity rule of plan r2, section 5.1 (C1-C5 on pre-recovery-1).
Usage: python scorer_diag.py <context.json>
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import acreceipts

COMPLETE = ("adapter_accepted", "delivered")
SAT, VIO, UNK = "SATISFIED", "VIOLATED", "UNKNOWN"


class Unreadable(Exception):
    pass


def load(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise Unreadable(f"{path}: {type(error).__name__}") from error


def load_dir(folder: Path) -> list:
    if not folder.is_dir():
        return []
    return [load(p) for p in sorted(folder.glob("*.json"))]


# ------------------------------------------------------------------ A. execution validity
def execution_validity(ctx: dict, folder: Path) -> list[str]:
    problems = []
    unit = ctx["unit"]
    kills = ctx.get("kills", [])
    uid = ctx.get("agent_uid")
    if unit["scenario"].get("no_fault"):
        try:
            receipt = load(folder / "injection-receipt.json")
            problems += [f"control receipt: {p}" for p in acreceipts.validate_none_receipt(receipt)]  # F1
        except Unreadable as error:
            problems.append(f"control receipt unreadable: {error}")
    expected = []
    if unit["scenario"].get("send_pause"):
        expected.append(("agent-send", unit["scenario"]["send_pause"], "injection-receipt"))
    elif unit["scenario"].get("barrier"):
        n = {"B2_DROP": 2, "B2_LATE": 2, "B2_ARRIVE": 2, "B3_LATE": 3, "B4": 4, "B5": 5, "B6": 6}[unit["scenario"]["barrier"]]
        expected.append(("agent-send", f"B{n}", "injection-receipt"))
    for step in unit["scenario"].get("recoveries", []):
        if step.get("pause"):
            expected.append((step["label"], step["pause"], f"injection-receipt-{step['label']}"))
    if [(k["label"], k["point"]) for k in kills] != [(lab, pt) for lab, pt, _ in expected]:
        problems.append(f"kills {kills} differ from the planned kills {[(lab, pt) for lab, pt, _ in expected]}")
    receipts = {}
    adapter_identity = None
    if any(pt in acreceipts.ADAPTER_BARRIERS for _, pt, _ in expected):
        # F3: the endpoint of THIS cell, from its own ready record, and the adapter uid given by the runner
        try:
            ready = load(Path(ctx["endpoint_events"]) / "ready.json")
            adapter_identity = {"pid": ready.get("pid"), "euid": ctx.get("adapter_uid")}
            if ready.get("uid") != ctx.get("adapter_uid"):
                problems.append("endpoint ready record uid differs from the adapter identity")
        except Unreadable as error:
            problems.append(f"endpoint ready record unreadable: {error}")
    for label, point, name in expected:
        try:
            receipt = load(folder / f"{name}.json")
            basis_file = folder / (f"{label}-pause-observed.json" if not point.startswith("B") else f"{point}-observed.json")
            basis = load(basis_file)
            launch = load(folder / f"{label}-start.json")  # F3: launch record written by the runner
        except Unreadable as error:
            problems.append(f"{label}: {error}")
            continue
        receipts[label] = receipt
        if receipt.get("run_id") != ctx.get("run_id"):
            problems.append(f"{label}: kill receipt run_id differs from this cell")
        for reason in acreceipts.validate_kill_receipt(receipt, basis, expected_point=point, expected_uid=uid,
                                                       adapter_identity=adapter_identity, launch_record=launch):
            problems.append(f"{label}: {reason}")
    previous = receipts.get("agent-send")
    for index, step in enumerate(unit["scenario"].get("recoveries", []), 1):
        try:
            snap = load(folder / f"pre-recovery-{index}-record.json")
            start = load(folder / f"{step['label']}-start.json")
        except Unreadable as error:
            problems.append(f"snapshot {index}: {error}")
            continue
        for reason in acreceipts.validate_snapshot_timing(previous, snap, start,
                                                          live_agent_processes=snap.get("live_agent_processes", [])):
            problems.append(f"snapshot {index}: {reason}")
        previous = receipts.get(step["label"])
    return problems


# ------------------------------------------------------------------ B. safety
def ledger_effects(path: str) -> list[tuple]:
    db = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    try:
        return db.execute("SELECT seq,logical_operation_id,attempt_id,transport_request_id FROM effects ORDER BY seq").fetchall()
    finally:
        db.close()


def judge_s1_s2(ctx: dict) -> dict:
    try:
        effects = ledger_effects(ctx["ledger"])
    except (sqlite3.Error, OSError) as error:
        return {"S1": UNK, "S2": UNK, "n_effects_deliverable": None, "n_effects_scenario_key": None,
                "keys": None, "reason": f"ledger unreadable: {type(error).__name__}"}
    n_all = len(effects)
    n_key = sum(e[1] == ctx["logical_operation_id"] for e in effects)
    allowed = ctx["truth"].get("max_allowed_effects")
    s2 = UNK if allowed is None else (VIO if n_all > allowed else SAT)
    return {"S1": VIO if n_all > 1 else SAT, "S2": s2, "n_effects_deliverable": n_all, "n_effects_scenario_key": n_key,
            "keys": sorted({e[1] for e in effects}), "reason": None}


def task_state(store: Path) -> tuple[str | None, str | None]:
    path = store / "task.json"
    if not path.is_file():
        return None, "task.json missing"
    try:
        return load(path).get("deliveryState"), None
    except Unreadable as error:
        return None, str(error)


def judge_s3(n_all, state, state_problem) -> str:
    if state_problem or n_all is None:
        return UNK
    return VIO if (state in COMPLETE and n_all == 0) else SAT


def judge_s4(ctx: dict, store: Path) -> dict:
    try:
        log = sorted(load_dir(Path(ctx["endpoint_events"])), key=lambda r: r.get("index", 0))
    except Unreadable as error:
        return {"S4": UNK, "reason": f"endpoint log unreadable: {error}", "sends": []}
    sends = [r for r in log if r.get("kind") == "send" and not r.get("gated")]
    if not sends:
        return {"S4": SAT, "reason": "no recovery-time send", "sends": []}
    try:
        grants = load_dir(store / "authority" / "grants")
        actions = load_dir(store / "recovery")
    except Unreadable as error:
        return {"S4": UNK, "reason": f"basis records unreadable while sends exist: {error}", "sends": []}
    grant_ids = {g.get("send_attempt_id") for g in grants}
    labels = {a["request"]["transport_request_id"] for a in actions
              if a.get("action") == "AUTHORIZE_SEND" and isinstance(a.get("request"), dict)}
    try:
        primary = load(Path(ctx["primary_score"]))
        judged = {j["transport_request_id"]: j for j in primary.get("sends_judged", [])}
    except Unreadable:
        judged = None
    rows, verdict = [], SAT
    for s in sends:
        tid = (s.get("request") or {}).get("transport_request_id")
        if tid not in grant_ids and tid not in labels:
            rows.append({"transport_request_id": tid, "verdict": VIO, "why": "send has no durable claimed basis"})
            verdict = VIO
            continue
        if judged is None or tid not in judged:
            rows.append({"transport_request_id": tid, "verdict": UNK, "why": "primary judgement unavailable"})
            if verdict != VIO:
                verdict = UNK
            continue
        ok = judged[tid].get("contract_valid") is True
        rows.append({"transport_request_id": tid, "verdict": SAT if ok else VIO, "why": "primary scorer_v2 claimed-basis verdict"})
        if not ok:
            verdict = VIO
    return {"S4": verdict, "reason": None, "sends": rows}


# ------------------------------------------------------------------ C. opportunity and L
def f3p0_opportunity(ctx: dict, folder: Path) -> dict:
    """Plan r2, section 5.1. Returns {"opportunity": "YES"|"UNKNOWN"|"INVALID", "conditions": {...}}."""
    snap = folder / "pre-recovery-1"
    cond = {"C1_code_identity_authority_sha256": ctx.get("authority_sha256"), "C2_A3": "ASSUMED (not checkable in a cell)"}
    unit = ctx["unit"]
    if not snap.is_dir():
        return {"opportunity": "UNKNOWN", "conditions": {**cond, "unreadable": "pre-recovery-1 snapshot missing"}}
    try:
        consumed = load_dir(snap / "authority" / "consumed")
        grants = load_dir(snap / "authority" / "grants")
        void = load_dir(snap / "authority" / "void")
        fences = load_dir(snap / "authority" / "fences")
        log = load_dir(snap / "authority" / "log")
        events = load_dir(snap / "journal")
        queue = load(snap / "queue.json") if (snap / "queue.json").is_file() else None
        recovery = list((snap / "recovery").glob("*.json")) if (snap / "recovery").is_dir() else []
        evidence_files = list(snap.glob("endpoint-evidence*.json"))
    except Unreadable as error:
        return {"opportunity": "UNKNOWN", "conditions": {**cond, "unreadable": str(error)}}
    failed = []
    if len(consumed) != 1:
        failed.append(f"C3 exactly one consumed marker (found {len(consumed)})")
    else:
        c = consumed[0]
        g = [x for x in grants if x.get("grant_id") == c.get("grant_id")]
        if len(g) != 1:
            failed.append("C3 consumed.grant_id resolves to exactly one grant")
        else:
            g = g[0]
            key = unit["ids"]["logical_operation_id"]
            checks = {
                "C3 grant.first_dispatch": g.get("first_dispatch") is True,
                "C3 grant.basis == PRE_DISPATCH": g.get("basis") == "PRE_DISPATCH",
                "C3 grant not void": all(v.get("grant_id") != g.get("grant_id") for v in void),
                "C3 consumed key == grant key == K": c.get("logical_operation_id") == g.get("logical_operation_id") == key,
                "C3 send_attempt_id equal": c.get("send_attempt_id") == g.get("send_attempt_id"),
                "C3 generation equal and 0": c.get("generation") == g.get("generation") == 0,
                "C3 grant.attempt_id == journal attemptId": g.get("attempt_id") == unit["ids"]["identity"]["attemptId"],
            }
            failed += [k for k, ok in checks.items() if not ok]
    types = [e.get("eventType") for e in events]
    if types != ["AttemptCommitted", "QueueBindingPrepared", "QueueItemPersisted"]:
        failed.append(f"C4 journal is exactly events 1-3 without DBC or outcome (found {types})")
    if not (isinstance(queue, dict) and queue.get("pending") is True):
        failed.append("C4 queue pending")
    if len(grants) != 1 or void or fences or any(x.get("kind") == "result" for x in log) or recovery or evidence_files:
        failed.append("C5 no other grant, void, fence, result log, recovery record or endpoint evidence")
    cond["failed"] = failed
    if failed:
        return {"opportunity": "INVALID", "conditions": cond}
    try:
        endpoint = load_dir(Path(ctx["endpoint_events"]))
    except Unreadable as error:
        return {"opportunity": "UNKNOWN", "conditions": {**cond, "consistency": f"endpoint log unreadable: {error}"}}
    original = unit["ids"]["transport_request_id"]
    if any(r.get("kind") == "send" and (r.get("request") or {}).get("transport_request_id") == original for r in endpoint):
        return {"opportunity": "UNKNOWN", "conditions": {**cond, "consistency":
                "FINDING: endpoint log shows the original send although C3-C5 hold (C1 or C2 violated)"}}
    return {"opportunity": "YES", "conditions": cond}


def judge_l(opportunity, safety: dict, n_all, state, state_problem, allowed) -> str:
    if opportunity in ("UNKNOWN", "INVALID") or state_problem or any(safety[k] == UNK for k in ("S1", "S2", "S3", "S4")):
        return UNK
    if opportunity in (False, "NO"):
        return "NO_OPPORTUNITY"
    done = (state in COMPLETE and n_all == 1 and allowed == 1 and all(safety[k] == SAT for k in ("S1", "S2", "S3", "S4")))
    return "COMPLETED" if done else "NOT_COMPLETED"


def score(ctx: dict) -> dict:
    folder = Path(ctx["attempt_folder"])
    store = Path(ctx["sut_copy"]) / "store"
    unit = ctx["unit"]
    execution = execution_validity(ctx, folder)
    s12 = judge_s1_s2(ctx)
    state, state_problem = task_state(store)
    s3 = judge_s3(s12["n_effects_deliverable"], state, state_problem)
    s4 = judge_s4(ctx, store)
    safety = {"S1": s12["S1"], "S2": s12["S2"], "S3": s3, "S4": s4["S4"]}
    rule = unit.get("opportunity_rule", "TRUTH")
    f3 = None
    if rule == "F3P0_CONDITIONAL":
        f3 = f3p0_opportunity(ctx, folder)
        opportunity = f3["opportunity"]
        if opportunity == "INVALID":
            # readable but contradicting the constructed F3 state: the injection did not produce it
            execution.append(f"F3/P0 state not as constructed: {f3['conditions'].get('failed')}")
    else:
        opportunity = bool(ctx["truth"].get("safe_opportunity"))
    recovery = judge_l(opportunity if opportunity != "YES" else True, safety, s12["n_effects_deliverable"], state,
                       state_problem, ctx["truth"].get("max_allowed_effects"))
    planned = {k["label"] for k in ctx.get("kills", [])}
    abnormal = {k: v for k, v in (ctx.get("recovery_exits") or {}).items() if k not in planned and v not in (0, None)}
    unknown = [k for k, v in safety.items() if v == UNK] + (["L"] if recovery == UNK else [])
    pred = unit.get("prediction") or {}
    comparison = {"safety": {k: (pred.get(k) == v) if k in pred else None for k, v in safety.items()},
                  "L": (pred.get("L") == recovery) if "L" in pred else None,
                  "note": "a matched prediction of a defect is not a pass of the mechanism"}
    return {"run_id": ctx["run_id"], "unit_id": unit["unit_id"], "scorer": "scorer_diag_ac1",
            "A_execution": "VALID" if not execution else "INVALID", "A_problems": execution,
            "B_safety": safety, "B_details": {"effects": s12, "task_state": state, "task_state_problem": state_problem,
                                              "s4": s4},
            "C_recovery": recovery, "opportunity": opportunity, "f3p0": f3,
            "labels": {"abnormal_exit": abnormal, "evidence_incomplete": unknown},
            "prediction": pred, "prediction_comparison": comparison,
            "scorer_uid": getattr(os, "geteuid", lambda: -1)(), "scorer_pid": os.getpid()}


if __name__ == "__main__":
    print(json.dumps(score(json.loads(Path(sys.argv[1]).read_text())), sort_keys=True))
