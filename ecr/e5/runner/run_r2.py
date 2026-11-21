"""Phase-3 root supervisor for the asynchronous simulation (async_sim_r2). No automatic retries.

Port of async_sim_r1/run.py (three OS identities ecr-sut / ecr-adapter / ecr-scorer, barrier
provenance receipts, SIGKILL injection receipts, in-flight fate, overlays) with the phase-3
additions:
  * unit and attempt identifiers (p3lib); every attempt gets new, exclusively created
    directories, endpoint, ledger and store; nothing is deleted, overwritten or re-used;
  * a global attempt index (BATCH_PARENT/attempt-index) created with O_EXCL, so attempt numbers
    are global per unit across batches and continuation batches;
  * the agents' `node` is the planner-call recorder (tools/planner_recorder.py); the recorder's
    per-call records are checked against the agent's PLAN records (tools/planner_records.py);
  * planner route J (pre-stripped .js, src/plan_js.mjs) or T (frozen plan.mjs), fixed by the
    execution manifest; the strip manifest and every generated file hash are verified at start;
  * executors v1 (frozen agent_v1.py, EBRQ / CONV) and v2 (agent_v2.py, EBRQ2, R2A / R2B);
    overlay F7_LOCAL_FAILURE_AFTER_DISPATCH; recovery-time crash cells of P3-C (crash hook in the
    first recovery process, a second recovery follows);
  * scorer_v2 (primary) and the historical scorer (secondary) both run as ecr-scorer;
  * attempt classification (p3lib.evaluate_attempt) and the batch stops at the first attempt that
    is not OBSERVED_PENDING_REPLAY; replay (same runtime here, cross runtime on the host) decides
    VALID_COMPLETED later (tools/finalize_states.py);
  * a failed attempt is still scored on whatever evidence is readable, so effects that already
    happened are recorded, never dropped.
P3-C first-dispatch crash cells (crash hook in the send process, no SIGKILL receipt) are refused:
both scorers require a SIGKILL injection receipt, which those cells cannot have (see plan v2).

Subcommands (all require sudo inside the dedicated guest; see guard()):
  run    --set qualification|P3A|P3C --batch-id ID [--continuation AUTH.json]
  replay --batch-id ID        same-runtime replay of every recorded planner call of the batch
"""
from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
import platform
import pwd
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RUNNER = Path(__file__).resolve().parent
HERE = RUNNER.parent
sys.path.insert(0, str(HERE / "src"))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, str(RUNNER))
from protocol import PHASES, digest, durable_json, wait_file  # noqa: E402
import controls  # noqa: E402
import p3lib  # noqa: E402
import planner_records  # noqa: E402

BASE = Path("/var/lib/ecr-followup-r1")
BATCH_PARENT = BASE / "async-sim-r2"
NODE = Path("/opt/ecr-followup-r1-runtime/node-v24.21.0/bin/node")
PUBLIC_PARENT = Path("/opt/ecr-followup-r1-async-r2")
IDENTITIES = {"ecr-sut": [999, 987, [987]], "ecr-adapter": [996, 986, [986]], "ecr-scorer": [995, 985, [985, 988]]}
BARRIER_INDEX = {"B1": 1, "B2_DROP": 2, "B2_LATE": 2, "B2_ARRIVE": 2, "B3_LATE": 3, "B4": 4, "B5": 5, "B6": 6}
AGENT = {"v1": "src/agent_v1.py", "CONV": "src/agent_v1.py", "v2": "src/agent_v2.py"}
BRIDGE = {"J": ("src/plan_js.mjs", "arms_js"), "T": ("src/plan.mjs", "arms")}
RECOVERY_TIMEOUT_S = 60
NODE_STAT = None  # "<size>:<mtime_ns>" of NODE, taken right after guard() verified the full hash


class IntegrityError(RuntimeError):
    """Evidence inconsistent: provenance, identity, hash or record mismatch."""


class InfraError(RuntimeError):
    """Infrastructure did not complete: timeout, process failure, endpoint error."""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(name: str) -> tuple[int, int, list[int]]:
    account = pwd.getpwnam(name)
    return account.pw_uid, account.pw_gid, os.getgrouplist(name, account.pw_gid)


def gid(name: str) -> int:
    return 0 if name == "root" else grp.getgrnam(name).gr_gid


def uid(name: str) -> int:
    return 0 if name == "root" else pwd.getpwnam(name).pw_uid


def directory(path: Path, user: str, group: str, mode: int) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.mkdir()
    os.chown(path, uid(user), gid(group))
    os.chmod(path, mode)


def root_json(path: Path, value: object, group: str = "ecr-scorer") -> None:
    durable_json(path, value)
    os.chown(path, 0, gid(group))


def owned_json(path: Path, value: object, user: str, group: str, replace: bool = False) -> None:
    durable_json(path, value, replace=replace)
    os.chown(path, uid(user), gid(group))
    os.chmod(path, 0o640)


def release(control: Path, name: str) -> None:
    target = control / name
    if not target.exists():
        durable_json(target, {"released_utc_ns": time.time_ns()})
        os.chmod(target, 0o644)


def start(module: str, args: list[str], role: str, cwd: Path, output: Path, env_extra: dict | None = None) -> subprocess.Popen:
    user_id, group_id, groups = identity(role)
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": pwd.getpwnam(role).pw_dir, "PYTHONUNBUFFERED": "1",
           "PYTHONDONTWRITEBYTECODE": "1", **(env_extra or {})}
    with open(output.with_suffix(".stdout"), "xb") as out, open(output.with_suffix(".stderr"), "xb") as err:
        return subprocess.Popen([sys.executable, "-s", "-E", module, *args], user=user_id, group=group_id,
                                extra_groups=groups, cwd=cwd, env=env, stdout=out, stderr=err, start_new_session=True)


def request(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
        return json.loads(response.read())


# ------------------------------------------------------------------------------------ guard / deploy
def guard(manifest: dict) -> None:
    if os.geteuid() != 0 or platform.system() != "Linux":
        raise RuntimeError("Run with sudo in the dedicated Ubuntu guest only")
    if Path("/sys/class/dmi/id/product_uuid").read_text().strip().lower() not in manifest["vm_uuids"]:
        raise RuntimeError("Dedicated VM identity mismatch")
    if subprocess.check_output(["findmnt", "-n", "-o", "FSTYPE", "-T", str(BASE)], text=True).strip() != "ext4":
        raise RuntimeError("Native ext4 required")
    if platform.python_version() != manifest["python_version"] or platform.release() != manifest["kernel"]:
        raise RuntimeError("Guest Python/kernel profile drifted")
    if sha(NODE) != manifest["node_binary_sha256"]:
        raise RuntimeError("Node identity drift")
    owner = int(os.environ.get("SUDO_UID", "0"))
    if not owner or HERE.stat().st_uid != owner or HERE.stat().st_mode & 0o7777 != 0o700:
        raise RuntimeError("Private package directory must be administrator owned mode 0700; use sudo")
    for ancestor in (HERE, *HERE.parents):
        if ancestor.is_symlink() or ancestor.stat().st_mode & 0o022:
            raise RuntimeError("Unsafe private ancestor " + str(ancestor))
    for name, expected in IDENTITIES.items():
        user_id, group_id, groups = identity(name)
        if [user_id, group_id, sorted(groups)] != expected:
            raise RuntimeError("Service identity drift " + name)
    for rel, expected in manifest["files"].items():
        path = HERE / rel
        if Path(rel).is_absolute() or ".." in Path(rel).parts or path.is_symlink() or sha(path) != expected:
            raise RuntimeError("Package file drift " + rel)
    if manifest["planner_route"] not in BRIDGE:
        raise RuntimeError("Unknown planner route")
    if manifest["planner_route"] == "J":
        strip = json.loads((HERE / "arms_js/strip-manifest.json").read_text())
        if strip.get("status") != "COMPLETED" or strip.get("node_binary_sha256") != manifest["node_binary_sha256"]:
            raise RuntimeError("JS artifacts were not generated by the pinned Node, or generation failed")
        if strip.get("options") != {"mode": "strip"} or not strip.get("arms"):
            raise RuntimeError("Unexpected strip options or empty strip manifest")
        for arm, entry in strip["arms"].items():
            for name, digest_ in entry["inputs"].items():
                if sha(HERE / "arms" / arm / name) != digest_:
                    raise RuntimeError("TS source drift " + arm + "/" + name)
            for name, digest_ in entry["outputs"].items():
                if sha(HERE / "arms_js" / arm / name) != digest_:
                    raise RuntimeError("JS artifact drift " + arm + "/" + name)
    for path, owner_name, group_name, mode in ((BASE, "root", "root", 0o755),
                                               (BASE / "ledger", "ecr-adapter", "ecr-ledger-readers", 0o750),
                                               (BASE / "sut-state", "ecr-sut", "ecr-sut", 0o750)):
        info = path.lstat()
        if path.is_symlink() or (info.st_uid, info.st_gid, info.st_mode & 0o7777) != (uid(owner_name), gid(group_name), mode):
            raise RuntimeError("Isolation permission drift " + str(path))


def deploy(batch_id: str, manifest: dict) -> Path:
    if not PUBLIC_PARENT.exists():
        directory(PUBLIC_PARENT, "root", "root", 0o755)
    info = PUBLIC_PARENT.lstat()
    if PUBLIC_PARENT.is_symlink() or (info.st_uid, info.st_gid, info.st_mode & 0o7777) != (0, 0, 0o755):
        raise RuntimeError("Unsafe public parent")
    public = PUBLIC_PARENT / batch_id
    directory(public, "root", "root", 0o755)
    for rel in manifest["public_files"]:
        target = public / rel
        for parent in reversed(Path(rel).parents[:-1]):
            if not (public / parent).exists():
                directory(public / parent, "root", "root", 0o755)
        shutil.copyfile(HERE / rel, target)
        os.chown(target, 0, 0)
        os.chmod(target, 0o755 if rel == "tools/planner_recorder.py" else 0o644)
        if sha(target) != manifest["files"][rel]:
            raise RuntimeError("Deployed file differs from manifest " + rel)
    # the execution manifest itself is deployed read-only: it is the code identity the planner-call
    # recorder checks every executed file against (candidate v4)
    target = public / "execution-manifest-r2.json"
    shutil.copyfile(HERE / "execution-manifest-r2.json", target)
    os.chown(target, 0, 0)
    os.chmod(target, 0o644)
    if sha(target) != sha(HERE / "execution-manifest-r2.json"):
        raise RuntimeError("Deployed execution manifest differs")
    return public


# ------------------------------------------------------------------------------------ scenario mechanics
def settle_original(adapter_root: Path, seconds: float = 40) -> dict:
    deadline = time.monotonic() + seconds
    events, control = adapter_root / "events", adapter_root / "control"
    while not (events / "original-done.json").is_file():
        for index in (3, 4):
            if (events / f"B{index}-ready.json").is_file():
                release(control, f"release-B{index}")
        if time.monotonic() > deadline:
            raise InfraError("original request did not settle")
        time.sleep(0.01)
    return json.loads((events / "original-done.json").read_text())


def apply_overlay(store: Path, overlay: str | None, cell_ids: dict) -> dict | None:
    """r1 overlays F2-F6 unchanged; F7 writes a local definite failure after a valid dispatch."""
    if not overlay:
        return None
    journal = store / "journal"
    base = cell_ids["identity"]
    outcome_base = {**base, "eventType": "AdapterOutcomeObserved", "queueItemId": cell_ids["queue_item_id"]}
    if overlay == "F2_FOREIGN_ACCEPT":
        event = {**outcome_base, "attemptId": base["attemptId"] + "-stale", "artifactId": base["artifactId"] + "-v0",
                 "eventId": base["attemptId"] + "-stale-e5", "journalSequence": 5,
                 "outcome": "adapter_accepted", "evidenceLevel": "adapter_accepted"}
        owned_json(journal / "0005-AdapterOutcomeObserved.json", event, "ecr-sut", "ecr-sut")
    elif overlay == "F3_WEAK_ACK":
        event = {**outcome_base, "eventId": base["attemptId"] + "-e5", "journalSequence": 5,
                 "outcome": "adapter_accepted", "evidenceLevel": "transport_ack"}
        owned_json(journal / "0005-AdapterOutcomeObserved.json", event, "ecr-sut", "ecr-sut")
    elif overlay == "F4_REORDERED_CALLBACK":
        event = {**outcome_base, "eventId": base["attemptId"] + "-e5", "journalSequence": 3,
                 "outcome": "adapter_accepted", "evidenceLevel": "adapter_accepted"}
        owned_json(journal / "0005-AdapterOutcomeObserved.json", event, "ecr-sut", "ecr-sut")
    elif overlay == "F5_SUPERSEDED_VERSION":
        task = json.loads((store / "task.json").read_text())
        owned_json(store / "task.json", {**task, "taskStateVersion": task["taskStateVersion"] + 1}, "ecr-sut", "ecr-sut", replace=True)
    elif overlay == "F6_LOST_DISPATCH_FAILURE":
        lost = journal / "0004-DispatchBoundaryCrossed.json"
        retained = lost.read_bytes()
        lost.unlink()
        event = {**outcome_base, "eventId": base["attemptId"] + "-e5", "journalSequence": 5,
                 "outcome": "definite_failure", "evidenceLevel": "adapter_rejected"}
        owned_json(journal / "0005-AdapterOutcomeObserved.json", event, "ecr-sut", "ecr-sut")
        return {"overlay": overlay, "removed": lost.name, "removed_sha256": hashlib.sha256(retained).hexdigest()}
    elif overlay == "F7_LOCAL_FAILURE_AFTER_DISPATCH":
        dbc = journal / "0004-DispatchBoundaryCrossed.json"
        if not dbc.is_file() or (journal / "0005-AdapterOutcomeObserved.json").exists():
            raise IntegrityError("F7 requires a valid dispatch event and no outcome event")
        event = {**outcome_base, "eventId": base["attemptId"] + "-e5", "journalSequence": 5,
                 "outcome": "definite_failure", "evidenceLevel": "adapter_rejected"}
        owned_json(journal / "0005-AdapterOutcomeObserved.json", event, "ecr-sut", "ecr-sut")
        return {"overlay": overlay, "dispatch_event_sha256": sha(dbc)}
    else:
        raise ValueError("unknown overlay " + overlay)
    return {"overlay": overlay}


def copy_tree(source: Path, target: Path) -> None:
    directory(target, "root", "ecr-scorer", 0o750)
    for item in sorted(source.iterdir()):
        if item.is_symlink():
            raise IntegrityError("Symlink in copied state " + str(item))
        if item.is_dir():
            copy_tree(item, target / item.name)
        else:
            shutil.copyfile(item, target / item.name)
            os.chown(target / item.name, 0, gid("ecr-scorer"))
            os.chmod(target / item.name, 0o640)


def score(public: Path, scorer: str, context: dict, folder: Path, label: str) -> dict:
    path = folder / f"{label}-context.json"
    root_json(path, context)
    process = start(str(public / "src" / scorer), [str(path)], "ecr-scorer", public, folder / label)
    try:
        code = process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        raise InfraError("Scorer timeout " + label)
    if code != 0 or (folder / f"{label}.stderr").read_bytes():
        raise InfraError("Scorer process failure " + label + ": " + (folder / f"{label}.stderr").read_text()[-500:])
    result = json.loads((folder / f"{label}.stdout").read_text())
    if result["scorer_uid"] != identity("ecr-scorer")[0] or result["scorer_pid"] != process.pid:
        raise IntegrityError("Scorer identity mismatch " + label)
    root_json(folder / f"{label}-score.json", result)
    return result


# ------------------------------------------------------------------------------------ one attempt
def run_attempt(public: Path, manifest: dict, unit: dict, attempt: dict, batch: Path, roots: dict) -> dict:
    """Run one attempt of one unit. Never raises for experiment-level failures; returns the facts."""
    label = p3lib.attempt_dirname(attempt["attempt_id"])
    scenario, truth = unit["scenario"], unit["truth"]
    folder = batch / "attempts" / label
    directory(folder, "root", "ecr-scorer", 0o750)
    root_json(folder / "attempt-start.json", attempt)
    sut_root, adapter_root = roots["sut"] / label, roots["adapter"] / label
    directory(sut_root, "root", "ecr-sut", 0o750)
    directory(sut_root / "events", "ecr-sut", "ecr-sut", 0o750)
    directory(sut_root / "control", "root", "ecr-sut", 0o750)
    for name in ("store", "store/journal", "store/recovery", "planner-calls"):
        directory(sut_root / name, "ecr-sut", "ecr-sut", 0o750)
    directory(adapter_root, "ecr-adapter", "ecr-ledger-readers", 0o750)
    directory(adapter_root / "events", "ecr-adapter", "ecr-ledger-readers", 0o2750)
    directory(adapter_root / "control", "root", "ecr-adapter", 0o750)
    directory(adapter_root / "data", "ecr-adapter", "ecr-ledger-readers", 0o2750)
    # clean initial state: the directories above were just created exclusively; no file may exist yet
    present = sorted(str(p) for r in (sut_root, adapter_root) for p in r.rglob("*") if not p.is_dir())
    root_json(folder / "initial-state.json", {"files_present": present, "sut_root": str(sut_root), "adapter_root": str(adapter_root)})
    if present:
        raise RuntimeError("attempt directories not empty at start")
    ids = unit["ids"]
    db = adapter_root / "data/effects.sqlite3"
    executor, mode = unit["executor"], unit["r2_mode"]
    facts = {"attempt_id": attempt["attempt_id"], "unit_id": unit["unit_id"], "label": label,
             "harness_integrity_ok": True, "recovery_exit": None, "final_state_present": False,
             "planner_records": "INCOMPLETE", "planner_failures": 0, "infra_events": [], "scorer_faults": [],
             "replay": "PENDING", "sut_started": False, "errors": []}
    endpoint_config = {"run_id": label, "profile": scenario["profile"], "db": str(db),
                       "gated_transport": ids["transport_request_id"],
                       "events": str(adapter_root / "events"), "control": str(adapter_root / "control")}
    root_json(adapter_root / "configuration.json", endpoint_config, "ecr-adapter")
    endpoint = start(str(public / "src/endpoint.py"), [str(adapter_root / "configuration.json")], "ecr-adapter",
                     public / "src", folder / "endpoint")
    processes = {}
    scored = False
    try:
        wait_file(adapter_root / "events/ready.json", 15)
        ready = json.loads((adapter_root / "events/ready.json").read_text())
        if ready["pid"] != endpoint.pid or ready["uid"] != identity("ecr-adapter")[0]:
            raise IntegrityError("Endpoint process identity mismatch")
        if request(ready["port"], "/health")["run_id"] != label:
            raise IntegrityError("Endpoint identity mismatch")
        common = {"run_id": label, "store": str(sut_root / "store"), "events": str(sut_root / "events"),
                  "control": str(sut_root / "control"), "port": ready["port"], "ledger": str(db),
                  "identity": ids["identity"], "queue_item_id": ids["queue_item_id"],
                  "logical_operation_id": ids["logical_operation_id"], "attempt_id": ids["identity"]["attemptId"],
                  "transport_request_id": ids["transport_request_id"]}
        if executor == "v2":
            common.update(endpoint_contract=unit["endpoint_contract"], r2_mode=mode)
        root_json(sut_root / "send-configuration.json", common, "ecr-sut")
        agent = str(public / AGENT[executor])
        sut = processes["agent_send"] = start(agent, ["send", str(sut_root / "send-configuration.json")], "ecr-sut",
                                              public / "src", folder / "agent-send")
        facts["sut_started"] = True
        target = BARRIER_INDEX[scenario["barrier"]]
        for index in range(1, target + 1):
            actor_root, process, actor = (adapter_root, endpoint, "ecr-adapter") if index in (3, 4) else (sut_root, sut, "ecr-sut")
            try:
                wait_file(actor_root / f"events/B{index}-ready.json", 25)
            except TimeoutError as error:
                raise InfraError(f"barrier B{index} not reached: {error}")
            receipt = json.loads((actor_root / f"events/B{index}.json").read_text())
            marker = json.loads((actor_root / f"events/B{index}-ready.json").read_text())
            expected_hash = digest({k: v for k, v in receipt.items() if k != "receipt_sha256"})
            if (receipt["receipt_sha256"] != expected_hash or marker["barrier_sha256"] != expected_hash
                    or receipt["run_id"] != label or receipt["actor_pid"] != process.pid
                    or receipt["actor_euid"] != identity(actor)[0] or receipt["durable_sequence"] != index
                    or receipt["phase"] != PHASES[index - 1] or sut.poll() is not None):
                raise IntegrityError("Barrier provenance mismatch B" + str(index))
            root_json(folder / f"B{index}-observed.json", receipt)
            if index == target:
                process_uid = int(Path(f"/proc/{sut.pid}/status").read_text().split("Uid:\t", 1)[1].split()[1])
                if process_uid != identity("ecr-sut")[0] or sut.poll() is not None:
                    raise IntegrityError("Injection target is not the live agent")
                injection = {"run_id": label, "barrier_id": f"B{index}", "phase": PHASES[index - 1],
                             "basis_receipt_sha256": expected_hash, "durable_sequence": index,
                             "durable_timestamp_utc_ns": time.time_ns(), "injection_action": "SIGKILL",
                             "durable_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                             "target_pid": sut.pid, "target_uid": process_uid}
                root_json(folder / "injection-intent.json", injection)
                os.kill(sut.pid, signal.SIGKILL)
                injection["exit_status"] = sut.wait(timeout=10)
                if injection["exit_status"] != -signal.SIGKILL:
                    raise IntegrityError("Actual agent exit is not SIGKILL")
                injection["receipt_sha256"] = digest(injection)
                root_json(folder / "injection-receipt.json", injection)
            else:
                release(actor_root / "control", f"release-B{index}")
                if index == 2:
                    release(adapter_root / "control", "allow-accept")
        barrier_name = scenario["barrier"]
        fate_before = None
        if barrier_name == "B2_DROP":
            release(adapter_root / "control", "drop-request")
            fate_before = settle_original(adapter_root)
        elif barrier_name == "B2_ARRIVE":
            release(adapter_root / "control", "allow-accept")
            fate_before = settle_original(adapter_root)
        elif barrier_name == "B4":
            fate_before = settle_original(adapter_root)
        elif target >= 5:
            fate_before = json.loads((adapter_root / "events/original-done.json").read_text())
        root_json(folder / "original-before-recovery.json", {"fate": fate_before})
        overlay = apply_overlay(sut_root / "store", scenario.get("overlay"), ids)
        root_json(folder / "overlay.json", {"applied": overlay})
        bridge, arms_root = BRIDGE[manifest["planner_route"]]
        recover_config = {**common, "policy": unit["policy"], "endpoint_contract": unit["endpoint_contract"],
                          "node": str(public / "tools/planner_recorder.py"), "plan_bridge": str(public / bridge),
                          "arm_dir": str(public / arms_root / unit["classifier_arm"]) if executor != "CONV" else ""}
        root_json(sut_root / "recover-configuration.json", recover_config, "ecr-sut")
        recorder_env = {"ECR_REAL_NODE": str(NODE), "ECR_NODE_SHA256": manifest["node_binary_sha256"],
                        "ECR_PLANNER_RECORD_DIR": str(sut_root / "planner-calls"), "ECR_UNIT_ID": unit["unit_id"],
                        "ECR_ATTEMPT_ID": attempt["attempt_id"], "ECR_CODE_IDENTITY": attempt["manifest_sha256"],
                        "ECR_CODE_MANIFEST": str(public / "execution-manifest-r2.json"), "ECR_CODE_ROOT": str(public),
                        "ECR_NODE_STAT": NODE_STAT, "ECR_PLANNER_ROUTE": manifest["planner_route"]}
        if manifest["planner_route"] == "J":
            recorder_env.update(ECR_JS_MANIFEST=str(public / "arms_js/strip-manifest.json"),
                                ECR_JS_IDENTITY=sha(public / "arms_js/strip-manifest.json"))
        recoveries = []
        if scenario.get("crash_scope") == "recovery":
            # P3-C recovery-time crash: the first recovery must die at the hook, a second recovery follows
            crash_env = {**recorder_env, "ECR_TEST_CRASH_AT": scenario["crash_at"], "ECR_TEST_CRASH_SCOPE": "recovery"}
            recoveries.append(("agent-recover-1", crash_env, 137))
            recoveries.append(("agent-recover-2", recorder_env, None))
        else:
            recoveries.append(("agent-recover", recorder_env, None))
        for name, env, expected in recoveries:
            proc = processes[name] = start(agent, ["recover", str(sut_root / "recover-configuration.json")],
                                           "ecr-sut", public / "src", folder / name, env)
            try:
                code = proc.wait(timeout=RECOVERY_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                facts["infra_events"].append(f"{name} timeout after {RECOVERY_TIMEOUT_S}s")
                code = None
            facts.setdefault("recovery_exits", {})[name] = code
            if expected is not None:
                if code != expected:
                    raise IntegrityError(f"{name}: crash hook did not fire (exit {code})")
            else:
                facts["recovery_exit"] = code
        fate_after = None
        if barrier_name == "B2_LATE":
            release(adapter_root / "control", "allow-accept")
            fate_after = settle_original(adapter_root)
        elif barrier_name == "B3_LATE":
            fate_after = settle_original(adapter_root)
        root_json(folder / "original-after-recovery.json", {"fate": fate_after})
        release(adapter_root / "control", "stop")
        try:
            endpoint_code = endpoint.wait(timeout=15)
        except subprocess.TimeoutExpired:
            raise InfraError("endpoint did not stop")
        if endpoint_code != 0 or list((adapter_root / "events").glob("error-*.json")):
            facts["infra_events"].append(f"endpoint error or nonzero exit ({endpoint_code})")
        facts.update(score_attempt(public, unit, label, folder, sut_root, adapter_root, ids, db))
        scored = True
    except IntegrityError as error:
        facts["harness_integrity_ok"] = False
        facts["errors"].append(f"IntegrityError: {error}")
    except InfraError as error:
        facts["infra_events"].append(f"InfraError: {error}")
    except Exception as error:  # unknown cause: evidence cannot be trusted until investigated
        facts["harness_integrity_ok"] = False
        facts["errors"].append(f"{type(error).__name__}: {error}")
    finally:
        cleanup = {}
        for name, process in processes.items():
            if process is not None and process.poll() is None:
                process.kill()
                cleanup[name + "_killed"] = process.wait(timeout=5)
        if endpoint.poll() is None:
            for name in ("allow-accept", "release-B3", "release-B4", "stop"):
                release(adapter_root / "control", name)
            try:
                cleanup["endpoint_exit_status"] = endpoint.wait(timeout=15)
            except subprocess.TimeoutExpired:
                endpoint.kill()
                cleanup["endpoint_residue_killed"] = endpoint.wait(timeout=5)
        facts["cleanup"] = cleanup
    if not scored and facts["sut_started"]:
        # failed attempt: still score the readable evidence so that effects which happened are recorded
        # (its scorer faults, e.g. MISSING_FINAL_STATE, describe the failed run and are not integrity faults;
        #  its planner-record check is the real record check of this attempt)
        try:
            observation = score_attempt(public, unit, label, folder, sut_root, adapter_root, ids, db, suffix="-after-failure")
            facts["failed_attempt_observation"] = observation
            for key in ("planner_records", "planner_record_reasons", "planner_failures", "planner_calls", "agent_plan_records"):
                facts[key] = observation[key]
        except Exception as error:
            facts["failed_attempt_observation"] = {"error": f"{type(error).__name__}: {error}"}
    state, reasons = p3lib.evaluate_attempt(facts)
    facts.update(state=state, state_reasons=reasons)
    root_json(folder / "attempt-result.json", facts)
    return facts


def score_attempt(public, unit, label, folder, sut_root, adapter_root, ids, db, suffix="") -> dict:
    copy_tree(sut_root, folder / f"sut-copy{suffix}")
    copy_tree(sut_root / "planner-calls", folder / f"planner-calls{suffix}")
    actions = [json.loads(p.read_text()) for p in sorted((folder / f"sut-copy{suffix}/store/recovery").glob("*.json"))]
    plan_records = sum(a.get("action") == "PLAN" for a in actions)
    records = planner_records.check(folder / f"planner-calls{suffix}", plan_records)
    root_json(folder / f"planner-records-check{suffix}.json", records)
    context = {"run_id": label, "arm": unit["unit_id"],
               "truth": unit["truth"], "logical_operation_id": ids["logical_operation_id"],
               "ledger": str(db), "endpoint_events": str(adapter_root / "events"),
               "sut_copy": str(folder / f"sut-copy{suffix}"), "injection": str(folder / "injection-receipt.json")}
    primary = score(public, "scorer_v2.py", context, folder, f"primary-scorer_v2{suffix}")
    secondary = score(public, "scorer.py", context, folder, f"secondary-scorer_r1{suffix}")
    faults = [f"primary:{f}" for f in primary["faults"]]
    expected_secondary = set(unit.get("secondary_faults_not_gating", []))
    faults += [f"secondary:{f}" for f in secondary["faults"] if f not in expected_secondary]
    return {"final_state_present": (folder / f"sut-copy{suffix}/store/final.json").is_file(),
            "planner_records": records["status"], "planner_record_reasons": records["reasons"],
            "planner_failures": len(records["failures"]), "planner_calls": len(records["calls"]),
            "agent_plan_records": plan_records, "scorer_faults": faults,
            "secondary_faults_recorded_not_gating": sorted(set(secondary["faults"]) & expected_secondary),
            "primary_score": primary, "secondary_score": secondary}


# ------------------------------------------------------------------------------------ scorer controls
def chown_tree(path: Path) -> None:
    for p in [path, *path.rglob("*")]:
        os.chown(p, 0, gid("ecr-scorer"))
        os.chmod(p, 0o750 if p.is_dir() else 0o640)


def negative_controls(public: Path, batch: Path, rows: list[dict]) -> dict:
    """Planted-fixture scorer controls (runner/controls.py) on copies of the evidence of the first
    qualification attempt whose primary and secondary scores are a true completion. Each control is
    scored by a separate scorer process as ecr-scorer. Originals are never modified."""
    target = None
    for r in rows:
        if r.get("run_in_batch") and r.get("state") == "OBSERVED_PENDING_REPLAY":
            folder = batch / "attempts" / p3lib.attempt_dirname(r["attempt_id"])
            facts = json.loads((folder / "attempt-result.json").read_text())
            if facts["primary_score"]["true_completion"] and facts["secondary_score"]["true_completion"]:
                target = (folder, facts)
                break
    if target is None:
        return {"status": "NOT_RUN_NO_ELIGIBLE_ATTEMPT"}
    folder, facts = target
    ctx_v2 = json.loads((folder / "primary-scorer_v2-context.json").read_text())
    ctx_r1 = json.loads((folder / "secondary-scorer_r1-context.json").read_text())
    out = folder / "negative-controls"
    planted = controls.plant(ctx_v2, out)
    chown_tree(out)
    scores = {name: score(public, scorer, ctx, out, name) for name, scorer, ctx in controls.contexts(ctx_v2, ctx_r1, planted)}
    result = {"attempt_id": facts["attempt_id"], **controls.judge(facts["primary_score"], scores)}
    root_json(out / "controls.json", result)
    return result


# ------------------------------------------------------------------------------------ attempt index
def index_dir() -> Path:
    path = BATCH_PARENT / "attempt-index"
    if not path.exists():
        directory(path, "root", "ecr-scorer", 0o750)
    return path


def attempts_of(unit_id: str, finalized: dict | None = None) -> list[dict]:
    """All attempts of a unit across batches, in attempt order, with their recorded state.
    finalized: {attempt_id: state} from tools/finalize_states.py (replay outcome); it may only turn
    OBSERVED_PENDING_REPLAY into VALID_COMPLETED or INTEGRITY_UNKNOWN."""
    rows = []
    n = 1
    while True:
        aid = p3lib.attempt_id(unit_id, n)
        start_path = index_dir() / (p3lib.attempt_dirname(aid) + ".json")
        if not start_path.exists():
            return rows
        entry = json.loads(start_path.read_text())
        result = BATCH_PARENT / entry["batch_id"] / "attempts" / p3lib.attempt_dirname(aid) / "attempt-result.json"
        # an attempt that started but has no result was interrupted: its evidence cannot be trusted
        entry["state"] = json.loads(result.read_text())["state"] if result.is_file() else "INTEGRITY_UNKNOWN"
        final = (finalized or {}).get(aid)
        if final is not None:
            if entry["state"] != "OBSERVED_PENDING_REPLAY" or final not in ("VALID_COMPLETED", "INTEGRITY_UNKNOWN"):
                raise RuntimeError(f"finalized state {final} not applicable to {aid} ({entry['state']})")
            entry["state"] = final
        rows.append(entry)
        n += 1


def claim_attempt(unit_id: str, n: int, batch_id: str, manifest_sha: str, retry_of: str | None, reason: str) -> dict:
    aid = p3lib.attempt_id(unit_id, n)
    entry = {"attempt_id": aid, "unit_id": unit_id, "attempt_number": n, "batch_id": batch_id,
             "manifest_sha256": manifest_sha, "retry_of": retry_of, "retry_reason": reason, "utc_ns": time.time_ns()}
    root_json(index_dir() / (p3lib.attempt_dirname(aid) + ".json"), entry)  # O_EXCL: never reused
    return entry


# ------------------------------------------------------------------------------------ subcommands
def cmd_run(args, manifest: dict, manifest_sha: str) -> int:
    cells = json.loads((HERE / "private/cells_p3.json").read_text())
    if sha(HERE / "private/cells_p3.json") != manifest["files"]["private/cells_p3.json"]:
        raise RuntimeError("cell table drift")
    if args.set == "qualification":
        wanted = cells["qualification_unit_ids"]
        units_by_id = {u["unit_id"]: u for u in cells["units"]}
        units = [units_by_id[u] for u in wanted]
    else:
        authorization = json.loads((HERE / "qualification-authorization.json").read_text())
        if authorization.get("status") != "PASS_HOST_AUDIT" or authorization.get("manifest_sha256") != manifest_sha:
            raise RuntimeError("Host qualification authorization missing or for another manifest")
        units = [u for u in cells["units"] if u["family"] == args.set]
    blocked = [u["unit_id"] for u in units if u.get("group") == "crash_first"]
    if blocked:
        raise RuntimeError(f"{len(blocked)} first-dispatch crash cells are not runnable: scorers require a SIGKILL receipt")
    extra_allowed, finalized = set(), {}
    if args.continuation:
        cont = json.loads(Path(args.continuation).read_text())
        if cont.get("manifest_sha256") != manifest_sha or cont.get("approved") is not True:
            raise RuntimeError("Continuation authorization missing approval or for another manifest")
        extra_allowed = set(cont.get("extra_attempt_unit_ids", []))
        finalized = cont.get("finalized_attempt_states", {})
    if not BATCH_PARENT.exists():
        directory(BATCH_PARENT, "root", "ecr-scorer", 0o750)
    batch = BATCH_PARENT / args.batch_id
    directory(batch, "root", "ecr-scorer", 0o750)
    directory(batch / "attempts", "root", "ecr-scorer", 0o750)
    public = deploy(args.batch_id, manifest)
    roots = {"sut": BASE / "sut-state" / ("r2-" + args.batch_id), "adapter": BASE / "ledger" / ("r2-" + args.batch_id)}
    directory(roots["sut"], "root", "ecr-sut", 0o750)
    directory(roots["adapter"], "ecr-adapter", "ecr-ledger-readers", 0o750)
    root_json(batch / "execution-manifest.json", manifest)
    root_json(batch / "environment.json", {"python": sys.version, "kernel": platform.release(),
              "sqlite": sqlite3.sqlite_version, "node_sha256": sha(NODE), "set": args.set,
              "continuation": args.continuation, "manifest_sha256": manifest_sha})
    rows, status = [], "COMPLETED_PENDING_REPLAY"
    for unit in units:
        history = attempts_of(unit["unit_id"], finalized)
        states = [h["state"] for h in history]
        allowed, why = p3lib.may_add_attempt(states, unit["unit_id"] in extra_allowed)
        if not allowed:
            rows.append({"unit_id": unit["unit_id"], "run_in_batch": False, "reason": why, "unit_state": p3lib.unit_state(states)})
            continue
        retry_of = history[-1]["attempt_id"] if history else None
        attempt = claim_attempt(unit["unit_id"], len(history) + 1, args.batch_id, manifest_sha, retry_of, why)
        try:
            facts = run_attempt(public, manifest, unit, attempt, batch, roots)
        except Exception as error:  # harness aborted the attempt: keep everything, classify as unknown
            facts = {"state": "INTEGRITY_UNKNOWN", "state_reasons": [f"runner aborted attempt: {type(error).__name__}: {error}"]}
            result = batch / "attempts" / p3lib.attempt_dirname(attempt["attempt_id"]) / "attempt-result.json"
            if result.parent.is_dir() and not result.exists():
                root_json(result, {**attempt, **facts})
        rows.append({"unit_id": unit["unit_id"], "run_in_batch": True, "attempt_id": attempt["attempt_id"],
                     "state": facts["state"], "state_reasons": facts["state_reasons"]})
        print(f"{attempt['attempt_id']}={facts['state']}", flush=True)
        if p3lib.batch_should_stop(facts["state"]):
            status = "STOPPED_INVESTIGATE"
            break
    done = {r["unit_id"] for r in rows}
    rows.extend({"unit_id": u["unit_id"], "run_in_batch": False, "reason": "batch stopped before this unit"}
                for u in units if u["unit_id"] not in done)
    controls = None
    if args.set == "qualification" and status == "COMPLETED_PENDING_REPLAY":
        controls = negative_controls(public, batch, rows)
        if controls["status"] != "PASS":
            status = "STOPPED_INVESTIGATE"
    summary = {"batch_id": args.batch_id, "set": args.set, "status": status, "units": rows,
               "attempts_started": sum(r.get("run_in_batch") is True for r in rows),
               "scorer_negative_controls": controls,
               "manifest_sha256": manifest_sha, "utc_ns": time.time_ns(),
               "note": "OBSERVED_PENDING_REPLAY attempts become VALID_COMPLETED only after same- and cross-runtime replay"}
    root_json(batch / "batch-summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "units"}), flush=True)
    return 0 if status == "COMPLETED_PENDING_REPLAY" else 1


def cmd_replay(args, manifest: dict, manifest_sha: str) -> int:
    batch = BATCH_PARENT / args.batch_id
    public = PUBLIC_PARENT / args.batch_id
    record_dirs = sorted(str(p) for p in (batch / "attempts").glob("*/planner-calls") if p.is_dir())
    folder = batch / f"replay-same-runtime-{time.strftime('%Y%m%dT%H%M%S')}"
    directory(folder, "root", "ecr-scorer", 0o750)
    if not record_dirs:
        root_json(folder / "report.json", {"status": "NO_RECORDS"})
        return 1
    directory(folder / "evidence-parent", "ecr-scorer", "ecr-scorer", 0o750)
    proc = start(str(public / "tools/replay.py"), ["--records", *record_dirs, "--node", str(NODE), "--mode", "same",
                                                   "--evidence-dir", str(folder / "evidence-parent/evidence"),
                                                   "--out", "-"], "ecr-scorer", public, folder / "replay")
    code = proc.wait(timeout=3600)
    report = json.loads((folder / "replay.stdout").read_text())
    root_json(folder / "report.json", {**report, "exit_code": code, "manifest_sha256": manifest_sha})
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}), flush=True)
    return 0 if report["status"] == "CONSISTENT" else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--set", choices=["qualification", "P3A", "P3C"], required=True)
    run.add_argument("--batch-id", required=True)
    run.add_argument("--continuation")
    rep = sub.add_parser("replay")
    rep.add_argument("--batch-id", required=True)
    args = parser.parse_args()
    manifest_path = HERE / "execution-manifest-r2.json"
    manifest = json.loads(manifest_path.read_text())
    manifest_sha = sha(manifest_path)
    guard(manifest)
    global NODE_STAT
    st = NODE.stat()
    NODE_STAT = f"{st.st_size}:{st.st_mtime_ns}"
    os.umask(0o027)
    if not args.batch_id.replace("-", "").replace("_", "").isalnum():
        raise RuntimeError("unsafe batch id")
    raise SystemExit((cmd_run if args.command == "run" else cmd_replay)(args, manifest, manifest_sha))


if __name__ == "__main__":
    main()
