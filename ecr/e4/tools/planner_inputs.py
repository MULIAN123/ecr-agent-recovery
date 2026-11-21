"""Fixed planner input set for the phase-3 planner qualification (Q1 equivalence, Q4 stability).

18 inputs = 9 durable journal states x 2 journal formats (R2A = historical, R2B = with key/version
and grant binding). The 9 states cover every classifier branch the 52 + 12 scenarios reach:
  S1 pre-dispatch (B1)            S2 dispatched, no outcome (B2-B5)   S3 accepted (B6)
  S4 weak ack (F3)                S5 local definite failure (F7)      S6 superseded version (F5)
  S7 foreign accepted outcome (F2) S8 reordered callback (F4)         S9 lost dispatch + failure (F6)
The builder is self-contained (same event format as agent_v1.send / agent_v2.send and
tests/fixtures.py) and deterministic: the output file is byte-identical on every run.
Usage: python -I planner_inputs.py <out.json>     (refuses to overwrite)
"""
import hashlib
import json
import sys
from pathlib import Path

KEYS = ("taskId", "attemptId", "deliverableId", "artifactSetId", "artifactId", "bindingGeneration", "taskStateVersion")
GRANT = {"grant_id": "Q-g1", "logical_operation_id": "Q-op", "generation": 0, "send_attempt_id": "Q-t1"}


def ids(name="Q"):
    return {"artifactId": f"{name}-artifact", "artifactSetId": f"{name}-set", "attemptId": f"{name}-a1",
            "bindingGeneration": 1, "deliverableId": f"{name}-deliverable", "queueBindingId": f"{name}-binding",
            "taskId": f"{name}-task", "taskStateVersion": 1}


def events(mode, upto, *, outcome=None, level=None, outcome_seq=5, outcome_base=None):
    base = ids()
    out = []

    def ev(seq, etype, b=base, **extra):
        out.append({**b, "eventType": etype, "eventId": f"{b['attemptId']}-e{seq}", "journalSequence": seq, **extra})

    if upto >= 1:
        ev(1, "AttemptCommitted", **({"logicalOperationId": "Q-op", "operationVersion": 1} if mode == "R2B" else {}))
    if upto >= 2:
        ev(2, "QueueBindingPrepared")
    if upto >= 3:
        ev(3, "QueueItemPersisted", queueItemId="Q-queue")
    if upto >= 4:
        extra = {"queueItemId": "Q-queue"}
        if mode == "R2B":
            extra.update(grantId=GRANT["grant_id"], logicalOperationId=GRANT["logical_operation_id"],
                         generation=GRANT["generation"], sendAttemptId=GRANT["send_attempt_id"])
        ev(4, "DispatchBoundaryCrossed", **extra)
    if outcome is not None:
        b = outcome_base or base
        out.append({**b, "eventType": "AdapterOutcomeObserved", "eventId": f"{b['attemptId']}-e5",
                    "journalSequence": outcome_seq, "queueItemId": "Q-queue", "outcome": outcome, "evidenceLevel": level})
    return out


def build():
    base = ids()
    task = {**base, "deliveryState": "pending"}
    queue = {**base, "queueItemId": "Q-queue", "pending": True}
    projection = {"kind": "DISCOVERED", "preparationState": "FULLY_PREPARED", "queueBinding": {
        "schemaVersion": 1, "correlationId": base["queueBindingId"], "opaqueMetadata": {
            "queueBindingId": base["queueBindingId"], "attempts": [{k: base[k] for k in KEYS}]}}}
    foreign = {**base, "attemptId": base["attemptId"] + "-stale", "artifactId": base["artifactId"] + "-v0"}
    states = {
        "S1_pre_dispatch": dict(upto=3),
        "S2_dispatched": dict(upto=4),
        "S3_accepted": dict(upto=4, outcome="adapter_accepted", level="adapter_accepted"),
        "S4_weak_ack": dict(upto=4, outcome="adapter_accepted", level="transport_ack"),
        "S5_local_failure": dict(upto=4, outcome="definite_failure", level="adapter_rejected"),
        "S6_superseded": dict(upto=3, task={**task, "taskStateVersion": 2}),
        "S7_foreign_accept": dict(upto=4, outcome="adapter_accepted", level="adapter_accepted", outcome_base=foreign),
        "S8_reordered": dict(upto=4, outcome="adapter_accepted", level="adapter_accepted", outcome_seq=3),
        "S9_lost_dispatch_failure": dict(upto=3, outcome="definite_failure", level="adapter_rejected"),
    }
    inputs = []
    for name, spec in states.items():
        spec = dict(spec)
        t = spec.pop("task", task)
        for mode in ("R2A", "R2B"):
            snapshot = {"taskContext": t, "journalAttempt": {"events": events(mode, **spec)}, "queueItem": queue}
            payload = json.dumps({"snapshot": snapshot, "projection": projection})
            inputs.append({"input_id": f"{name}:{mode}", "payload": payload,
                           "payload_sha256": hashlib.sha256(payload.encode()).hexdigest()})
    return {"description": "phase-3 fixed planner input set (18 inputs)", "inputs": inputs}


def main():
    out = Path(sys.argv[1])
    if out.exists():
        sys.exit(f"refusing to overwrite {out}")
    data = json.dumps(build(), indent=1, sort_keys=True) + "\n"
    out.write_bytes(data.encode())
    print(json.dumps({"inputs": len(build()["inputs"]), "sha256": hashlib.sha256(data.encode()).hexdigest()}))


if __name__ == "__main__":
    main()
