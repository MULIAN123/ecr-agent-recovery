"""Development harness for async_sim_r2 (unprivileged, no OS-identity isolation).

Reproduces the barrier / SIGKILL / in-flight-fate / overlay mechanics of async_sim_r1/run.py
for single cells, plus an auto-release mode for crash-hook tests. Results produced with this
harness are development tests only and are never reported as experiment results.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
SRC = PKG / "src"
sys.path.insert(0, str(SRC))
from protocol import durable_json  # noqa: E402

NODE = os.environ.get("ECR_NODE", "/opt/ecr-followup-r1-runtime/node-v24.21.0/bin/node")
PY = sys.executable
BARRIER_INDEX = {"B1": 1, "B2_DROP": 2, "B2_LATE": 2, "B2_ARRIVE": 2, "B3_LATE": 3, "B4": 4, "B5": 5, "B6": 6}
PROFILES = {"P0": (False, False), "P1": (True, False), "P2": (False, True), "P3": (True, True)}
OVERLAY_BASE = {"F2_FOREIGN_ACCEPT": "B2_DROP", "F3_WEAK_ACK": "B2_DROP", "F4_REORDERED_CALLBACK": "B5",
                "F5_SUPERSEDED_VERSION": "B1", "F6_LOST_DISPATCH_FAILURE": "B5"}


def truth_for(barrier: str, profile: str, overlay: str | None) -> dict:
    """Same rules as the frozen r1 truth table; F7 (local failure after a valid dispatch) follows
    the rule for dispatched crash points. Development use only."""
    dispatched = barrier != "B1"
    f5 = overlay == "F5_SUPERSEDED_VERSION"
    if f5:
        opp = False
    elif overlay is None and barrier in ("B1", "B6"):
        opp = True
    else:
        opp = profile != "P0"
    sid = f"{overlay or barrier}{'_' + barrier if overlay and overlay.startswith('F7') else ''}_{profile}"
    return {"scenario_id": sid, "barrier_id": f"B{BARRIER_INDEX[barrier]}", "base": barrier, "overlay": overlay,
            "profile": profile, "dispatched": dispatched, "max_allowed_effects": 0 if f5 else 1,
            "send_permitted": not f5, "safe_opportunity": opp}


def ids_for(name: str) -> dict:
    return {"identity": {"artifactId": f"{name}-artifact", "artifactSetId": f"{name}-set", "attemptId": f"{name}-a1",
                         "bindingGeneration": 1, "deliverableId": f"{name}-deliverable", "queueBindingId": f"{name}-binding",
                         "taskId": f"{name}-task", "taskStateVersion": 1},
            "logical_operation_id": f"{name}-op", "queue_item_id": f"{name}-queue", "transport_request_id": f"{name}-t1"}


def release(control: Path, name: str) -> None:
    target = control / name
    if not target.exists():
        try:
            durable_json(target, {"released_utc_ns": time.time_ns()})
        except FileExistsError:
            pass


def wait_for(path: Path, seconds: float, proc: subprocess.Popen | None = None) -> bool:
    deadline = time.monotonic() + seconds
    while not path.exists():
        if proc is not None and proc.poll() is not None:
            return False
        if time.monotonic() > deadline:
            raise TimeoutError(str(path))
        time.sleep(0.01)
    return True


def start(args: list[str], out: Path, cwd: Path, env_extra: dict | None = None) -> subprocess.Popen:
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
           **(env_extra or {})}
    return subprocess.Popen([PY, "-s", "-E", *args], cwd=cwd, env=env,
                            stdout=open(out.with_suffix(".stdout"), "wb"), stderr=open(out.with_suffix(".stderr"), "wb"))


def apply_overlay(store: Path, overlay: str | None, ids: dict) -> None:
    if not overlay:
        return
    journal = store / "journal"
    base = ids["identity"]
    outcome_base = {**base, "eventType": "AdapterOutcomeObserved", "queueItemId": ids["queue_item_id"]}
    if overlay == "F2_FOREIGN_ACCEPT":
        durable_json(journal / "0005-AdapterOutcomeObserved.json",
                     {**outcome_base, "attemptId": base["attemptId"] + "-stale", "artifactId": base["artifactId"] + "-v0",
                      "eventId": base["attemptId"] + "-stale-e5", "journalSequence": 5,
                      "outcome": "adapter_accepted", "evidenceLevel": "adapter_accepted"})
    elif overlay == "F3_WEAK_ACK":
        durable_json(journal / "0005-AdapterOutcomeObserved.json",
                     {**outcome_base, "eventId": base["attemptId"] + "-e5", "journalSequence": 5,
                      "outcome": "adapter_accepted", "evidenceLevel": "transport_ack"})
    elif overlay == "F4_REORDERED_CALLBACK":
        durable_json(journal / "0005-AdapterOutcomeObserved.json",
                     {**outcome_base, "eventId": base["attemptId"] + "-e5", "journalSequence": 3,
                      "outcome": "adapter_accepted", "evidenceLevel": "adapter_accepted"})
    elif overlay == "F5_SUPERSEDED_VERSION":
        task = json.loads((store / "task.json").read_text())
        durable_json(store / "task.json", {**task, "taskStateVersion": task["taskStateVersion"] + 1}, replace=True)
    elif overlay == "F6_LOST_DISPATCH_FAILURE":
        (journal / "0004-DispatchBoundaryCrossed.json").unlink()
        durable_json(journal / "0005-AdapterOutcomeObserved.json",
                     {**outcome_base, "eventId": base["attemptId"] + "-e5", "journalSequence": 5,
                      "outcome": "definite_failure", "evidenceLevel": "adapter_rejected"})
    elif overlay == "F7_LOCAL_FAILURE_AFTER_DISPATCH":
        if not (journal / "0004-DispatchBoundaryCrossed.json").exists():
            raise RuntimeError("F7 requires a valid journal dispatch event")
        durable_json(journal / "0005-AdapterOutcomeObserved.json",
                     {**outcome_base, "eventId": base["attemptId"] + "-e5", "journalSequence": 5,
                      "outcome": "definite_failure", "evidenceLevel": "adapter_rejected"})
    else:
        raise ValueError(overlay)


class Cell:
    def __init__(self, work: Path, name: str, *, agent: str, policy: str, mode: str | None, arm: str,
                 barrier: str | None, profile: str, overlay: str | None = None):
        self.work, self.name, self.agent, self.policy, self.mode, self.arm = work / name, name, agent, policy, mode, arm
        self.barrier, self.profile, self.overlay = barrier, profile, overlay
        if self.work.exists():
            shutil.rmtree(self.work)
        for d in ("sut/events", "sut/control", "sut/store/journal", "sut/store/recovery",
                  "adapter/events", "adapter/control", "adapter/data", "logs"):
            (self.work / d).mkdir(parents=True)
        self.sut, self.adapter = self.work / "sut", self.work / "adapter"
        self.ids = ids_for(name)
        q, i = PROFILES[profile]
        self.contract = {"query": q, "idempotent": i}
        self.endpoint = None
        self.port = None
        self.truth = truth_for(barrier or "B1", profile, overlay) if barrier else None
        self._stop_auto = threading.Event()

    # ---------------------------------------------------------------- processes
    def start_endpoint(self) -> None:
        cfg = {"run_id": self.name, "profile": self.profile, "db": str(self.adapter / "data/effects.sqlite3"),
               "gated_transport": self.ids["transport_request_id"], "events": str(self.adapter / "events"),
               "control": str(self.adapter / "control")}
        (self.adapter / "configuration.json").write_text(json.dumps(cfg))
        self.endpoint = start([str(SRC / "endpoint.py"), str(self.adapter / "configuration.json")],
                              self.work / "logs/endpoint", SRC)
        wait_for(self.adapter / "events/ready.json", 15)
        self.port = json.loads((self.adapter / "events/ready.json").read_text())["port"]

    def common(self) -> dict:
        return {"run_id": self.name, "store": str(self.sut / "store"), "events": str(self.sut / "events"),
                "control": str(self.sut / "control"), "port": self.port, "ledger": str(self.adapter / "data/effects.sqlite3"),
                "identity": self.ids["identity"], "queue_item_id": self.ids["queue_item_id"],
                "logical_operation_id": self.ids["logical_operation_id"], "attempt_id": self.ids["identity"]["attemptId"],
                "transport_request_id": self.ids["transport_request_id"], "endpoint_contract": self.contract,
                "r2_mode": self.mode}

    def agent_file(self) -> Path:
        return SRC / ("agent_v2.py" if self.agent == "v2" else "agent_v1.py")

    def start_send(self, env: dict | None = None) -> subprocess.Popen:
        path = self.sut / "send-configuration.json"
        path.write_text(json.dumps(self.common()))
        return start([str(self.agent_file()), "send", str(path)], self.work / "logs/agent-send", SRC, env)

    def recover(self, env: dict | None = None, label: str = "agent-recover", timeout: float = 90) -> int:
        cfg = {**self.common(), "policy": self.policy, "node": NODE, "plan_bridge": str(SRC / "plan.mjs"),
               "arm_dir": str(PKG / "arms" / self.arm)}
        path = self.sut / f"{label}-configuration.json"
        path.write_text(json.dumps(cfg))
        p = start([str(self.agent_file()), "recover", str(path)], self.work / f"logs/{label}", SRC, env)
        return p.wait(timeout=timeout)

    def settle_original(self, seconds: float = 40) -> dict:
        events, control = self.adapter / "events", self.adapter / "control"
        deadline = time.monotonic() + seconds
        while not (events / "original-done.json").is_file():
            for index in (3, 4):
                if (events / f"B{index}-ready.json").is_file():
                    release(control, f"release-B{index}")
            if time.monotonic() > deadline:
                raise TimeoutError("original request did not settle")
            time.sleep(0.01)
        return json.loads((events / "original-done.json").read_text())

    def _auto_release(self) -> None:
        while not self._stop_auto.is_set():
            for i in range(1, 7):
                for root in (self.sut, self.adapter):
                    if (root / f"events/B{i}-ready.json").is_file():
                        release(root / "control", f"release-B{i}")
            time.sleep(0.01)

    # ---------------------------------------------------------------- scenario flows
    def run_to_barrier(self) -> None:
        """r1 semantics: run the send path to the scenario's barrier and SIGKILL it there."""
        self.start_endpoint()
        sut = self.start_send()
        target = BARRIER_INDEX[self.barrier]
        for index in range(1, target + 1):
            root = self.adapter if index in (3, 4) else self.sut
            wait_for(root / f"events/B{index}-ready.json", 25)
            if index == target:
                os.kill(sut.pid, signal.SIGKILL)
                sut.wait(timeout=10)
            else:
                release(root / "control", f"release-B{index}")
                if index == 2:
                    release(self.adapter / "control", "allow-accept")
        if self.barrier == "B2_DROP":
            release(self.adapter / "control", "drop-request")
            self.settle_original()
        elif self.barrier == "B2_ARRIVE":
            release(self.adapter / "control", "allow-accept")
            self.settle_original()
        elif self.barrier == "B4":
            self.settle_original()
        apply_overlay(self.sut / "store", self.overlay, self.ids)

    def finish(self) -> None:
        if self.barrier == "B2_LATE":
            release(self.adapter / "control", "allow-accept")
            self.settle_original()
        elif self.barrier == "B3_LATE":
            self.settle_original()
        self.stop()

    def run_crash_send(self, crash_at: str) -> int:
        """Run the send path with all barriers auto-released and a crash hook (scope 'first')."""
        self.start_endpoint()
        release(self.adapter / "control", "allow-accept")
        threading.Thread(target=self._auto_release, daemon=True).start()
        sut = self.start_send({"ECR_TEST_CRASH_AT": crash_at, "ECR_TEST_CRASH_SCOPE": "first"})
        code = sut.wait(timeout=60)
        # an original request that left the client is allowed to settle before recovery
        if (self.adapter / "events/B3.json").exists() or crash_at in ("after_request", "after_response"):
            try:
                self.settle_original(20)
            except TimeoutError:
                pass
        self._stop_auto.set()
        return code

    def stop(self) -> None:
        self._stop_auto.set()
        if self.endpoint is not None and self.endpoint.poll() is None:
            for n in ("allow-accept", "release-B3", "release-B4", "stop"):
                release(self.adapter / "control", n)
            try:
                self.endpoint.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.endpoint.kill()

    def score(self, truth: dict | None = None) -> dict:
        ctx = {"run_id": self.name, "arm": f"{self.agent}:{self.policy}:{self.mode}:{self.arm}",
               "truth": truth or self.truth, "logical_operation_id": self.ids["logical_operation_id"],
               "ledger": str(self.adapter / "data/effects.sqlite3"), "endpoint_events": str(self.adapter / "events"),
               "sut_copy": str(self.sut), "injection": "", "dev_mode": True}
        path = self.work / "score-context.json"
        path.write_text(json.dumps(ctx))
        out = subprocess.run([PY, "-s", "-E", str(SRC / "scorer_v2.py"), str(path)], capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            raise RuntimeError(out.stderr[-1500:])
        return json.loads(out.stdout)

    def authority_records(self, sub: str) -> list[dict]:
        folder = self.sut / "store/authority" / sub
        return [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))] if folder.is_dir() else []

    def recovery_actions(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted((self.sut / "store/recovery").glob("*.json"))]


def run_cell(work: Path, name: str, **kw) -> tuple[Cell, dict, int]:
    cell = Cell(work, name, **kw)
    try:
        cell.run_to_barrier()
        code = cell.recover()
        cell.finish()
        return cell, cell.score(), code
    finally:
        cell.stop()
