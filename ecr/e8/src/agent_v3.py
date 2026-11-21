"""Agent process for ECR-G v3 (bl6): EBRQ-v2 plus endpoint reconciliation under quarantined local evidence.

Identical to agent_v2.RecoveryV2 on every path EXCEPT one: when the frozen classifier returns
CORRUPT_OR_UNREADABLE_EVIDENCE, v2 executed the planner's REQUIRE_REVIEW intents. v3 instead asks
AuthorityV3 whether the operation may be settled by the endpoint alone (quarantine eligibility Q1-Q3,
see authority_v3.py). If eligible:
  P1/P3 : quarantined fence; COMMITTED -> adapter_accepted and release queue (endpoint-authoritative, no send);
          DEFINITELY_NOT_COMMITTED -> one FENCED_NEGATIVE send with the fenced generation (quarantined grant);
          anything else -> delivery_unknown
  P2    : one IDEMPOTENT send with the original key (quarantined grant)
  P0    : planner intents as in v2 (review)
If not eligible, or a quarantined fence/grant is denied: planner intents as in v2 (review), fail closed.
The send path (agent_v2.send) and the frozen classifier/planner are unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import agent_v2 as v2
from authority_v3 import AuthorityV3
from protocol import durable_json

CORRUPT = "CORRUPT_OR_UNREADABLE_EVIDENCE"


class RecoveryV3(v2.RecoveryV2):
    def __init__(self, configuration: dict):
        super().__init__(configuration)
        self.authority = AuthorityV3(self.store.root, self.mode, self.logical, v2._contract(configuration), self.port)

    def gated_send_quarantined(self, basis: str, generation: int, note: str) -> str:
        self.sends += 1
        n = len(list((self.store.root / "authority" / "grants").glob("*.json"))) + 1
        request = {"logical_operation_id": self.logical,
                   "attempt_id": f"{self.c['attempt_id']}-r{n}",
                   "transport_request_id": f"{self.c['transport_request_id']}-r{n}",
                   "generation": generation}
        grant = self.authority.authorize_quarantined(basis, request)
        if grant is None:
            self.store.record({"action": "AUTHORIZATION_DENIED", "basis": basis, "request": request, "note": note,
                               "quarantined": True})
            return "DENIED"
        base = self.store.read("task.json") or {}
        self.store.record({"action": "AUTHORIZE_SEND", "guarantee": basis, "basis": note, "request": request,
                           "grant_id": grant["grant_id"], "task_state_version": base.get("taskStateVersion"),
                           "quarantined": True})
        result = self.authority.dispatch(grant)
        status = result.get("http_status")
        self.store.record({"action": "SEND_RESULT", "http_status": status,
                           "result": {k: v for k, v in result.items() if k != "http_status"}, "request": request})
        if status == 200 and result.get("status") == "COMMITTED":
            self.set_task("adapter_accepted", "endpoint committed the quarantined-path send")
            self.release_queue("send committed")
        return "SENT"

    def quarantine_route(self, cls: str, plan: dict) -> None:
        q = self.authority.quarantine_eligibility()
        self.store.record({"action": "QUARANTINE_LOCAL_EVIDENCE", "classification": cls, "eligibility": q,
                           "contract": self.contract})
        if not q["eligible"] or not (self.contract["query"] or self.contract["idempotent"]):
            self.execute(plan)  # v2 behaviour: REQUIRE_REVIEW
            return
        if self.contract["query"]:
            out = self.authority.fence_quarantined()
            if out is None:
                self.fail_closed("review", "quarantined fence denied by the authority")
                return
            status, result, fid = out
            self.store.record({"action": "FENCE", "http_status": status, "result": result, "fence_id": fid,
                               "quarantined": True})
            if result.get("result") == "COMMITTED":
                self.set_task("adapter_accepted", "endpoint fence reports COMMITTED (local evidence quarantined)")
                self.release_queue("endpoint fence reports COMMITTED")
            elif result.get("result") == "DEFINITELY_NOT_COMMITTED":
                if self.gated_send_quarantined("FENCED_NEGATIVE", int(result["retry_generation"]),
                                               "fenced endpoint negative (local evidence quarantined)") == "DENIED":
                    self.fail_closed("review", "quarantined FENCED_NEGATIVE denied")
            else:
                self.fail_closed("delivery_unknown", "endpoint answer inconclusive (local evidence quarantined)")
            return
        if self.gated_send_quarantined("IDEMPOTENT", 0, "server idempotency contract (local evidence quarantined)") == "DENIED":
            self.fail_closed("review", "quarantined IDEMPOTENT denied")

    def run_ebrq_v3(self) -> None:
        voided = self.authority.void_orphans()
        if voided:
            self.store.record({"action": "VOID_ORPHAN_GRANTS", "grants": voided})
        snapshot = self.snapshot()
        plan = self.plan(snapshot)
        cls = plan["classification"]
        self.store.record({"action": "VALIDATION", "report": self.authority.report().summary()})
        if cls == CORRUPT:
            self.quarantine_route(cls, plan)
            return
        # ---- from here on: the v2 body, unchanged ----
        if cls in ("OUTCOME_UNKNOWN", "CONFIRMED_NO_ACCEPTANCE_EVIDENCE"):
            if cls == "CONFIRMED_NO_ACCEPTANCE_EVIDENCE":
                self.store.record({"action": "LOCAL_FAILURE_TREATED_AS_UNKNOWN"})
            self.unknown_route(cls, snapshot, plan)
            return
        if self.execute(plan):
            if self.gated_send("PRE_DISPATCH", 0, "planner preserved/recreated queue responsibility (" + cls + ")") == "SENT":
                return
            report = self.authority.report()
            if report.valid and report.dispatched and not report.accepted:
                self.store.record({"action": "REROUTE_TO_UNKNOWN", "reason": "PRE_DISPATCH denied: dispatch evidence exists"})
                self.unknown_route("PRE_DISPATCH_DENIED", snapshot, plan)
                return
            self.denied("PRE_DISPATCH")


def recover(configuration: dict) -> None:
    events = Path(configuration["events"])
    probe = events / "ledger-access-recover.json"
    n = 2
    while probe.exists():
        probe = events / f"ledger-access-recover-{n}.json"
        n += 1
    durable_json(probe, v2.ledger_probe(configuration))
    recovery = RecoveryV3(configuration)
    recovery.store.record({"action": "RECOVERY_START", "policy": configuration["policy"], "r2_mode": recovery.mode})
    if configuration["policy"] != "EBRQ3":
        raise ValueError("agent_v3 runs only the EBRQ3 policy")
    recovery.run_ebrq_v3()
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
    (v2.send if args.mode == "send" else recover)(configuration)


if __name__ == "__main__":
    main()
