"""Agent process for EBRQ-v2 (ECR technical fix, async_sim_r2).

`send`    : delivery path with the six barrier receipts. The first dispatch is authorized by
            authority.py (PRE_DISPATCH) and follows grant -> consumed marker -> journal
            DispatchBoundaryCrossed -> HTTP. Mode R2A keeps the old journal format (key from
            configuration, assumption A4); mode R2B writes the logical operation key and the
            operation version into AttemptCommitted and binds the dispatch event to its grant.
`recover` : EBRQ-v2. Routing follows the frozen classifier and planner (unchanged), so the
            original mechanism's recovery paths and the effect of classifier errors stay
            observable; every send and every fence goes through authority.py, which decides
            from the durable state alone. A locally recorded definite failure is never a
            basis: it is handled like an unknown outcome.

Reference policies (CONV, STOP, RETRY) and the frozen EBR/EBRQ executor stay in agent_v1.py.
Frozen helpers are imported from agent_v1.py; every v1 method that could contact the endpoint
is overridden here so that it cannot be reached.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import agent_v1 as v1
from authority import Authority
from protocol import barrier, durable_json

Store, ledger_probe, IDENTITY_KEYS = v1.Store, v1.ledger_probe, v1.IDENTITY_KEYS


def _contract(configuration: dict) -> dict:
    c = configuration["endpoint_contract"]
    return {"query": bool(c["query"]), "idempotent": bool(c["idempotent"])}


# ---------------------------------------------------------------- send path
def send(configuration: dict) -> None:
    mode = configuration["r2_mode"]
    store = Store(Path(configuration["store"]))
    events, control, run_id = Path(configuration["events"]), Path(configuration["control"]), configuration["run_id"]
    durable_json(events / "ledger-access-send.json", ledger_probe(configuration))
    base = configuration["identity"]
    key = configuration["logical_operation_id"]
    queue_item_id = configuration["queue_item_id"]
    store.write("task.json", {**base, "deliveryState": "pending"})
    store.write("projection.json", {"kind": "DISCOVERED", "preparationState": "FULLY_PREPARED", "queueBinding": {
        "schemaVersion": 1, "correlationId": base["queueBindingId"], "opaqueMetadata": {
            "queueBindingId": base["queueBindingId"], "attempts": [{k: base[k] for k in IDENTITY_KEYS}]}}})

    def event(sequence: int, event_type: str, **extra) -> None:
        store.append({**base, "eventType": event_type, "eventId": f"{base['attemptId']}-e{sequence}",
                      "journalSequence": sequence, **extra})

    if mode == "R2B":
        event(1, "AttemptCommitted", logicalOperationId=key, operationVersion=base["taskStateVersion"])
    else:
        event(1, "AttemptCommitted")
    event(2, "QueueBindingPrepared")
    store.write("queue.json", {**base, "queueItemId": queue_item_id, "pending": True})
    event(3, "QueueItemPersisted", queueItemId=queue_item_id)
    barrier(events, control, run_id, "intent_persisted")

    authority = Authority(store.root, mode, key, _contract(configuration), configuration["port"])
    request = {"logical_operation_id": key, "attempt_id": base["attemptId"],
               "transport_request_id": configuration["transport_request_id"], "generation": 0}
    grant = authority.authorize("PRE_DISPATCH", request, first_dispatch=True)
    if grant is None:
        durable_json(events / "first-dispatch-denied.json", {"run_id": run_id})
        return

    def journal_dispatch(g: dict) -> None:
        if mode == "R2B":
            event(4, "DispatchBoundaryCrossed", queueItemId=queue_item_id, grantId=g["grant_id"],
                  logicalOperationId=g["logical_operation_id"], generation=g["generation"],
                  sendAttemptId=g["send_attempt_id"])
        else:
            event(4, "DispatchBoundaryCrossed", queueItemId=queue_item_id)

    result = authority.dispatch(
        grant, journal_writer=journal_dispatch,
        on_request_left=lambda: barrier(events, control, run_id, "request_left_client"),
        on_response=lambda: barrier(events, control, run_id, "response_received"),
        record_result=False)
    accepted = result.get("http_status") == 200 and result.get("status") == "COMMITTED"
    outcome = {k: v for k, v in result.items() if k != "http_status"}
    event(5, "AdapterOutcomeObserved", queueItemId=queue_item_id,
          outcome="adapter_accepted" if accepted else "definite_failure",
          evidenceLevel="adapter_accepted" if accepted else "adapter_rejected", endpointResponse=outcome)
    barrier(events, control, run_id, "response_persisted")
    durable_json(events / "unexpected-normal-completion.json", {"run_id": run_id})


# ------------------------------------------------------------ recovery side
class RecoveryV2(v1.Recovery):
    def __init__(self, configuration: dict):
        super().__init__(configuration)
        self.mode = configuration["r2_mode"]
        self.authority = Authority(self.store.root, self.mode, self.logical, _contract(configuration), self.port)

    # v1 methods that contact the endpoint are not reachable in EBRQ-v2
    def fence(self):
        raise PermissionError("EBRQ-v2: use authority.fence()")

    def new_send(self, *args, **kwargs):
        raise PermissionError("EBRQ-v2: use authority.authorize()/dispatch()")

    def run_ebr(self, *args, **kwargs):
        raise PermissionError("EBRQ-v2: frozen executor is in agent_v1.py")

    def queue_worker(self, *args, **kwargs):
        raise PermissionError("EBRQ-v2: queue dispatch goes through gated_send('PRE_DISPATCH')")

    def run_conv(self, *args, **kwargs):
        raise PermissionError("reference policies run from agent_v1.py")

    run_stop = run_retry = run_conv

    def endpoint_evidence(self, snapshot: dict, negative: bool, fenced: dict) -> dict:
        """Same projection as the frozen v1 method; only the persisted copy gets a fresh file name
        so that a later recovery (after a crash during recovery) does not overwrite evidence."""
        events = [dict(e) for e in snapshot["journalAttempt"]["events"]]
        local = [i for i, e in enumerate(events) if e.get("eventType") == "AdapterOutcomeObserved"]
        dispatch = next((e for e in events if e.get("eventType") == "DispatchBoundaryCrossed"), events[-1])
        record = {key: dispatch.get(key) for key in (*IDENTITY_KEYS, "queueBindingId", "queueItemId") if key in dispatch}
        record.update(eventType="AdapterOutcomeObserved", eventId=f"{self.c['attempt_id']}-endpoint-fence",
                      outcome="definite_failure" if negative else "adapter_accepted",
                      evidenceLevel="endpoint_authoritative" if negative else "adapter_accepted",
                      endpointFence=fenced)
        if local:
            record["journalSequence"] = events[local[-1]]["journalSequence"]
            events[local[-1]] = record
        else:
            record["journalSequence"] = max(int(e.get("journalSequence", 0)) for e in events) + 1
            events.append(record)
        name, n = "endpoint-evidence.json", 2
        while (self.store.root / name).exists():
            name, n = f"endpoint-evidence-{n}.json", n + 1
        self.store.write(name, record)
        return {**snapshot, "journalAttempt": {"events": events}}

    # ---------------------------------------------------------------- helpers
    def fail_closed(self, state: str, reason: str) -> None:
        self.set_task(state, reason)
        self.store.record({"action": "FAIL_CLOSED", "state": state, "reason": reason})

    def gated_fence(self) -> dict | None:
        out = self.authority.fence()
        if out is None:
            return None
        status, result, fid = out
        # same record shape as v1, so the historical scorer can compare fences
        self.store.record({"action": "FENCE", "http_status": status, "result": result, "fence_id": fid})
        return result

    def gated_send(self, basis: str, generation: int, note: str) -> str:
        """Apply for a basis; send only if the authority grants it. Returns "SENT" or "DENIED".
        A denial is recorded here; the caller decides the fail-closed state."""
        self.sends += 1
        # send-attempt identity must be unique across recovery processes: number it after every
        # grant ever written (including void ones), not after this process's send counter
        n = len(list((self.store.root / "authority" / "grants").glob("*.json"))) + 1
        request = {"logical_operation_id": self.logical,
                   "attempt_id": f"{self.c['attempt_id']}-r{n}",
                   "transport_request_id": f"{self.c['transport_request_id']}-r{n}",
                   "generation": generation}
        grant = self.authority.authorize(basis, request)
        if grant is None:
            self.store.record({"action": "AUTHORIZATION_DENIED", "basis": basis, "request": request, "note": note})
            return "DENIED"
        base = self.store.read("task.json") or {}
        # same record shape as v1 (historical scorer compatibility); the label is not trusted by scorer_v2
        self.store.record({"action": "AUTHORIZE_SEND", "guarantee": basis, "basis": note, "request": request,
                           "grant_id": grant["grant_id"], "task_state_version": base.get("taskStateVersion")})
        result = self.authority.dispatch(grant)
        status = result.get("http_status")
        self.store.record({"action": "SEND_RESULT", "http_status": status,
                           "result": {k: v for k, v in result.items() if k != "http_status"}, "request": request})
        if status == 200 and result.get("status") == "COMMITTED":
            self.set_task("adapter_accepted", "endpoint committed the authorized send")
            self.release_queue("send committed")
        return "SENT"

    def denied(self, what: str) -> None:
        report = self.authority.report()
        self.fail_closed("review" if not report.valid else "delivery_unknown", f"{what} denied by the authority")

    # ---------------------------------------------------------------- EBRQ-v2
    def unknown_route(self, cls: str, snapshot: dict, plan: dict) -> None:
        """Outcome may have been dispatched and is not established: endpoint guarantees only."""
        if self.contract["query"]:
            fenced = self.gated_fence()
            if fenced is None:
                self.denied("fence")
                return
            if fenced.get("result") in ("COMMITTED", "DEFINITELY_NOT_COMMITTED"):
                negative = fenced["result"] == "DEFINITELY_NOT_COMMITTED"
                evidence = self.endpoint_evidence(snapshot, negative, fenced)
                plan2 = self.plan(evidence)
                if negative and plan2["classification"] == "CONFIRMED_NO_ACCEPTANCE_EVIDENCE":
                    self.execute(plan2)
                    if self.gated_send("FENCED_NEGATIVE", int(fenced["retry_generation"]),
                                       "fenced endpoint negative") == "DENIED":
                        self.denied("FENCED_NEGATIVE")
                    return
                if self.execute(plan2):
                    if self.gated_send("PRE_DISPATCH", 0, "planner kept queue responsibility after endpoint evidence") == "DENIED":
                        self.denied("PRE_DISPATCH")
                return
            self.fail_closed("delivery_unknown", "endpoint answer inconclusive")
            return
        if self.contract["idempotent"]:
            if cls == "OUTCOME_UNKNOWN":
                self.execute(plan)
            else:
                self.set_task("delivery_unknown", "outcome not established by local evidence")
                self.store.record({"action": "TREATED_AS_UNKNOWN", "classification": cls})
            if self.gated_send("IDEMPOTENT", 0, "unknown outcome under server idempotency contract") == "DENIED":
                self.denied("IDEMPOTENT")
            return
        # P0: no endpoint guarantee; a dispatched operation is never resent
        if cls == "OUTCOME_UNKNOWN":
            self.execute(plan)
        else:
            self.fail_closed("delivery_unknown", "dispatch may have happened; no endpoint guarantee")

    def run_ebrq_v2(self) -> None:
        voided = self.authority.void_orphans()
        if voided:
            self.store.record({"action": "VOID_ORPHAN_GRANTS", "grants": voided})
        snapshot = self.snapshot()
        plan = self.plan(snapshot)
        cls = plan["classification"]
        self.store.record({"action": "VALIDATION", "report": self.authority.report().summary()})
        if cls in ("OUTCOME_UNKNOWN", "CONFIRMED_NO_ACCEPTANCE_EVIDENCE"):
            # CONFIRMED can only come from a local failure record: never a basis, handled as unknown
            if cls == "CONFIRMED_NO_ACCEPTANCE_EVIDENCE":
                self.store.record({"action": "LOCAL_FAILURE_TREATED_AS_UNKNOWN"})
            self.unknown_route(cls, snapshot, plan)
            return
        if self.execute(plan):
            if self.gated_send("PRE_DISPATCH", 0, "planner preserved/recreated queue responsibility (" + cls + ")") == "SENT":
                return
            # PRE_DISPATCH denied. If valid evidence shows the operation may already have been
            # dispatched (for example a consumed first-dispatch grant without a journal dispatch
            # event, or a classifier that missed the dispatch), take the unknown-outcome route;
            # it is itself fully gated. Otherwise fail closed.
            report = self.authority.report()
            if report.valid and report.dispatched and not report.accepted:
                self.store.record({"action": "REROUTE_TO_UNKNOWN", "reason": "PRE_DISPATCH denied: dispatch evidence exists"})
                self.unknown_route("PRE_DISPATCH_DENIED", snapshot, plan)
                return
            self.denied("PRE_DISPATCH")


def recover(configuration: dict) -> None:
    events = Path(configuration["events"])
    # the first recovery keeps the historical file name; a later recovery (after a crash during
    # recovery) records its own probe instead of overwriting evidence
    probe = events / "ledger-access-recover.json"
    n = 2
    while probe.exists():
        probe = events / f"ledger-access-recover-{n}.json"
        n += 1
    durable_json(probe, ledger_probe(configuration))
    recovery = RecoveryV2(configuration)
    recovery.store.record({"action": "RECOVERY_START", "policy": configuration["policy"], "r2_mode": recovery.mode})
    if configuration["policy"] != "EBRQ2":
        raise ValueError("agent_v2 runs only the EBRQ2 policy")
    recovery.run_ebrq_v2()
    recovery.store.write("final.json", {"policy": configuration["policy"], "r2_mode": recovery.mode,
                                        "task": recovery.store.read("task.json"),
                                        "queue": recovery.store.read("queue.json"), "sends": recovery.sends})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["send", "recover"])
    parser.add_argument("configuration", type=Path)
    args = parser.parse_args()
    os.umask(0o027)
    configuration = json.loads(args.configuration.read_text())
    (send if args.mode == "send" else recover)(configuration)


if __name__ == "__main__":
    main()
