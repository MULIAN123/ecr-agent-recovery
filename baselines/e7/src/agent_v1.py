"""Agent-side process for the asynchronous simulation (ECR experiment 2).

`send`    : durable delivery path with the six barrier receipts (killed at one).
`recover` : restart after the kill and apply one recovery policy.

Policies (arm configuration):
- EBR       frozen classifier + planner, intents executed; no endpoint query.
- EBRQ      EBR plus the declared query extension: endpoint fence/idempotency
            evidence is handed to the same classifier, and a new send is
            authorized only by a fenced negative or a server idempotency contract.
- EBRQ_*    EBRQ with an ablated classifier source (integrated ablation).
- CONV      conventional strong baseline: logical-operation id, query/fence first,
            idempotent retry when the contract allows, unknown otherwise; trusts
            recorded outcomes and pending queue items without evidence validation.
- STOP      any item that crossed dispatch goes to review; pre-dispatch continues.
- RETRY     no recorded acceptance => send again.

The process never reads the external-effect ledger (access attempts are recorded
as isolation evidence) and receives no scenario name or truth.
"""
from __future__ import annotations

import argparse
import errno
import http.client
import json
import os
import subprocess
import time
from pathlib import Path

from protocol import barrier, durable_json

IDENTITY_KEYS = ("taskId", "attemptId", "deliverableId", "artifactSetId", "artifactId",
                 "bindingGeneration", "taskStateVersion")
COMPLETE_STATES = ("adapter_accepted", "delivered")


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.journal = root / "journal"
        self.recovery = root / "recovery"

    def read(self, name: str):
        path = self.root / name
        return json.loads(path.read_text()) if path.is_file() else None

    def write(self, name: str, value, replace: bool = False) -> None:
        durable_json(self.root / name, value, replace=replace)

    def events(self) -> list[dict]:
        # Append order is the file index; journalSequence is a field inside the record.
        return [json.loads(p.read_text()) for p in sorted(self.journal.glob("*.json"))]

    def append(self, event: dict) -> None:
        index = len(list(self.journal.glob("*.json"))) + 1
        durable_json(self.journal / f"{index:04d}-{event['eventType']}.json", event)

    def record(self, value: dict) -> None:
        index = len(list(self.recovery.glob("*.json"))) + 1
        durable_json(self.recovery / f"{index:03d}.json", {"index": index, "utc_ns": time.time_ns(), **value})


def ledger_probe(configuration: dict) -> list[dict]:
    rows = []
    for action in ("read", "list"):
        row = {"action": action, "pid": os.getpid(), "uid": os.geteuid(), "observed": "ALLOWED"}
        try:
            if action == "read":
                with open(configuration["ledger"], "rb") as stream:
                    stream.read(1)
            else:
                os.listdir(Path(configuration["ledger"]).parent)
        except OSError as error:
            row["errno"] = error.errno
            row["observed"] = "DENIED" if error.errno in (errno.EACCES, errno.EPERM) else "ERROR"
        rows.append(row)
    return rows


def http_json(port: int, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=40)
    raw = None if body is None else json.dumps(body).encode()
    client.request(method, path, body=raw, headers={"Content-Type": "application/json"})
    response = client.getresponse()
    return response.status, json.loads(response.read() or b"{}")


# ---------------------------------------------------------------- send path
def send(configuration: dict) -> None:
    store = Store(Path(configuration["store"]))
    events, control, run_id = Path(configuration["events"]), Path(configuration["control"]), configuration["run_id"]
    durable_json(events / "ledger-access-send.json", ledger_probe(configuration))
    base = configuration["identity"]
    queue_item_id = configuration["queue_item_id"]
    store.write("task.json", {**base, "deliveryState": "pending"})
    store.write("projection.json", {"kind": "DISCOVERED", "preparationState": "FULLY_PREPARED", "queueBinding": {
        "schemaVersion": 1, "correlationId": base["queueBindingId"], "opaqueMetadata": {
            "queueBindingId": base["queueBindingId"], "attempts": [{key: base[key] for key in IDENTITY_KEYS}]}}})

    def event(sequence: int, event_type: str, **extra) -> None:
        store.append({**base, "eventType": event_type, "eventId": f"{base['attemptId']}-e{sequence}",
                      "journalSequence": sequence, **extra})

    event(1, "AttemptCommitted")
    event(2, "QueueBindingPrepared")
    store.write("queue.json", {**base, "queueItemId": queue_item_id, "pending": True})
    event(3, "QueueItemPersisted", queueItemId=queue_item_id)
    barrier(events, control, run_id, "intent_persisted")

    event(4, "DispatchBoundaryCrossed", queueItemId=queue_item_id)
    request = {"logical_operation_id": configuration["logical_operation_id"], "attempt_id": base["attemptId"],
               "transport_request_id": configuration["transport_request_id"], "generation": 0}
    client = http.client.HTTPConnection("127.0.0.1", configuration["port"], timeout=60)
    body = json.dumps(request).encode()
    client.putrequest("POST", "/send")
    client.putheader("Content-Type", "application/json")
    client.putheader("Content-Length", str(len(body)))
    client.endheaders(body)
    barrier(events, control, run_id, "request_left_client")
    response = client.getresponse()
    outcome = json.loads(response.read())
    barrier(events, control, run_id, "response_received")
    accepted = response.status == 200 and outcome.get("status") == "COMMITTED"
    event(5, "AdapterOutcomeObserved", queueItemId=queue_item_id,
          outcome="adapter_accepted" if accepted else "definite_failure",
          evidenceLevel="adapter_accepted" if accepted else "adapter_rejected", endpointResponse=outcome)
    barrier(events, control, run_id, "response_persisted")
    durable_json(events / "unexpected-normal-completion.json", {"run_id": run_id})


# ------------------------------------------------------------ recovery side
class Recovery:
    def __init__(self, configuration: dict):
        self.c = configuration
        self.store = Store(Path(configuration["store"]))
        self.port = configuration["port"]
        self.contract = configuration["endpoint_contract"]  # {"query": bool, "idempotent": bool}
        self.logical = configuration["logical_operation_id"]
        self.sends = 0

    # local state
    def snapshot(self) -> dict:
        return {"taskContext": self.store.read("task.json"),
                "journalAttempt": {"events": self.store.events()},
                "queueItem": self.store.read("queue.json")}

    def set_task(self, state: str, reason: str) -> None:
        task = self.store.read("task.json") or {}
        self.store.write("task.json", {**task, "deliveryState": state}, replace=True)
        self.store.record({"action": "SET_TASK_STATE", "state": state, "reason": reason})

    def release_queue(self, reason: str) -> None:
        queue = self.store.read("queue.json")
        if queue is not None:
            self.store.write("queue.json", {**queue, "pending": False}, replace=True)
        self.store.record({"action": "RELEASE_QUEUE", "reason": reason})

    # endpoint
    def fence(self) -> dict:
        status, result = http_json(self.port, "POST", "/fence", {"logical_operation_id": self.logical})
        self.store.record({"action": "FENCE", "http_status": status, "result": result})
        return result

    def new_send(self, generation: int, guarantee: str, basis: str) -> dict:
        """Authorize and issue one new external send; the decision is durable before the send."""
        self.sends += 1
        base = self.store.read("task.json") or {}
        request = {"logical_operation_id": self.logical,
                   "attempt_id": f"{self.c['attempt_id']}-r{self.sends}",
                   "transport_request_id": f"{self.c['transport_request_id']}-r{self.sends}",
                   "generation": generation}
        self.store.record({"action": "AUTHORIZE_SEND", "guarantee": guarantee, "basis": basis,
                           "request": request, "task_state_version": base.get("taskStateVersion")})
        status, result = http_json(self.port, "POST", "/send", request)
        self.store.record({"action": "SEND_RESULT", "http_status": status, "result": result, "request": request})
        if status == 200 and result.get("status") == "COMMITTED":
            self.set_task("adapter_accepted", "endpoint committed the authorized send")
            self.release_queue("send committed")
        return result

    # frozen planner
    def plan(self, snapshot: dict) -> dict:
        process = subprocess.run([self.c["node"], self.c["plan_bridge"], self.c["arm_dir"]],
                                 input=json.dumps({"snapshot": snapshot, "projection": self.store.read("projection.json")}),
                                 capture_output=True, text=True, timeout=20, cwd=self.c["arm_dir"])
        if process.returncode != 0:
            raise RuntimeError("planner bridge failed: " + process.stderr[-2000:])
        result = json.loads(process.stdout)
        self.store.record({"action": "PLAN", "node": result["node"], "plan": result["plan"]})
        return result["plan"]

    def execute(self, plan: dict) -> bool:
        """Execute planner intents; returns True when queue responsibility remains to be dispatched."""
        dispatch = False
        for intent in plan["orderedIntents"]:
            kind = intent["type"]
            if kind == "TRANSITION_TASK_SEMANTIC_STATE":
                self.set_task(intent["target"], "planner intent")
            elif kind == "RECORD_RECONCILIATION_APPLIED":
                self.store.record({"action": "RECONCILIATION_APPLIED", "classification": plan["classification"]})
            elif kind == "RELEASE_STALE_QUEUE_RESPONSIBILITY":
                self.release_queue("planner intent")
            elif kind == "PRESERVE_EXISTING_QUEUE_RESPONSIBILITY":
                dispatch = True
            elif kind in ("RECREATE_QUEUE_RESPONSIBILITY", "PREPARE_DURABLE_QUEUE_BINDING"):
                queue = self.store.read("queue.json") or {**(self.store.read("task.json") or {}),
                                                          "queueItemId": self.c["queue_item_id"]}
                self.store.write("queue.json", {**queue, "pending": True}, replace=True)
                self.store.record({"action": "QUEUE_RESPONSIBILITY_RECREATED", "intent": kind})
                dispatch = True
            elif kind in ("REQUIRE_REVIEW", "QUARANTINE_QUEUE_RESPONSIBILITY"):
                self.set_task("review", kind)
        if plan["decision"] == "NO_ACTION":
            self.store.record({"action": "HOLD", "classification": plan["classification"], "reason": plan["reason"]})
        return dispatch

    def queue_worker(self, basis: str) -> None:
        # A pending queue item is executed by the ordinary queue worker as a first dispatch.
        self.new_send(0, "PRE_DISPATCH", basis)

    # policies
    def run_ebr(self, query_extension: bool) -> None:
        snapshot = self.snapshot()
        plan = self.plan(snapshot)
        cls = plan["classification"]
        if query_extension and cls == "OUTCOME_UNKNOWN" and (self.contract["query"] or self.contract["idempotent"]):
            if self.contract["query"]:
                fenced = self.fence()
                if fenced.get("result") in ("COMMITTED", "DEFINITELY_NOT_COMMITTED"):
                    negative = fenced["result"] == "DEFINITELY_NOT_COMMITTED"
                    evidence = self.endpoint_evidence(snapshot, negative, fenced)
                    plan2 = self.plan(evidence)
                    if negative and plan2["classification"] == "CONFIRMED_NO_ACCEPTANCE_EVIDENCE":
                        self.execute(plan2)
                        self.new_send(int(fenced["retry_generation"]), "FENCED_NEGATIVE",
                                      "fenced endpoint negative classified CONFIRMED_NO_ACCEPTANCE_EVIDENCE")
                        return
                    if self.execute(plan2):
                        self.queue_worker("planner kept queue responsibility after endpoint evidence")
                    return
            else:
                self.execute(plan)
                self.new_send(0, "IDEMPOTENT", "OUTCOME_UNKNOWN under server idempotency contract")
                return
        if query_extension and cls == "CONFIRMED_NO_ACCEPTANCE_EVIDENCE":
            self.execute(plan)
            self.new_send(0, "CLASSIFIED_DEFINITE_FAILURE", "classifier confirmed definite pre-acceptance failure")
            return
        if self.execute(plan):
            self.queue_worker("planner preserved/recreated queue responsibility (" + cls + ")")

    def endpoint_evidence(self, snapshot: dict, negative: bool, fenced: dict) -> dict:
        """Endpoint-authoritative outcome supersedes a missing or lower-level local outcome record."""
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
        self.store.write("endpoint-evidence.json", record)
        return {**snapshot, "journalAttempt": {"events": events}}

    def run_conv(self) -> None:
        events = self.store.events()
        outcomes = [e for e in events if e.get("eventType") == "AdapterOutcomeObserved"]
        dispatched = any(e.get("eventType") == "DispatchBoundaryCrossed" for e in events)
        queue = self.store.read("queue.json")
        if outcomes and outcomes[-1].get("outcome") == "adapter_accepted":
            self.set_task("adapter_accepted", "recorded adapter outcome")
            self.release_queue("recorded adapter outcome")
            return
        if not dispatched and not outcomes:
            if queue and queue.get("pending"):
                self.queue_worker("pending queue item, no dispatch record")
            return
        if self.contract["query"]:
            fenced = self.fence()
            if fenced.get("result") == "COMMITTED":
                self.set_task("adapter_accepted", "endpoint query committed")
                self.release_queue("endpoint query committed")
            elif fenced.get("result") == "DEFINITELY_NOT_COMMITTED":
                self.new_send(int(fenced["retry_generation"]), "FENCED_NEGATIVE", "endpoint fenced negative")
            else:
                self.set_task("delivery_unknown", "endpoint query inconclusive")
        elif self.contract["idempotent"]:
            self.new_send(0, "IDEMPOTENT", "server idempotency contract")
        elif outcomes and outcomes[-1].get("outcome") == "definite_failure":
            self.new_send(0, "RECORDED_FAILURE", "recorded definite failure")
        else:
            self.set_task("delivery_unknown", "no query, no idempotency, no recorded outcome")

    def run_stop(self) -> None:
        events = self.store.events()
        queue = self.store.read("queue.json")
        if any(e.get("eventType") in ("DispatchBoundaryCrossed", "AdapterOutcomeObserved") for e in events):
            self.set_task("review", "stop-all after dispatch evidence")
        elif queue and queue.get("pending"):
            self.queue_worker("pending queue item before dispatch")

    def run_retry(self) -> None:
        events = self.store.events()
        outcomes = [e for e in events if e.get("eventType") == "AdapterOutcomeObserved"]
        queue = self.store.read("queue.json")
        if outcomes and outcomes[-1].get("outcome") == "adapter_accepted":
            self.set_task("adapter_accepted", "recorded adapter outcome")
            self.release_queue("recorded adapter outcome")
        elif any(e.get("eventType") == "DispatchBoundaryCrossed" for e in events) or outcomes:
            self.new_send(0, "NONE", "missing acknowledgement")
        elif queue and queue.get("pending"):
            self.queue_worker("pending queue item before dispatch")


def recover(configuration: dict) -> None:
    events = Path(configuration["events"])
    durable_json(events / "ledger-access-recover.json", ledger_probe(configuration))
    recovery = Recovery(configuration)
    recovery.store.record({"action": "RECOVERY_START", "policy": configuration["policy"]})
    policy = configuration["policy"]
    if policy == "EBR":
        recovery.run_ebr(query_extension=False)
    elif policy.startswith("EBRQ"):
        recovery.run_ebr(query_extension=True)
    elif policy == "CONV":
        recovery.run_conv()
    elif policy == "STOP":
        recovery.run_stop()
    elif policy == "RETRY":
        recovery.run_retry()
    else:
        raise ValueError("unknown policy")
    recovery.store.write("final.json", {"policy": policy, "task": recovery.store.read("task.json"),
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
