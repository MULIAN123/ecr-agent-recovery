"""Root supervisor for the authorization-crash experiment (async_sim_ac1). No automatic retries.

Derived from the frozen async_sim_r2 runner (run_r2.py, 068e0d13...). Changes (CHANGES_ac1.md):
  * crash injection only by SIGKILL from this root process, at an agent pause point (src/pausepoint.py)
    or at a B barrier; raw child status recorded via waitid(WNOWAIT) (runner/ackill.py) and validated
    (src/acreceipts.py). The frozen os._exit(137) hooks no longer exist; ECR_TEST_CRASH_* is refused;
  * a pre-recovery snapshot of the agent store before every recovery, with timing validation;
  * consecutive recoveries with a pause in each but the last; injected overlays between recoveries;
  * the authorization file must name this batch id and set (the frozen runner checked status and
    manifest hash only);
  * attempt classification by runner/aclib.py (abnormal exits are observations, not stops);
  * a third, diagnostic scorer (src/scorer_diag.py) besides the unchanged primary and secondary scorers.

Subcommands (all require sudo inside the dedicated guest; see guard()):
  run    --set ACQ|AC1|ACFULL --batch-id ID [--continuation AUTH.json]
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
import aclib  # noqa: E402
import ackill  # noqa: E402
import acreceipts  # noqa: E402
import planner_records  # noqa: E402

BASE = Path("/var/lib/ecr-followup-r1")
BATCH_PARENT = BASE / "async-sim-ac1"
NODE = Path("/opt/ecr-followup-r1-runtime/node-v24.21.0/bin/node")
PUBLIC_PARENT = Path("/opt/ecr-followup-r1-async-ac1")
MANIFEST_NAME = "execution-manifest-ac1.json"
IDENTITIES = {"ecr-sut": [999, 987, [987]], "ecr-adapter": [996, 986, [986]], "ecr-scorer": [995, 985, [985, 988]]}
BARRIER_INDEX = {"B1": 1, "B2_DROP": 2, "B2_LATE": 2, "B2_ARRIVE": 2, "B3_LATE": 3, "B4": 4, "B5": 5, "B6": 6}
AGENT = {"v2": "src/agent_v2.py"}
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
    forbidden = [k for k in (env_extra or {}) if k.startswith("ECR_TEST_CRASH")]
    if forbidden:
        raise RuntimeError(f"frozen crash-hook variables are not allowed in ac1: {forbidden}")
    user_id, group_id, groups = identity(role)
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": pwd.getpwnam(role).pw_dir, "PYTHONUNBUFFERED": "1",
           "PYTHONDONTWRITEBYTECODE": "1", **(env_extra or {})}
    with open(output.with_suffix(".stdout"), "xb") as out, open(output.with_suffix(".stderr"), "xb") as err:
        return subprocess.Popen([sys.executable, "-s", "-E", "-B", module, *args], user=user_id, group=group_id,
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
    target = public / MANIFEST_NAME
    shutil.copyfile(HERE / MANIFEST_NAME, target)
    os.chown(target, 0, 0)
    os.chmod(target, 0o644)
    if sha(target) != sha(HERE / MANIFEST_NAME):
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
        err = InfraError("Scorer process failure " + label + f" (exit {code}): " + (folder / f"{label}.stderr").read_text()[-500:])
        err.exit_code = code
        raise err
    result = json.loads((folder / f"{label}.stdout").read_text())
    if result["scorer_uid"] != identity("ecr-scorer")[0] or result["scorer_pid"] != process.pid:
        raise IntegrityError("Scorer identity mismatch " + label)
    root_json(folder / f"{label}-score.json", result)
    return result


# ------------------------------------------------------------------------------------ ac1: pause/kill helpers
def pause_env(point: str, scope: str, label: str, sut_root: Path, run_id: str) -> dict:
    return {"ECR_PAUSE_AT": point, "ECR_PAUSE_SCOPE": scope, "ECR_PAUSE_LABEL": label,
            "ECR_PAUSE_EVENTS": str(sut_root / "events"), "ECR_PAUSE_CONTROL": str(sut_root / "control"),
            "ECR_PAUSE_RUN_ID": run_id}


def await_pause(proc: subprocess.Popen, sut_root: Path, label: str, point: str, scope: str, run_id: str,
                folder: Path, timeout: float) -> dict | None:
    """Wait for the pause receipt of `label`. Returns the verified receipt, or None if the process
    exited before reaching the pause (the window was not reached: recorded, never treated as a kill)."""
    ready = sut_root / "events" / pausepoint_ready(label)
    deadline = time.monotonic() + timeout
    while not ready.is_file():
        if proc.poll() is not None:
            return None
        if time.monotonic() > deadline:
            raise InfraError(f"{label}: pause {point} not reached within {timeout}s while process alive")
        time.sleep(0.01)
    receipt = json.loads((sut_root / "events" / pausepoint_receipt(label)).read_text())
    marker = json.loads(ready.read_text())
    problems = []
    if not acreceipts.basis_hash_ok(receipt) or marker.get("pause_sha256") != receipt.get("receipt_sha256"):
        problems.append("pause receipt hash")
    if receipt.get("pause_point") != point or receipt.get("scope") != scope or receipt.get("process_label") != label:
        problems.append("pause point/scope/label")
    if receipt.get("run_id") != run_id or receipt.get("actor_pid") != proc.pid:
        problems.append("pause run id or pid")
    if receipt.get("actor_euid") != identity("ecr-sut")[0]:
        problems.append("pause euid")
    if problems:
        raise IntegrityError(f"{label}: pause provenance mismatch: {problems}")
    root_json(folder / f"{label}-pause-observed.json", receipt)
    return receipt


def pausepoint_receipt(label: str) -> str:
    return f"pause-{label}.json"


def pausepoint_ready(label: str) -> str:
    return f"pause-{label}-ready.json"


def launch_record(proc: subprocess.Popen, label: str, folder: Path, requested_utc_ns: int | None = None) -> dict:
    """F3: identity of a started agent process, recorded right after start (pid, /proc start time, uid).
    started_utc_ns is the time just BEFORE the process was requested (used by the snapshot-timing check);
    recorded_utc_ns is when this record was taken."""
    now = time.time_ns()
    record = {"label": label, "pid": proc.pid, "starttime": ackill.proc_starttime(proc.pid),
              "uid": ackill.proc_uid(proc.pid), "started_utc_ns": requested_utc_ns if requested_utc_ns is not None else now,
              "recorded_utc_ns": now}
    root_json(folder / f"{label}-start.json", record)
    return record


def kill_and_check(proc: subprocess.Popen, point: str, label: str, run_id: str, basis: dict, folder: Path,
                   name: str, *, launch: dict, adapter_identity: dict | None = None) -> dict:
    receipt = ackill.kill_with_receipt(proc, point=point, label=label, run_id=run_id, basis=basis,
                                       write_json=root_json, folder=folder, name=name)
    problems = acreceipts.validate_kill_receipt(receipt, basis, expected_point=point,
                                                expected_uid=identity("ecr-sut")[0],
                                                adapter_identity=adapter_identity, launch_record=launch)
    root_json(folder / f"{name}-validation.json", {"valid": not problems, "problems": problems})
    if problems:
        raise IntegrityError(f"{label}: kill receipt invalid: {problems}")
    return receipt


def tree_sha(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode() + b"\0")
        if p.is_file():
            h.update(hashlib.sha256(p.read_bytes()).hexdigest().encode())
        h.update(b"\n")
    return h.hexdigest()


def snapshot_store(sut_root: Path, folder: Path, index: int, processes: dict) -> dict:
    live = sorted(name for name, p in processes.items() if p is not None and p.poll() is None)
    started = time.time_ns()
    target = folder / f"pre-recovery-{index}"
    copy_tree(sut_root / "store", target)
    record = {"index": index, "started_utc_ns": started, "finished_utc_ns": time.time_ns(),
              "tree_sha256": tree_sha(target), "live_agent_processes": live}
    root_json(folder / f"pre-recovery-{index}-record.json", record)
    return record


def apply_overlay_ac(store: Path, overlay: str, cell_ids: dict, alt_key: str | None) -> dict:
    """ac1 overlays (applied as root between a kill and the next recovery; every change recorded)."""
    auth = store / "authority"

    def first_grant() -> Path:
        grants = sorted((auth / "grants").glob("*.json"))
        firsts = [g for g in grants if json.loads(g.read_text()).get("first_dispatch")]
        if len(firsts) != 1:
            raise IntegrityError(f"{overlay}: expected exactly one first-dispatch grant, found {len(firsts)}")
        return firsts[0]

    def truncate(path: Path) -> dict:
        data = path.read_bytes()
        cut = data[: max(1, len(data) // 2)]
        tmp = path.with_name(path.name + ".ac1-trunc")
        tmp.write_bytes(cut)
        os.chown(tmp, uid("ecr-sut"), gid("ecr-sut"))
        os.chmod(tmp, 0o640)
        os.replace(tmp, path)
        return {"overlay": overlay, "path": str(path.relative_to(store)), "original_sha256": hashlib.sha256(data).hexdigest(),
                "truncated_sha256": hashlib.sha256(cut).hexdigest(), "kept_bytes": len(cut), "original_bytes": len(data)}

    if overlay == "F5_SUPERSEDED_VERSION":
        return apply_overlay(store, overlay, cell_ids) or {"overlay": overlay}
    if overlay == "TRUNCATE_GRANT":
        return truncate(first_grant())
    if overlay == "TRUNCATE_CONSUMED":
        consumed = sorted((auth / "consumed").glob("*.json"))
        if len(consumed) != 1:
            raise IntegrityError(f"{overlay}: expected exactly one consumed marker")
        return truncate(consumed[0])
    if overlay == "TRUNCATE_FENCE":
        fences = sorted((auth / "fences").glob("*.json"))
        if not fences:
            raise IntegrityError(f"{overlay}: no fence record to truncate")
        return truncate(fences[-1])
    if overlay == "GHOST_CONSUMED":
        owned_json(auth / "consumed" / "ghost.json", {"kind": "consumed", "grant_id": "ghost",
                   "logical_operation_id": cell_ids["logical_operation_id"], "authority_seq": 999}, "ecr-sut", "ecr-sut")
        return {"overlay": overlay}
    if overlay == "VOID_AND_CONSUMED":
        g = json.loads(first_grant().read_text())
        owned_json(auth / "void" / f"{g['grant_id']}.json", {"kind": "void", "grant_id": g["grant_id"],
                   "reason": "ac1 injected", "authority_seq": 998}, "ecr-sut", "ecr-sut")
        return {"overlay": overlay, "grant_id": g["grant_id"]}
    if overlay == "CONSUMED_KEY_MISMATCH":
        path = sorted((auth / "consumed").glob("*.json"))[0]
        value = json.loads(path.read_text())
        owned_json(path, {**value, "logical_operation_id": value["logical_operation_id"] + "-other"}, "ecr-sut", "ecr-sut",
                   replace=True)
        return {"overlay": overlay, "path": str(path.relative_to(store))}
    if overlay == "FOREIGN_KEY_FENCE":
        owned_json(auth / "fences" / "99990.json", {"kind": "fence", "fence_id": "99990",
                   "logical_operation_id": cell_ids["logical_operation_id"] + "-other", "http_status": 200,
                   "response": {"result": "DEFINITELY_NOT_COMMITTED", "retry_generation": 1}, "authority_seq": 997},
                   "ecr-sut", "ecr-sut")
        return {"overlay": overlay}
    if overlay == "STALE_FENCE_COPY":
        fences = sorted((auth / "fences").glob("*.json"))
        negatives = [json.loads(f.read_text()) for f in fences
                     if (json.loads(f.read_text()).get("response") or {}).get("result") == "DEFINITELY_NOT_COMMITTED"]
        if not negatives:
            raise IntegrityError(f"{overlay}: no negative fence record to copy")
        top = max(json.loads(p.read_text()).get("authority_seq", 0) for p in auth.rglob("*.json") if p.suffix == ".json")
        copy = {**negatives[0], "fence_id": "99991", "authority_seq": top + 1}
        owned_json(auth / "fences" / "99991.json", copy, "ecr-sut", "ecr-sut")
        return {"overlay": overlay, "copied_generation": copy["response"].get("retry_generation")}
    if overlay == "REWRITE_AUTHORITY_KEYS":
        if not alt_key:
            raise IntegrityError(f"{overlay}: alternative key missing")
        changed = []
        for sub in ("grants", "consumed"):
            for path in sorted((auth / sub).glob("*.json")):
                value = json.loads(path.read_text())
                new = {**value, "logical_operation_id": alt_key}
                if "idempotency_key" in value:
                    new["idempotency_key"] = alt_key
                owned_json(path, new, "ecr-sut", "ecr-sut", replace=True)
                changed.append(str(path.relative_to(store)))
        return {"overlay": overlay, "alt_key": alt_key, "changed": changed}
    raise ValueError("unknown ac1 overlay " + overlay)


# ------------------------------------------------------------------------------------ one attempt (ac1)
def run_attempt(public: Path, manifest: dict, unit: dict, attempt: dict, batch: Path, roots: dict) -> dict:
    """Run one attempt of one ac1 unit. Never raises for experiment-level failures; returns the facts.

    scenario keys:
      profile          actual endpoint profile P0..P3 (unit["endpoint_contract"] is the declared one)
      barrier          base barrier of the send process (B2_DROP, B4, B5) or None
      send_pause       pause point in the send process (scope "first") or None
      no_fault         True for the controls: the send process runs to normal completion
      recoveries       list of {"label", "pause" (point|None)}; the last one never pauses
      overlays         list of {"before_recovery": n, "name": overlay}
      tool_steps       list of {"before_recovery": n, "tool": name, "args": {...}} (stress S6 only)
      recovery_key     "ALT" to give recovery a different logical-operation key (I1, B1a, B1b)
    """
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
             "replay": "PENDING", "sut_started": False, "errors": [], "kills": [], "snapshots": [],
             "recovery_exits": {}, "abnormal_exits": [], "pause_not_reached": [], "overlays": [], "tool_steps": []}
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
                  "transport_request_id": ids["transport_request_id"],
                  "endpoint_contract": unit["endpoint_contract"], "r2_mode": mode}
        root_json(sut_root / "send-configuration.json", common, "ecr-sut")
        agent = str(public / AGENT[executor])
        send_env = pause_env(scenario["send_pause"], "first", "agent-send", sut_root, label) if scenario.get("send_pause") else None
        sut = processes["agent_send"] = start(agent, ["send", str(sut_root / "send-configuration.json")], "ecr-sut",
                                              public / "src", folder / "agent-send", send_env)
        facts["sut_started"] = True
        send_launch = launch_record(sut, "agent-send", folder)
        adapter_identity = {"pid": ready["pid"], "euid": identity("ecr-adapter")[0]}
        send_kill = None
        fate_before = None
        barrier_name = scenario.get("barrier")
        if scenario.get("send_pause"):
            # B1 (intent persisted) is the only barrier before the authority writes; release it, then the pause
            send_kill = drive_barriers_until(sut, sut_root, adapter_root, label, folder, endpoint, target=1, kill=False,
                                             launch=send_launch, adapter_identity=adapter_identity)
            basis = await_pause(sut, sut_root, "agent-send", scenario["send_pause"], "first", label, folder, 30)
            if basis is None:
                facts["pause_not_reached"].append({"label": "agent-send", "point": scenario["send_pause"],
                                                   "exit": sut.returncode})
                raise IntegrityError("send pause not reached")
            send_kill = kill_and_check(sut, scenario["send_pause"], "agent-send", label, basis, folder, "injection-receipt",
                                       launch=send_launch)
            facts["kills"].append({"label": "agent-send", "point": scenario["send_pause"]})
        elif scenario.get("no_fault"):
            drive_barriers_until(sut, sut_root, adapter_root, label, folder, endpoint, target=6, kill=False, release_last=True,
                                 launch=send_launch, adapter_identity=adapter_identity)
            code = sut.wait(timeout=30)
            facts["send_exit"] = code
            if code != 0:
                raise IntegrityError(f"control send process did not complete normally (exit {code})")
            # scorer_v2 requires an injection receipt file; a control has none to give (design record DC2).
            # F1: the receipt states the conditions validate_none_receipt checks, including the send exit code.
            none_receipt = {"injection_action": "NONE", "exit_status": None, "barrier_id": "NONE", "point": "NONE",
                            "normal_completion": True, "send_exit_code": code, "run_id": label}
            root_json(folder / "injection-receipt.json", none_receipt)
            none_problems = acreceipts.validate_none_receipt(none_receipt)
            if none_problems:
                raise IntegrityError(f"control NONE receipt invalid: {none_problems}")
            fate_before = json.loads((adapter_root / "events/original-done.json").read_text()) \
                if (adapter_root / "events/original-done.json").is_file() else None
        else:
            target = BARRIER_INDEX[barrier_name]
            send_kill = drive_barriers_until(sut, sut_root, adapter_root, label, folder, endpoint, target=target, kill=True,
                                             launch=send_launch, adapter_identity=adapter_identity)
            facts["kills"].append({"label": "agent-send", "point": f"B{target}"})
            if barrier_name == "B2_DROP":
                release(adapter_root / "control", "drop-request")
                fate_before = settle_original(adapter_root)
            elif barrier_name == "B4":
                fate_before = settle_original(adapter_root)
            elif target >= 5:
                fate_before = json.loads((adapter_root / "events/original-done.json").read_text())
        root_json(folder / "original-before-recovery.json", {"fate": fate_before})

        bridge, arms_root = BRIDGE[manifest["planner_route"]]
        recovery_key = ids["logical_operation_id"] + "-alt" if scenario.get("recovery_key") == "ALT" else ids["logical_operation_id"]
        recover_config = {**common, "logical_operation_id": recovery_key, "policy": unit["policy"],
                          "node": str(public / "tools/planner_recorder.py"), "plan_bridge": str(public / bridge),
                          "arm_dir": str(public / arms_root / unit["classifier_arm"])}
        root_json(sut_root / "recover-configuration.json", recover_config, "ecr-sut")
        recorder_env = {"ECR_REAL_NODE": str(NODE), "ECR_NODE_SHA256": manifest["node_binary_sha256"],
                        "ECR_PLANNER_RECORD_DIR": str(sut_root / "planner-calls"), "ECR_UNIT_ID": unit["unit_id"],
                        "ECR_ATTEMPT_ID": attempt["attempt_id"], "ECR_CODE_IDENTITY": attempt["manifest_sha256"],
                        "ECR_CODE_MANIFEST": str(public / MANIFEST_NAME), "ECR_CODE_ROOT": str(public),
                        "ECR_NODE_STAT": NODE_STAT, "ECR_PLANNER_ROUTE": manifest["planner_route"]}
        if manifest["planner_route"] == "J":
            recorder_env.update(ECR_JS_MANIFEST=str(public / "arms_js/strip-manifest.json"),
                                ECR_JS_IDENTITY=sha(public / "arms_js/strip-manifest.json"))
        previous_kill = send_kill
        plan = scenario["recoveries"]
        if not plan or plan[-1].get("pause"):
            raise RuntimeError("cell definition error: the last recovery must not pause")
        for index, step in enumerate(plan, 1):
            for ov in [o for o in scenario.get("overlays", []) if o["before_recovery"] == index]:
                applied = apply_overlay_ac(sut_root / "store", ov["name"], ids, ids["logical_operation_id"] + "-alt")
                root_json(folder / f"overlay-{index}-{ov['name']}.json", applied)
                facts["overlays"].append(applied)
            for ts in [t for t in scenario.get("tool_steps", []) if t["before_recovery"] == index]:
                facts["tool_steps"].append(run_tool_step(public, ts, sut_root, folder, index))
            snap = snapshot_store(sut_root, folder, index, processes)
            name = step["label"]
            env = dict(recorder_env)
            if step.get("pause"):
                env.update(pause_env(step["pause"], "recovery", name, sut_root, label))
            start_record = {"label": name, "started_utc_ns": time.time_ns()}
            proc = processes[name] = start(agent, ["recover", str(sut_root / "recover-configuration.json")],
                                           "ecr-sut", public / "src", folder / name, env)
            launch = launch_record(proc, name, folder, requested_utc_ns=start_record["started_utc_ns"])
            timing = acreceipts.validate_snapshot_timing(previous_kill, snap, start_record,
                                                         live_agent_processes=snap["live_agent_processes"])
            root_json(folder / f"pre-recovery-{index}-timing.json", {"valid": not timing, "problems": timing})
            facts["snapshots"].append({"index": index, "valid": not timing, "problems": timing})
            if timing:
                raise IntegrityError(f"snapshot {index} timing invalid: {timing}")
            if step.get("pause"):
                basis = await_pause(proc, sut_root, name, step["pause"], "recovery", label, folder, RECOVERY_TIMEOUT_S)
                if basis is None:
                    facts["recovery_exits"][name] = proc.returncode
                    facts["pause_not_reached"].append({"label": name, "point": step["pause"], "exit": proc.returncode})
                    raise IntegrityError(f"{name}: pause {step['pause']} not reached")
                previous_kill = kill_and_check(proc, step["pause"], name, label, basis, folder, f"injection-receipt-{name}",
                                               launch=launch)
                facts["kills"].append({"label": name, "point": step["pause"]})
                facts["recovery_exits"][name] = previous_kill["normalized_returncode"]
            else:
                try:
                    code = proc.wait(timeout=RECOVERY_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                    facts["infra_events"].append(f"{name} timeout after {RECOVERY_TIMEOUT_S}s")
                    code = None
                facts["recovery_exits"][name] = code
                facts["recovery_exit"] = code
                if code not in (0, None):
                    facts["abnormal_exits"].append({"label": name, "exit": code})
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
        facts.update(score_attempt(public, unit, label, folder, sut_root, adapter_root, ids, db, facts=facts))
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
        try:
            observation = score_attempt(public, unit, label, folder, sut_root, adapter_root, ids, db,
                                        suffix="-after-failure", facts=facts)
            facts["failed_attempt_observation"] = observation
            for key in ("planner_records", "planner_record_reasons", "planner_failures", "planner_calls", "agent_plan_records"):
                facts[key] = observation[key]
        except Exception as error:
            facts["failed_attempt_observation"] = {"error": f"{type(error).__name__}: {error}"}
    state, reasons = aclib.evaluate_attempt(facts, unit)
    facts.update(state=state, state_reasons=reasons)
    root_json(folder / "attempt-result.json", facts)
    return facts


def drive_barriers_until(sut: subprocess.Popen, sut_root: Path, adapter_root: Path, label: str, folder: Path,
                         endpoint: subprocess.Popen, *, target: int, kill: bool, release_last: bool = False,
                         launch: dict, adapter_identity: dict) -> dict | None:
    """Release barriers B1..target-1 (and B<target> if release_last), verifying every barrier receipt as
    run_r2 did; if kill, SIGKILL the agent at B<target> with a raw-status receipt (injection-receipt)."""
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
        if index == target and kill:
            return kill_and_check(sut, f"B{index}", "agent-send", label, receipt, folder, "injection-receipt",
                                  launch=launch, adapter_identity=adapter_identity)
        if index == target and not release_last:
            release(actor_root / "control", f"release-B{index}")
            return None
        release(actor_root / "control", f"release-B{index}")
        if index == 2:
            release(adapter_root / "control", "allow-accept")
    return None


def run_tool_step(public: Path, step: dict, sut_root: Path, folder: Path, index: int) -> dict:
    """Stress S6 only: run tools/direct_authorize.py as ecr-sut between recoveries (bypasses the agent)."""
    name = f"tool-{index}-{step['tool']}"
    args = [str(sut_root / "recover-configuration.json"), json.dumps(step.get("args", {}))]
    proc = start(str(public / "tools/direct_authorize.py"), args, "ecr-sut", public / "src", folder / name)
    code = proc.wait(timeout=60)
    out = {"tool": step["tool"], "exit": code, "stdout": (folder / f"{name}.stdout").read_text()[-2000:]}
    root_json(folder / f"{name}-result.json", out)
    return out


def truncation_failure_reasons(error: Exception, folder: Path, scorer_label: str, suffix: str, adapter_root: Path,
                               facts: dict | None) -> list[str]:
    """F2: reasons why a scorer failure is NOT the expected parse error on an injected truncated file
    ([] = expected, may be tolerated). Required, all of them:
      * the runner raised 'Scorer process failure' (not a timeout or identity error) with exit_code == 1;
      * the scorer's last stderr line is an uncaught json.decoder.JSONDecodeError;
      * at least one TRUNCATE_* overlay was applied in this attempt, and each truncated file in the scored
        store copy still has the truncated hash recorded by the overlay and does not parse;
      * every other JSON file of the scored store copy and of the endpoint log parses, so the truncated file
        is the only possible cause of the parse error."""
    reasons = []
    if not str(error).startswith("Scorer process failure"):
        reasons.append(f"not a scorer process failure: {str(error)[:200]}")
    if getattr(error, "exit_code", None) != 1:
        reasons.append(f"scorer exit_code is {getattr(error, 'exit_code', None)}, expected 1")
    stderr_path = folder / f"{scorer_label}.stderr"
    try:
        lines = [x for x in stderr_path.read_text(errors="replace").splitlines() if x.strip()]
    except OSError as err:
        lines = []
        reasons.append(f"stderr unreadable: {err}")
    if not lines or not lines[-1].startswith("json.decoder.JSONDecodeError"):
        reasons.append("last stderr line is not an uncaught json.decoder.JSONDecodeError")
    store = folder / f"sut-copy{suffix}" / "store"
    truncated = [o for o in (facts or {}).get("overlays", []) if str(o.get("overlay", "")).startswith("TRUNCATE_")]
    if not truncated:
        reasons.append("no TRUNCATE_* overlay recorded for this attempt")
    truncated_paths = set()
    for o in truncated:
        path = store / o["path"]
        truncated_paths.add(path.resolve())
        try:
            data = path.read_bytes()
        except OSError as err:
            reasons.append(f"truncated file missing in the store copy: {o['path']} ({err})")
            continue
        if hashlib.sha256(data).hexdigest() != o.get("truncated_sha256"):
            reasons.append(f"truncated file {o['path']} differs from the overlay record")
        try:
            json.loads(data)
            reasons.append(f"truncated file {o['path']} parses; it cannot be the cause")
        except ValueError:
            pass
    for root in (store, adapter_root / "events"):
        for p in sorted(root.rglob("*.json")) if root.is_dir() else []:
            if p.resolve() in truncated_paths:
                continue
            try:
                json.loads(p.read_text())
            except (OSError, ValueError):
                reasons.append(f"another file does not parse: {p}")
    return reasons


def filter_expected_faults(unit: dict, folder: Path, suffix: str, facts: dict | None) -> tuple[set[str], set[str], dict]:
    """F1 / F10: gate expected primary and non-gating secondary scorer faults on the unit's specific
    construction so they never act as general exemptions:
      * INJECTION_RECEIPT_MISMATCH holds only for a no-fault control (window CTRL, no_fault=True) whose
        NONE receipt passes acreceipts.validate_none_receipt;
      * MISSING_FINAL_STATE holds only for an S-layer TRUNCATE_* unit where a TRUNCATE_* overlay was
        declared and applied, the recovery exited abnormally, and store/final.json is absent;
      * FOREIGN_EFFECT_IN_LEDGER holds only for a B1B cross-key tampering unit (window B1B, recovery_key ALT)
        where the REWRITE_AUTHORITY_KEYS overlay was declared and applied."""
    expected_primary = set(unit.get("expected_primary_faults", []))
    expected_secondary = set(unit.get("secondary_faults_not_gating", []))
    refused = {}
    scenario = unit.get("scenario") or {}
    applied_overlays = (facts or {}).get("overlays") or []
    if "INJECTION_RECEIPT_MISMATCH" in (expected_primary | expected_secondary):
        try:
            none_receipt = json.loads((folder / "injection-receipt.json").read_text())
        except (OSError, ValueError):
            none_receipt = None
        none_problems = acreceipts.validate_none_receipt(none_receipt)
        if unit.get("window") != "CTRL" or not scenario.get("no_fault") or none_problems:
            expected_primary.discard("INJECTION_RECEIPT_MISMATCH")
            expected_secondary.discard("INJECTION_RECEIPT_MISMATCH")
            refused["INJECTION_RECEIPT_MISMATCH"] = {
                "window": unit.get("window"),
                "no_fault": bool(scenario.get("no_fault")),
                "problems": none_problems,
            }
    if "MISSING_FINAL_STATE" in (expected_primary | expected_secondary):
        trunc_declared = any(str(o.get("name", "")).startswith("TRUNCATE_") for o in scenario.get("overlays", []))
        trunc_applied = any(str(o.get("overlay", "")).startswith("TRUNCATE_") for o in applied_overlays)
        abnormal = bool((facts or {}).get("abnormal_exits"))
        final_present = (folder / f"sut-copy{suffix}/store/final.json").is_file()
        if not (str(unit.get("window", "")).startswith("TRUNCATE_") and trunc_declared and trunc_applied
                and abnormal and not final_present):
            expected_primary.discard("MISSING_FINAL_STATE")
            expected_secondary.discard("MISSING_FINAL_STATE")
            refused["MISSING_FINAL_STATE"] = {
                "window": unit.get("window"),
                "truncate_declared": trunc_declared,
                "truncate_applied": trunc_applied,
                "abnormal_exits": list((facts or {}).get("abnormal_exits") or []),
                "final_state_present": final_present,
            }
    if "FOREIGN_EFFECT_IN_LEDGER" in (expected_primary | expected_secondary):
        rewrite_declared = any(o.get("name") == "REWRITE_AUTHORITY_KEYS" for o in scenario.get("overlays", []))
        rewrite_applied = any(o.get("overlay") == "REWRITE_AUTHORITY_KEYS" for o in applied_overlays)
        if not (unit.get("window") == "B1B" and scenario.get("recovery_key") == "ALT"
                and rewrite_declared and rewrite_applied):
            expected_primary.discard("FOREIGN_EFFECT_IN_LEDGER")
            expected_secondary.discard("FOREIGN_EFFECT_IN_LEDGER")
            refused["FOREIGN_EFFECT_IN_LEDGER"] = {
                "window": unit.get("window"),
                "recovery_key": scenario.get("recovery_key"),
                "rewrite_declared": rewrite_declared,
                "rewrite_applied": rewrite_applied,
            }
    return expected_primary, expected_secondary, refused


def score_attempt(public, unit, label, folder, sut_root, adapter_root, ids, db, suffix="", facts=None) -> dict:
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
    # Design record DC3: the unchanged primary/secondary scorers read every authority record with json.loads
    # and fail on the deliberately truncated records of the S-layer truncation cells. Only for units that
    # declare it in advance is such a scorer failure recorded instead of stopping the batch; the scoring
    # rules are not changed, and the diagnostic scorer then reports the affected metrics as UNKNOWN.
    # F2: tolerated only when the failure is provably the expected parse error on the injected truncated file.
    tolerated = set(unit.get("scorer_failure_tolerated", []))
    scorer_failures = {}
    try:
        primary = score(public, "scorer_v2.py", context, folder, f"primary-scorer_v2{suffix}")
    except InfraError as error:
        reasons = truncation_failure_reasons(error, folder, f"primary-scorer_v2{suffix}", suffix, adapter_root, facts)
        if "primary" not in tolerated or reasons:
            raise InfraError(f"{error} | not tolerated: {reasons or 'unit does not declare it'}")
        primary, scorer_failures["primary"] = {"faults": [], "sends_judged": None}, str(error)
    try:
        secondary = score(public, "scorer.py", context, folder, f"secondary-scorer_r1{suffix}")
    except InfraError as error:
        reasons = truncation_failure_reasons(error, folder, f"secondary-scorer_r1{suffix}", suffix, adapter_root, facts)
        if "secondary" not in tolerated or reasons:
            raise InfraError(f"{error} | not tolerated: {reasons or 'unit does not declare it'}")
        secondary, scorer_failures["secondary"] = {"faults": []}, str(error)
    root_json(folder / f"scorer-failures-recorded{suffix}.json", scorer_failures)
    diag_context = {**context, "unit": {k: unit[k] for k in ("unit_id", "window", "layer", "r2_mode", "scenario",
                                                             "prediction", "opportunity_rule", "expected_primary_faults", "ids")},
                    "attempt_folder": str(folder), "suffix": suffix,
                    "recovery_exits": (facts or {}).get("recovery_exits", {}),
                    "kills": (facts or {}).get("kills", []),
                    "authority_sha256": sha(public / "src/authority.py"),
                    "agent_uid": identity("ecr-sut")[0], "adapter_uid": identity("ecr-adapter")[0],
                    "primary_score": str(folder / f"primary-scorer_v2{suffix}-score.json")}
    diag = score(public, "scorer_diag.py", diag_context, folder, f"diag-scorer_ac1{suffix}")
    expected_primary, expected_secondary, refused = filter_expected_faults(unit, folder, suffix, facts)
    if "INJECTION_RECEIPT_MISMATCH" in refused:
        root_json(folder / f"none-receipt-exemption-refused{suffix}.json", refused["INJECTION_RECEIPT_MISMATCH"])
    if refused:
        root_json(folder / f"fault-exemptions-refused{suffix}.json", refused)
    faults = [f"primary:{f}" for f in primary["faults"] if f not in expected_primary]
    faults += [f"secondary:{f}" for f in secondary["faults"] if f not in expected_secondary]
    if diag["A_execution"] != "VALID":
        faults.append("diag:A_EXECUTION_INVALID")
    return {"final_state_present": (folder / f"sut-copy{suffix}/store/final.json").is_file(),
            "scorer_failures_recorded": scorer_failures,
            "planner_records": records["status"], "planner_record_reasons": records["reasons"],
            "planner_failures": len(records["failures"]), "planner_calls": len(records["calls"]),
            "agent_plan_records": plan_records, "scorer_faults": faults,
            "secondary_faults_recorded_not_gating": sorted(set(secondary["faults"]) & expected_secondary),
            "primary_faults_recorded_not_gating": sorted(set(primary["faults"]) & expected_primary),
            "primary_score": primary, "secondary_score": secondary, "diag_score": diag}


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
    cells = json.loads((HERE / "private/cells_ac.json").read_text())
    if sha(HERE / "private/cells_ac.json") != manifest["files"]["private/cells_ac.json"]:
        raise RuntimeError("cell table drift")
    if args.set == "ACQ":
        units_by_id = {u["unit_id"]: u for u in cells["units"]}
        units = [units_by_id[u] for u in cells["qualification_unit_ids"]]
    else:
        authorization = json.loads((HERE / "ac1-authorization.json").read_text())
        if authorization.get("status") != "PASS_HOST_AUDIT" or authorization.get("manifest_sha256") != manifest_sha:
            raise RuntimeError("Host qualification authorization missing or for another manifest")
        # ac1: the authorization is bound to one batch id and one set in code (the frozen runner did not check these)
        if authorization.get("authorizes", {}).get("batch_id") != args.batch_id or authorization.get("authorizes", {}).get("set") != args.set:
            raise RuntimeError("Authorization names another batch id or set")
        units = [u for u in cells["units"] if args.set == "ACFULL" or u.get("first_batch")]
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
    if args.set == "ACQ" and status == "COMPLETED_PENDING_REPLAY":
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
    run.add_argument("--set", choices=["ACQ", "AC1", "ACFULL"], required=True)
    run.add_argument("--batch-id", required=True)
    run.add_argument("--continuation")
    rep = sub.add_parser("replay")
    rep.add_argument("--batch-id", required=True)
    args = parser.parse_args()
    manifest_path = HERE / MANIFEST_NAME
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
