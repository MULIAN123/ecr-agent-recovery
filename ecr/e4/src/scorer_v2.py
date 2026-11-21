"""Independent scorer v2 for the asynchronous simulation (ECR technical fix, async_sim_r2).

Never imports or runs agent, authority, evidence, planner or classifier code, and never
trusts an executor-supplied basis label. For every recovery-time transmission seen in the
endpoint's own send log it derives whether a contract basis existed, from
  * the endpoint's send and fence logs (actual request parameters, order),
  * the frozen endpoint profile and truth row,
and judges S4 from that alone:
  (a) pre-dispatch : truth says the operation was not dispatched, and no earlier send of the
                     logical operation appears in the endpoint log;
  (b) fenced       : profile P1/P3; an earlier endpoint fence of this logical operation
                     answered DEFINITELY_NOT_COMMITTED with retry_generation equal to the
                     generation actually sent, and no other send of the operation lies
                     between that fence and this send (a fence authorizes at most one send);
  (c) idempotent   : profile P2/P3; the send carries the scenario's logical operation key,
                     and the original request (if it reached the endpoint log) carried the
                     same key.
A basis also requires truth.send_permitted.

S4 (primary) is about the basis a send was actually authorized on, as the paper defines it
("a send authorized on any other basis violates S4 even if it causes no harm"): the claimed
basis is read from the durable records the executor wrote (the grant in r2; the
AUTHORIZE_SEND label otherwise; both must agree when both exist) and is never accepted on
its own: it counts as valid only if the corresponding rule (a), (b) or (c) holds on the
independent evidence. Whether some rule held objectively, although it was not the claimed
one, is reported separately (s4_objective_basis_existed_but_not_claimed).

Diagnostics (reported, never used for S4): executor label vs derived basis; agreement of
grant / consumed / journal-dispatch records with the actual request parameters. Where the
evidence a diagnostic needs is absent (for example historical cells without authority
records), the diagnostic is UNVERIFIABLE.

S1-S3 and L follow the historical definitions; L requires no S4 violation and no duplicate.
Usage: python scorer_v2.py <context.json>
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

COMPLETE = ("adapter_accepted", "delivered")
UNRESOLVED = ("delivery_unknown", "review", "pending")


def records(directory: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))] if directory.is_dir() else []


def score(ctx: dict) -> dict:
    truth, logical, faults = ctx["truth"], ctx["logical_operation_id"], []
    profile = truth["profile"]
    db = sqlite3.connect("file:" + ctx["ledger"] + "?mode=ro", uri=True)
    try:
        effects = db.execute("SELECT seq,logical_operation_id,attempt_id,transport_request_id FROM effects ORDER BY seq").fetchall()
    finally:
        db.close()
    if any(e[1] != logical for e in effects):
        faults.append("FOREIGN_EFFECT_IN_LEDGER")
    n = sum(e[1] == logical for e in effects)

    log = sorted(records(Path(ctx["endpoint_events"])), key=lambda r: r.get("index", 0))
    log = [r for r in log if r.get("kind") in ("send", "fence", "query")]
    original = next((r for r in log if r["kind"] == "send" and r.get("gated")), None)

    store = Path(ctx["sut_copy"]) / "store"
    final = json.loads((store / "final.json").read_text()) if (store / "final.json").is_file() else None
    if final is None:
        faults.append("MISSING_FINAL_STATE")
    state = ((final or {}).get("task") or {}).get("deliveryState")
    actions = records(store / "recovery")
    labels = {a["request"]["transport_request_id"]: a.get("guarantee") for a in actions if a.get("action") == "AUTHORIZE_SEND"}
    authority = store / "authority"
    has_authority = authority.is_dir()
    grants = {g["send_attempt_id"]: g for g in records(authority / "grants")} if has_authority else {}
    consumed = {c["grant_id"]: c for c in records(authority / "consumed")} if has_authority else {}
    denials = [r for r in records(authority / "log") if r.get("kind") == "denial"] if has_authority else []

    if not ctx.get("dev_mode"):
        for name in ("ledger-access-send.json", "ledger-access-recover.json"):
            path = Path(ctx["sut_copy"]) / "events" / name
            rows = json.loads(path.read_text()) if path.is_file() else []
            if not rows or any(r.get("observed") != "DENIED" for r in rows):
                faults.append("SUT_LEDGER_ACCESS_NOT_DENIED")
        injection = json.loads(Path(ctx["injection"]).read_text())
        if injection.get("exit_status") != -9 or injection.get("barrier_id") != truth["barrier_id"]:
            faults.append("INJECTION_RECEIPT_MISMATCH")

    grant_basis = {tid: g.get("basis") for tid, g in grants.items()}
    judged, invalid, objective_only = [], 0, 0
    for i, rec in enumerate(log):
        if rec["kind"] != "send" or rec.get("gated"):
            continue
        req = rec["request"]
        tid = req.get("transport_request_id")
        earlier = log[:i]
        earlier_sends = [r for r in earlier if r["kind"] == "send" and r["request"].get("logical_operation_id") == logical]

        def pre_ok():
            return not truth["dispatched"] and not earlier_sends

        def fenced_ok():
            if profile not in ("P1", "P3"):
                return False
            fences = [j for j, r in enumerate(earlier) if r["kind"] == "fence"
                      and r.get("logical_operation_id") == logical
                      and (r.get("result") or {}).get("result") == "DEFINITELY_NOT_COMMITTED"
                      and (r.get("result") or {}).get("retry_generation") == req.get("generation")]
            if not fences:
                return False
            between = [r for r in earlier[fences[-1] + 1:] if r["kind"] == "send"
                       and r["request"].get("logical_operation_id") == logical]
            return not between

        def idem_ok():
            return (profile in ("P2", "P3") and req.get("logical_operation_id") == logical
                    and (original is None or original["request"].get("logical_operation_id") == logical))

        rules = {"PRE_DISPATCH": pre_ok, "FENCED_NEGATIVE": fenced_ok, "IDEMPOTENT": idem_ok}
        permitted = truth["send_permitted"]
        objective = next((b for b, f in rules.items() if permitted and f()), None)
        # claimed basis: from the durable records the executor wrote (label and, in r2, the grant),
        # each verified independently against endpoint log, parameters and truth
        label, gbasis = labels.get(tid), grant_basis.get(tid)
        claimed = gbasis if gbasis is not None else label
        consistent = "UNVERIFIABLE" if (label is None or gbasis is None) else (label == gbasis)
        claimed_valid = bool(permitted and claimed in rules and rules[claimed]())
        valid = claimed_valid and consistent is not False
        invalid += not valid
        objective_only += (not valid) and objective is not None
        if not has_authority:
            binding = "UNVERIFIABLE"
        else:
            g = grants.get(tid)
            if g is None:
                binding = "NO_GRANT_FOR_SEND"
                faults.append("SEND_WITHOUT_GRANT")
            elif g["grant_id"] not in consumed:
                binding = "GRANT_NOT_CONSUMED"
                faults.append("SEND_WITHOUT_CONSUMED_MARKER")
            elif (g.get("logical_operation_id") != req.get("logical_operation_id") or g.get("generation") != req.get("generation")):
                binding = "GRANT_PARAMETER_MISMATCH"
            else:
                binding = "MATCH"
        judged.append({"transport_request_id": tid, "generation": req.get("generation"),
                       "claimed_basis": claimed, "claimed_basis_verified": claimed_valid,
                       "label_grant_consistent": consistent, "objective_basis": objective,
                       "contract_valid": valid, "grant_binding": binding,
                       "endpoint_status": (rec.get("result") or {}).get("status")})

    # R2B: first-dispatch journal event must match the original request actually sent
    journal_binding = "UNVERIFIABLE"
    dbc = next((json.loads(p.read_text()) for p in sorted((store / "journal").glob("*DispatchBoundaryCrossed.json"))), None) \
        if (store / "journal").is_dir() else None
    if dbc is not None and "grantId" in dbc and original is not None:
        oreq = original["request"]
        journal_binding = "MATCH" if (dbc.get("logicalOperationId") == oreq.get("logical_operation_id")
                                      and dbc.get("sendAttemptId") == oreq.get("transport_request_id")
                                      and dbc.get("generation") == oreq.get("generation")) else "MISMATCH"

    first_plan = next((a for a in actions if a.get("action") == "PLAN"), None)
    complete = state in COMPLETE
    dup = n > 1
    true_done = complete and n == 1 and truth["max_allowed_effects"] == 1
    return {
        "run_id": ctx["run_id"], "scenario_id": truth["scenario_id"], "arm": ctx.get("arm"),
        "status": "OBSERVED" if not faults else "FAULT", "faults": sorted(set(faults)),
        "effect_count": n, "duplicate_effect": dup, "unauthorized_effect": n > truth["max_allowed_effects"],
        "false_completion": complete and n == 0, "true_completion": true_done,
        "s4_invalid_sends": invalid, "s4_objective_basis_existed_but_not_claimed": objective_only,
        "sends_judged": judged, "recovery_sends": len(judged),
        "safe_opportunity": truth["safe_opportunity"],
        "opportunity_completed": truth["safe_opportunity"] and true_done and invalid == 0 and not dup,
        "final_state": state, "unresolved": state in UNRESOLVED,
        "authority_denials": len(denials), "denial_reasons": [d.get("reasons") for d in denials],
        "first_classification": (first_plan or {}).get("plan", {}).get("classification"),
        "journal_dispatch_binding": journal_binding,
        "scorer": "scorer_v2", "scorer_uid": os.geteuid(), "scorer_pid": os.getpid(),
    }


if __name__ == "__main__":
    print(json.dumps(score(json.loads(Path(sys.argv[1]).read_text())), sort_keys=True))
