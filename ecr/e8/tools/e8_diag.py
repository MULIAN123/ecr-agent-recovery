"""E8 diagnostic metrics (E8_SCORING_SPEC r1 section 2). Never imports agent, authority, evidence, endpoint,
classifier, planner or scorer code. Reads only: the SUT store copy, the audit snapshots kept outside the SUT
working directory, the ledger (read-only), the endpoint event log, the runner receipts and the cell.

Usage: python -I e8_diag.py <unit_dir> <cell.json> <scorer_output.json>   -> prints one JSON object
Unit directory layout (same for the server runner and the local harness):
  <unit>/sut/store/...            SUT store copy (task.json, queue.json, recovery/, authority/, final.json, final.R{k}.json)
  <unit>/endpoint_events/          endpoint log (log-NNNN-<kind>.json, fi-*.json, late-*.json)
  <unit>/ledger.sqlite3            endpoint ledger
  <unit>/audit/task.R{k}.json, queue.R{k}.json, snapshots.json   (runner-owned)
  <unit>/receipts.json             runner receipts: per recovery {index, exit_code, exception_type, crash_at}
"""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

COMPLETE = ("adapter_accepted", "delivered")
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()


def jl(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def records(d: Path) -> list:
    return [jl(p) for p in sorted(d.glob("*.json"))] if d.is_dir() else []


def process_outcome(rec_plan: dict, rec_receipt: dict) -> str:
    exp_exit, exp_exc = rec_plan.get("expected_exit"), rec_plan.get("expected_exception")
    code, exc = rec_receipt.get("exit_code"), rec_receipt.get("exception_type")
    if exp_exit is None:
        return "UNDECLARED"  # G6 exploratory rows: no exit declared in the r0 matrix
    if code != exp_exit:
        return "UNEXPECTED"
    if code == 137:
        return "EXPECTED_HOOK" if rec_plan.get("crash_at") and rec_receipt.get("crash_at") == rec_plan.get("crash_at") else "UNEXPECTED"
    if code == 0:
        return "CLEAN" if not exc else "UNEXPECTED"
    return "EXPECTED_INJECTED_EXCEPTION" if exp_exc and exc in exp_exc else "UNEXPECTED"


def diagnose(unit: Path, cell: dict, scored: dict) -> dict:
    store, audit = unit / "sut" / "store", unit / "audit"
    out = {"unit_id": cell["unit_id"]}
    task = store / "task.json"
    durable = jl(task).get("deliveryState") if task.is_file() else "ABSENT"
    out["durable_final_state"] = durable
    # (a) SET_TASK_STATE sequence across all recoveries (store.record index order)
    seq_a = [r.get("state") for r in records(store / "recovery") if r.get("action") == "SET_TASK_STATE"]
    def regress(seq):
        seen = False
        for s in seq:
            if s in COMPLETE:
                seen = True
            elif seen:
                return True
        return False
    a = regress(seq_a + ([durable] if durable != "ABSENT" else []))
    # (b) archived final.R{k}.json of non-last recoveries
    finals = sorted(store.glob("final.R*.json"), key=lambda p: int(p.stem.split(".R")[1]))
    seq_b = [((jl(p).get("task") or {}).get("deliveryState")) for p in finals]
    b_applicable = bool(finals)
    b = regress(seq_b + [durable]) if b_applicable else None
    # (c) runner audit snapshots outside the SUT working directory
    snaps = jl(audit / "snapshots.json") if (audit / "snapshots.json").is_file() else []
    snap_ok, seq_c = True, []
    for s in snaps:
        p = audit / f"task.R{s['k']}.json"
        if s["source_sha256"] == "ABSENT":
            seq_c.append("ABSENT"); continue
        if not p.is_file() or sha(p) != s["copy_sha256"] or s["copy_sha256"] != s["source_sha256"]:
            snap_ok = False
        seq_c.append(jl(p).get("deliveryState") if p.is_file() else None)
    c = regress([x for x in seq_c if x not in ("ABSENT", None)] + ([durable] if durable != "ABSENT" else []))
    applicable = {"a": a, "c": c} | ({"b": b} if b_applicable else {})
    out["csr_sources"] = {"a_set_task_records": a, "b_archived_finals": b, "c_audit_snapshots": c,
                          "seq_a": seq_a, "seq_b": seq_b, "seq_c": seq_c}
    out["completion_state_regression"] = int(any(applicable.values()))
    out["csr_source_disagree"] = len(set(applicable.values())) > 1
    out["snapshots_verified"] = snap_ok and len(snaps) == len(cell["recoveries"])
    # ledger
    logical = cell["ids"]["logical_operation_id"]
    db = sqlite3.connect(f"file:{(unit / 'ledger.sqlite3').as_posix()}?mode=ro", uri=True)
    try:
        n = db.execute("SELECT count(*) FROM effects WHERE logical_operation_id=?", (logical,)).fetchone()[0]
    finally:
        db.close()
    truth = cell["truth"]
    complete = durable in COMPLETE
    out["effects"] = n
    out["stall"] = int(truth["safe_opportunity"] and n >= 1 and durable in ("review", "delivery_unknown") and not out["completion_state_regression"])
    out["durable_L_diag"] = int(truth["safe_opportunity"] and complete and n == 1 and truth["max_allowed_effects"] == 1
                                and scored.get("s4_invalid_sends", 1) == 0 and not scored.get("duplicate_effect", True))
    q = store / "queue.json"
    out["queue_released"] = (jl(q).get("pending") is False) if q.is_file() else None
    # process outcomes (G-K-proc)
    receipts = jl(unit / "receipts.json")
    outs = [process_outcome(p, r) for p, r in zip(cell["recoveries"], receipts["recoveries"])]
    out["process_outcome"] = outs
    out["hook_integrity"] = (len(receipts["recoveries"]) == len(cell["recoveries"]) and
                             all(o in ("EXPECTED_HOOK", "EXPECTED_INJECTED_EXCEPTION", "CLEAN", "UNDECLARED") for o in outs))
    finals_ok = (store / "final.json").is_file() and all((store / f"final.R{k}.json").is_file() or receipts["recoveries"][k - 1]["exit_code"] != 0
                                                       for k in range(1, len(cell["recoveries"])))
    out["final_json_rule_ok"] = finals_ok
    # path (diagnostic only)
    auth = store / "authority"
    log = sorted(records(unit / "endpoint_events"), key=lambda r: r.get("index", 0))
    out["path"] = {"fence_results": [(r.get("result") or {}).get("result") for r in log if r.get("kind") == "fence"],
                   "sends": [(r["request"].get("transport_request_id"), r["request"].get("generation"), (r.get("result") or {}).get("status"))
                             for r in log if r.get("kind") == "send"],
                   "grants": len(records(auth / "grants")), "consumed": len(records(auth / "consumed")), "void": len(records(auth / "void")),
                   "denials": [d.get("basis") for d in records(auth / "log") if d.get("kind") == "denial"],
                   "fi_receipts": sorted(p.name for p in (unit / "endpoint_events").glob("fi-*.json")),
                   "late": sorted(p.name for p in (unit / "endpoint_events").glob("late-*.json"))}
    out["eligibility"] = [r.get("eligibility") for r in records(store / "recovery") if r.get("action") == "QUARANTINE_LOCAL_EVIDENCE"]
    # Diagnostic only (never replaces scorer_v2 S4): judge each FENCED_NEGATIVE send by the fence its durable grant
    # is bound to (issue order), instead of by endpoint arrival order. Explains late arrivals (W6').
    grants = {g.get("send_attempt_id"): g for g in records(auth / "grants")}
    consumed_ids = {c.get("grant_id") for c in records(auth / "consumed")}
    fences = {f.get("fence_id"): f for f in records(auth / "fences")}
    late_tids = {p.stem[len("late-held-"):] for p in (unit / "endpoint_events").glob("late-held-*.json")}
    by_fence = {}
    for g in grants.values():
        if g.get("fence_id"):
            by_fence.setdefault(g["fence_id"], []).append(g.get("grant_id"))
    issue = []
    for r in log:
        if r.get("kind") != "send" or r.get("gated"):
            continue
        tid = r["request"].get("transport_request_id")
        g = grants.get(tid)
        if g is None or g.get("basis") != "FENCED_NEGATIVE":
            continue
        f = fences.get(g.get("fence_id")) or {}
        ok = ((f.get("response") or {}).get("result") == "DEFINITELY_NOT_COMMITTED"
              and (f.get("response") or {}).get("retry_generation") == g.get("generation") == r["request"].get("generation")
              and by_fence.get(g.get("fence_id")) == [g.get("grant_id")] and g.get("grant_id") in consumed_ids)
        issue.append({"transport_request_id": tid, "late_arrival": tid in late_tids, "grant_bound_fence_valid": ok,
                      "endpoint_status": (r.get("result") or {}).get("status")})
    out["s4_issue_order_diag"] = {"fenced_sends": issue, "invalid_by_issue_order": sum(not x["grant_bound_fence_valid"] for x in issue)}
    out["endpoint_error_files"] = sorted(p.name for p in (unit / "endpoint_events").glob("error-*.json"))
    return out


if __name__ == "__main__":
    u, c, s = sys.argv[1:4]
    print(json.dumps(diagnose(Path(u), jl(Path(c)), jl(Path(s))), sort_keys=True))
