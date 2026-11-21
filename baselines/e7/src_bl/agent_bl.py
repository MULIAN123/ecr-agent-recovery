"""Agent process for the CCF-A rule-level baselines (ECR baseline batch bl1).

Two recovery policies re-implemented from published rules, on the frozen v1 send path. Neither
is the original system: Beldi, Olive and RIFL keep the effect inside storage they control, and our
endpoint is a third-party service the agent does not control. Each rule below cites its source.

`send`    : the frozen v1 delivery path (agent_v1.send), unchanged.
`recover` : one of
  INTENT_LOG  Beldi (OSDI'20, Sec. 4) / Olive (OSDI'16, Sec. 3.1) intent-log re-execution.
              The intent is this delivery; its instance id is the attempt id, its stable step key
              the logical operation id (from configuration, as for CONV). The step log is the
              journal's AdapterOutcomeObserved records of this instance (lookup by attemptId, so a
              record of another instance is not found under this key).
              - logged step result adapter_accepted -> replay it: task adapter_accepted, release queue;
              - logged step result definite_failure -> replay it: the intent's own logic surfaces the
                failure; no re-execution of a completed step -> task review;
              - step started (DispatchBoundaryCrossed of this instance) but no logged result ->
                re-execute the step with the same key (calls outside the log are at-least-once,
                Beldi Sec. 4.5); claimed basis IDEMPOTENT (safety rests on callee de-duplication
                by the stable key);
              - step not started and queue item pending -> execute it (first dispatch, PRE_DISPATCH).
              No endpoint query (neither system queries an external service), no check of task
              version, sequence or acknowledgement level.
  RIFL_C      Client side of RIFL (SOSP'15, Sec. 3, 4.3). Client ids come from leases that are not
              durable on the client; after the SIGKILL the recovering process is a new client and
              cannot rendezvous with completion records of the old client's RPCs, so it never
              retries an RPC whose outcome it did not record; RIFL reports such cases as ambiguous.
              Recorded responses are looked up by the RPC's attempt id.
              - recorded response adapter_accepted -> task adapter_accepted, release queue;
              - recorded response definite_failure -> the RPC completed with a failure result; it is
                not retried -> task review;
              - RPC issued (DispatchBoundaryCrossed of this attempt) but no recorded response ->
                ambiguous, reported -> task delivery_unknown;
              - RPC not issued and queue item pending -> issue it (first dispatch, PRE_DISPATCH).
              No endpoint query, no check of task version, sequence or acknowledgement level.
The frozen agent_v1.py is imported unchanged; only Recovery is extended.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import agent_v1 as v1
from protocol import durable_json

POLICIES = ("INTENT_LOG", "RIFL_C")


class BaselineRecovery(v1.Recovery):
    def own_events(self) -> tuple[list[dict], list[dict]]:
        """Journal records of this instance / RPC (keyed lookup by attemptId)."""
        mine = [e for e in self.store.events() if e.get("attemptId") == self.c["attempt_id"]]
        outcomes = [e for e in mine if e.get("eventType") == "AdapterOutcomeObserved"]
        started = [e for e in mine if e.get("eventType") == "DispatchBoundaryCrossed"]
        return outcomes, started

    def replay_logged(self, outcomes: list[dict], source: str) -> None:
        last = outcomes[-1]
        self.store.record({"action": "LOGGED_RESULT_REPLAYED", "source": source,
                           "eventId": last.get("eventId"), "outcome": last.get("outcome")})
        if last.get("outcome") == "adapter_accepted":
            self.set_task("adapter_accepted", f"{source}: logged result adapter_accepted")
            self.release_queue(f"{source}: logged result adapter_accepted")
        else:
            self.set_task("review", f"{source}: logged result {last.get('outcome')}, completed step not re-executed")

    def run_intent_log(self) -> None:
        outcomes, started = self.own_events()
        queue = self.store.read("queue.json")
        if outcomes:
            self.replay_logged(outcomes, "INTENT_LOG")
        elif started:
            self.store.record({"action": "REEXECUTE_STEP", "reason": "step started, no logged result (at-least-once)"})
            self.new_send(0, "IDEMPOTENT", "intent-log re-execution with the stable step key")
        elif queue and queue.get("pending"):
            self.queue_worker("intent not started: execute step")
        else:
            self.store.record({"action": "HOLD", "reason": "no intent step to execute"})

    def run_rifl_c(self) -> None:
        outcomes, started = self.own_events()
        queue = self.store.read("queue.json")
        if outcomes:
            self.replay_logged(outcomes, "RIFL_C")
        elif started:
            self.set_task("delivery_unknown", "RPC issued by a crashed client; completion record unreachable (ambiguous)")
        elif queue and queue.get("pending"):
            self.queue_worker("RPC not issued: first dispatch")
        else:
            self.store.record({"action": "HOLD", "reason": "no RPC to issue"})


def recover(configuration: dict) -> None:
    events = Path(configuration["events"])
    durable_json(events / "ledger-access-recover.json", v1.ledger_probe(configuration))
    recovery = BaselineRecovery(configuration)
    policy = configuration["policy"]
    recovery.store.record({"action": "RECOVERY_START", "policy": policy})
    if policy == "INTENT_LOG":
        recovery.run_intent_log()
    elif policy == "RIFL_C":
        recovery.run_rifl_c()
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
    (v1.send if args.mode == "send" else recover)(configuration)


if __name__ == "__main__":
    main()
