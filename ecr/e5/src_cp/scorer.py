"""Independent scorer for the asynchronous simulation (ECR experiment 2).

Reads the endpoint's external-effect ledger (read-only), the endpoint request log,
the copied agent store and the predeclared truth row. It never imports or runs
agent, planner or classifier code.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

COMPLETE = ("adapter_accepted", "delivered")
UNRESOLVED = ("delivery_unknown", "review", "pending")


def records(directory: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))]


def score(context: dict) -> dict:
    truth, faults = context["truth"], []
    logical, profile = context["logical_operation_id"], truth["profile"]
    db = sqlite3.connect("file:" + context["ledger"] + "?mode=ro", uri=True)
    try:
        effects = db.execute("SELECT seq,logical_operation_id,attempt_id,transport_request_id FROM effects ORDER BY seq").fetchall()
        fences = db.execute("SELECT generation FROM fences WHERE logical_operation_id=?", (logical,)).fetchall()
    finally:
        db.close()
    if any(row[1] != logical for row in effects):
        faults.append("FOREIGN_EFFECT_IN_LEDGER")
    effect_count = sum(row[1] == logical for row in effects)

    store = Path(context["sut_copy"])
    final_path = store / "store/final.json"
    if not final_path.is_file():
        faults.append("MISSING_FINAL_STATE")
        final = {}
    else:
        final = json.loads(final_path.read_text())
    final_state = (final.get("task") or {}).get("deliveryState")
    actions = records(store / "store/recovery")
    for name in ("ledger-access-send.json", "ledger-access-recover.json"):
        path = store / "events" / name
        rows = json.loads(path.read_text()) if path.is_file() else []
        if not rows or any(r.get("observed") != "DENIED" for r in rows):
            faults.append("SUT_LEDGER_ACCESS_NOT_DENIED")
    injection = json.loads(Path(context["injection"]).read_text())
    if injection.get("exit_status") != -9 or injection.get("barrier_id") != truth["barrier_id"]:
        faults.append("INJECTION_RECEIPT_MISMATCH")

    # Every non-original send seen by the endpoint must match a durable authorization record.
    endpoint_sends = [r for r in records(Path(context["endpoint_events"])) if r.get("kind") == "send" and not r.get("gated")]
    authorizations = [a for a in actions if a.get("action") == "AUTHORIZE_SEND"]
    authorized_transports = {a["request"]["transport_request_id"] for a in authorizations}
    if any(s["request"].get("transport_request_id") not in authorized_transports for s in endpoint_sends):
        faults.append("UNRECORDED_SEND")
    endpoint_fences = [r["result"] for r in records(Path(context["endpoint_events"])) if r.get("kind") == "fence"]
    if endpoint_fences != [a.get("result") for a in actions if a.get("action") == "FENCE"]:
        faults.append("FENCE_RECORD_MISMATCH")

    false_safe, judged = 0, []
    fence_results = []
    for action in actions:
        if action.get("action") == "FENCE":
            fence_results.append(action.get("result", {}))
        if action.get("action") != "AUTHORIZE_SEND":
            continue
        guarantee = action.get("guarantee")
        last_fence = fence_results[-1] if fence_results else {}
        valid = truth["send_permitted"] and (
            (guarantee == "PRE_DISPATCH" and not truth["dispatched"])
            or (guarantee == "FENCED_NEGATIVE" and profile in ("P1", "P3")
                and last_fence.get("result") == "DEFINITELY_NOT_COMMITTED"
                and action["request"].get("generation") == last_fence.get("retry_generation"))
            or (guarantee == "IDEMPOTENT" and profile in ("P2", "P3")))
        false_safe += not valid
        judged.append({"guarantee": guarantee, "contract_valid": valid})

    complete = final_state in COMPLETE
    duplicate = effect_count > 1
    unauthorized_effect = effect_count > truth["max_allowed_effects"]
    false_completion = complete and effect_count == 0
    true_completion = complete and effect_count == 1 and truth["max_allowed_effects"] == 1
    opportunity_completed = truth["safe_opportunity"] and true_completion and false_safe == 0 and not duplicate
    return {
        "run_id": context["run_id"], "scenario_id": truth["scenario_id"], "arm": context["arm"],
        "status": "OBSERVED" if not faults else "FAULT", "faults": sorted(set(faults)),
        "effect_count": effect_count, "duplicate_effect": duplicate, "unauthorized_effect": unauthorized_effect,
        "final_state": final_state, "true_completion": true_completion, "false_completion": false_completion,
        "unresolved": final_state in UNRESOLVED, "sends_authorized": len(authorizations),
        "false_safe_authorizations": false_safe, "authorization_judgements": judged,
        "fences": len(fences), "safe_opportunity": truth["safe_opportunity"],
        "opportunity_completed": opportunity_completed,
        "completion_without_opportunity": true_completion and not truth["safe_opportunity"],
        "scorer_uid": os.geteuid(), "scorer_pid": os.getpid(),
    }


if __name__ == "__main__":
    print(json.dumps(score(json.loads(Path(sys.argv[1]).read_text())), sort_keys=True))
