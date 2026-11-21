"""Root supervisor for the asynchronous simulation (ECR experiment 2). No automatic reruns.

Per cell: start the simulated endpoint (ecr-adapter), run the agent send path
(ecr-sut) to the scenario's barrier, SIGKILL it there with a durable injection
receipt, resolve the in-flight original request as the scenario specifies,
apply the scenario's local-evidence overlay, run the recovery policy (ecr-sut),
settle late requests, stop the endpoint and score independently (ecr-scorer).

Modes: --mode qualify (harness checks on CONV/RETRY cells + offline scorer
controls) and --mode formal (all predeclared cells, requires host authorization).
Barrier mechanics follow qualification_v2/run.py.
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
from protocol import PHASES, digest, durable_json, wait_file  # noqa: E402

BASE = Path("/var/lib/ecr-followup-r1")
NODE = Path("/opt/ecr-followup-r1-runtime/node-v24.21.0/bin/node")
PUBLIC_PARENT = Path("/opt/ecr-followup-r1-async")
IDENTITIES = {"ecr-sut": [999, 987, [987]], "ecr-adapter": [996, 986, [986]], "ecr-scorer": [995, 985, [985, 988]]}
BARRIER_INDEX = {"B1": 1, "B2_DROP": 2, "B2_LATE": 2, "B2_ARRIVE": 2, "B3_LATE": 3, "B4": 4, "B5": 5, "B6": 6}


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


def start(module: str, args: list[str], role: str, cwd: Path, output: Path, executable: str | None = None) -> subprocess.Popen:
    user_id, group_id, groups = identity(role)
    with open(output.with_suffix(".stdout"), "xb") as out, open(output.with_suffix(".stderr"), "xb") as err:
        return subprocess.Popen([executable or sys.executable, "-s", "-E", module, *args], user=user_id, group=group_id,
                                extra_groups=groups, cwd=cwd, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                                "HOME": pwd.getpwnam(role).pw_dir, "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
                                stdout=out, stderr=err, start_new_session=True)


def request(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
        return json.loads(response.read())


def guard(manifest: dict) -> None:
    if os.geteuid() != 0 or platform.system() != "Linux":
        raise RuntimeError("Run with sudo in the dedicated Ubuntu guest only")
    if Path("/sys/class/dmi/id/product_uuid").read_text().strip().lower() not in manifest["vm_uuids"]:
        raise RuntimeError("Dedicated VM identity mismatch")
    if subprocess.check_output(["findmnt", "-n", "-o", "FSTYPE", "-T", str(BASE)], text=True).strip() != "ext4":
        raise RuntimeError("Native ext4 required")
    if sys.version_info[:2] != (3, 12) or platform.release() != "6.8.0-146-generic":
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
        os.chmod(target, 0o644)
    return public


def settle_original(adapter_root: Path, seconds: float = 40) -> dict:
    deadline = time.monotonic() + seconds
    events, control = adapter_root / "events", adapter_root / "control"
    while not (events / "original-done.json").is_file():
        for index in (3, 4):
            if (events / f"B{index}-ready.json").is_file():
                release(control, f"release-B{index}")
        if time.monotonic() > deadline:
            raise TimeoutError("original request did not settle")
        time.sleep(0.01)
    return json.loads((events / "original-done.json").read_text())


def apply_overlay(store: Path, overlay: str | None, cell_ids: dict) -> dict | None:
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
    else:
        raise ValueError("unknown overlay " + overlay)
    return {"overlay": overlay}


def score(public: Path, context: dict, folder: Path, label: str) -> dict:
    path = folder / f"{label}-context.json"
    root_json(path, context)
    process = start(str(public / "src/scorer.py"), [str(path)], "ecr-scorer", public, folder / label)
    try:
        code = process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        raise RuntimeError("Scorer timeout " + label)
    if code != 0 or (folder / f"{label}.stderr").read_bytes():
        raise RuntimeError("Scorer process failure " + label + ": " + (folder / f"{label}.stderr").read_text()[-500:])
    result = json.loads((folder / f"{label}.stdout").read_text())
    if result["scorer_uid"] != identity("ecr-scorer")[0] or result["scorer_pid"] != process.pid:
        raise RuntimeError("Scorer identity mismatch")
    root_json(folder / f"{label}-score.json", result)
    return result


def copy_tree(source: Path, target: Path) -> None:
    directory(target, "root", "ecr-scorer", 0o750)
    for item in sorted(source.iterdir()):
        if item.is_symlink():
            raise RuntimeError("Symlink in SUT state " + str(item))
        if item.is_dir():
            copy_tree(item, target / item.name)
        else:
            shutil.copyfile(item, target / item.name)
            os.chown(target / item.name, 0, gid("ecr-scorer"))
            os.chmod(target / item.name, 0o640)


def run_cell(public: Path, cell: dict, batch: Path, roots: dict) -> dict:
    label = cell["run_id"]
    scenario, truth = cell["scenario"], cell["truth"]
    folder = batch / label
    directory(folder, "root", "ecr-scorer", 0o750)
    sut_root, adapter_root = roots["sut"] / label, roots["adapter"] / label
    directory(sut_root, "root", "ecr-sut", 0o750)
    directory(sut_root / "events", "ecr-sut", "ecr-sut", 0o750)
    directory(sut_root / "control", "root", "ecr-sut", 0o750)
    for name in ("store", "store/journal", "store/recovery"):
        directory(sut_root / name, "ecr-sut", "ecr-sut", 0o750)
    directory(adapter_root, "ecr-adapter", "ecr-ledger-readers", 0o750)
    directory(adapter_root / "events", "ecr-adapter", "ecr-ledger-readers", 0o2750)
    directory(adapter_root / "control", "root", "ecr-adapter", 0o750)
    directory(adapter_root / "data", "ecr-adapter", "ecr-ledger-readers", 0o2750)
    ids = cell["ids"]
    db = adapter_root / "data/effects.sqlite3"
    endpoint_config = {"run_id": label, "profile": scenario["profile"], "db": str(db),
                       "gated_transport": ids["transport_request_id"],
                       "events": str(adapter_root / "events"), "control": str(adapter_root / "control")}
    root_json(adapter_root / "configuration.json", endpoint_config, "ecr-adapter")
    endpoint = start(str(public / "src/endpoint.py"), [str(adapter_root / "configuration.json")], "ecr-adapter",
                     public / "src", folder / "endpoint")
    sut = recovery = None
    result = {"run_id": label, "status": "FAIL", "sut_started": False}
    try:
        wait_file(adapter_root / "events/ready.json", 15)
        ready = json.loads((adapter_root / "events/ready.json").read_text())
        if ready["pid"] != endpoint.pid or ready["uid"] != identity("ecr-adapter")[0]:
            raise RuntimeError("Endpoint process identity mismatch")
        if request(ready["port"], "/health")["run_id"] != label:
            raise RuntimeError("Endpoint identity mismatch")
        common = {"run_id": label, "store": str(sut_root / "store"), "events": str(sut_root / "events"),
                  "control": str(sut_root / "control"), "port": ready["port"], "ledger": str(db),
                  "identity": ids["identity"], "queue_item_id": ids["queue_item_id"],
                  "logical_operation_id": ids["logical_operation_id"], "attempt_id": ids["identity"]["attemptId"],
                  "transport_request_id": ids["transport_request_id"]}
        root_json(sut_root / "send-configuration.json", common, "ecr-sut")
        sut = start(str(public / "src/agent.py"), ["send", str(sut_root / "send-configuration.json")], "ecr-sut",
                    public / "src", folder / "agent-send")
        result["sut_started"] = True
        target = BARRIER_INDEX[scenario["barrier"]]
        for index in range(1, target + 1):
            actor_root, process, actor = (adapter_root, endpoint, "ecr-adapter") if index in (3, 4) else (sut_root, sut, "ecr-sut")
            wait_file(actor_root / f"events/B{index}-ready.json", 25)
            receipt = json.loads((actor_root / f"events/B{index}.json").read_text())
            marker = json.loads((actor_root / f"events/B{index}-ready.json").read_text())
            expected_hash = digest({k: v for k, v in receipt.items() if k != "receipt_sha256"})
            if (receipt["receipt_sha256"] != expected_hash or marker["barrier_sha256"] != expected_hash
                    or receipt["run_id"] != label or receipt["actor_pid"] != process.pid
                    or receipt["actor_euid"] != identity(actor)[0] or receipt["durable_sequence"] != index
                    or receipt["phase"] != PHASES[index - 1] or sut.poll() is not None):
                raise RuntimeError("Barrier provenance mismatch B" + str(index))
            root_json(folder / f"B{index}-observed.json", receipt)
            if index == target:
                process_uid = int(Path(f"/proc/{sut.pid}/status").read_text().split("Uid:\t", 1)[1].split()[1])
                if process_uid != identity("ecr-sut")[0] or sut.poll() is not None:
                    raise RuntimeError("Injection target is not the live agent")
                injection = {"run_id": label, "barrier_id": f"B{index}", "phase": PHASES[index - 1],
                             "basis_receipt_sha256": expected_hash, "durable_sequence": index,
                             "durable_timestamp_utc_ns": time.time_ns(), "injection_action": "SIGKILL",
                             "durable_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                             "target_pid": sut.pid, "target_uid": process_uid}
                root_json(folder / "injection-intent.json", injection)
                os.kill(sut.pid, signal.SIGKILL)
                injection["exit_status"] = sut.wait(timeout=10)
                if injection["exit_status"] != -signal.SIGKILL:
                    raise RuntimeError("Actual agent exit is not SIGKILL")
                injection["receipt_sha256"] = digest(injection)
                root_json(folder / "injection-receipt.json", injection)
            else:
                release(actor_root / "control", f"release-B{index}")
                if index == 2:
                    release(adapter_root / "control", "allow-accept")
        # Fate of the original in-flight request before recovery.
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
        recover_config = {**common, "policy": cell["policy"], "endpoint_contract": cell["endpoint_contract"],
                          "node": str(NODE), "plan_bridge": str(public / "src/plan.mjs"),
                          "arm_dir": str(public / "arms" / cell["classifier_arm"]) if cell["classifier_arm"] else ""}
        root_json(sut_root / "recover-configuration.json", recover_config, "ecr-sut")
        recovery = start(str(public / "src/agent.py"), ["recover", str(sut_root / "recover-configuration.json")],
                         "ecr-sut", public / "src", folder / "agent-recover")
        try:
            recovery_code = recovery.wait(timeout=60)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Recovery timeout")
        if recovery_code != 0:
            raise RuntimeError("Recovery process failed: " + (folder / "agent-recover.stderr").read_text()[-1500:])
        fate_after = None
        if barrier_name == "B2_LATE":
            release(adapter_root / "control", "allow-accept")
            fate_after = settle_original(adapter_root)
        elif barrier_name == "B3_LATE":
            fate_after = settle_original(adapter_root)
        root_json(folder / "original-after-recovery.json", {"fate": fate_after})
        release(adapter_root / "control", "stop")
        endpoint_code = endpoint.wait(timeout=15)
        if endpoint_code != 0 or list((adapter_root / "events").glob("error-*.json")):
            raise RuntimeError("Endpoint error or nonzero exit")
        copy_tree(sut_root, folder / "sut-copy")
        context = {"run_id": label, "arm": cell["arm"], "truth": truth, "logical_operation_id": ids["logical_operation_id"],
                   "ledger": str(db), "endpoint_events": str(adapter_root / "events"),
                   "sut_copy": str(folder / "sut-copy"), "injection": str(folder / "injection-receipt.json")}
        scored = score(public, context, folder, "primary")
        if scored["faults"]:
            raise RuntimeError("Infrastructure/integrity fault " + str(scored["faults"]))
        result.update(status="OBSERVED", score=scored, endpoint_exit_status=endpoint_code, recovery_exit_status=recovery_code)
        result["context"] = context
    except Exception as error:
        result.update(error_type=type(error).__name__, error=str(error))
    finally:
        cleanup = {}
        for name, process in (("agent_send", sut), ("agent_recover", recovery)):
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
        result["cleanup"] = cleanup
        root_json(folder / "cell-result.json", result)
    return result


def negative_controls(public: Path, base_result: dict, folder: Path) -> dict:
    """Offline scorer fixtures copied from a completed qualification cell; not extra trials."""
    context = base_result["context"]
    results = {}
    ledger = folder / "negative-duplicate.sqlite3"
    shutil.copyfile(context["ledger"], ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("DROP INDEX IF EXISTS effect_once")
        db.execute("INSERT INTO effects(logical_operation_id,attempt_id,transport_request_id,committed) VALUES(?,?,?,1)",
                   (context["logical_operation_id"], "planted-a", "planted-t"))
    os.chown(ledger, 0, gid("ecr-scorer"))
    os.chmod(ledger, 0o640)
    results["duplicate"] = score(public, {**context, "ledger": str(ledger)}, folder, "negative-duplicate")
    if not results["duplicate"]["duplicate_effect"]:
        raise RuntimeError("Scorer missed planted duplicate")
    empty = folder / "negative-false-completion.sqlite3"
    shutil.copyfile(context["ledger"], empty)
    with sqlite3.connect(empty) as db:
        db.execute("DELETE FROM effects")
    os.chown(empty, 0, gid("ecr-scorer"))
    os.chmod(empty, 0o640)
    results["false_completion"] = score(public, {**context, "ledger": str(empty)}, folder, "negative-false-completion")
    if not results["false_completion"]["false_completion"]:
        raise RuntimeError("Scorer missed planted false completion")
    planted = folder / "negative-unsafe-auth"
    shutil.copytree(context["sut_copy"], planted)
    durable_json(planted / "store/recovery/999.json", {"index": 999, "action": "AUTHORIZE_SEND", "guarantee": "NONE",
                 "basis": "planted", "request": {"transport_request_id": "planted-t", "generation": 0}})
    for path in [planted, *planted.rglob("*")]:
        os.chown(path, 0, gid("ecr-scorer"))
    results["unsafe_authorization"] = score(public, {**context, "sut_copy": str(planted)}, folder, "negative-unsafe-auth")
    if results["unsafe_authorization"]["false_safe_authorizations"] < 1:
        raise RuntimeError("Scorer missed planted unsafe authorization")
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["qualify", "formal"], required=True)
    args = parser.parse_args()
    manifest = json.loads((HERE / "execution-manifest.json").read_text())
    guard(manifest)
    os.umask(0o027)
    plan = json.loads((HERE / "private/cells.json").read_text())
    if args.mode == "formal":
        authorization = json.loads((HERE / "qualification-authorization.json").read_text())
        if authorization.get("status") != "PASS_HOST_AUDIT" or authorization.get("manifest_sha256") != sha(HERE / "execution-manifest.json"):
            raise RuntimeError("Host qualification authorization missing or for another manifest")
        if sha(BASE / "async-sim" / manifest["qualification_id"] / "batch-summary.json") != authorization["guest_summary_sha256"]:
            raise RuntimeError("Qualification summary drift")
    batch_id = manifest["qualification_id"] if args.mode == "qualify" else manifest["formal_id"]
    cells = plan["qualification_cells"] if args.mode == "qualify" else plan["formal_cells"]
    parent = BASE / "async-sim"
    if not parent.exists():
        directory(parent, "root", "ecr-scorer", 0o750)
    batch = parent / batch_id
    directory(batch, "root", "ecr-scorer", 0o750)
    public = deploy(batch_id, manifest)
    roots = {"sut": BASE / "sut-state" / batch_id, "adapter": BASE / "ledger" / batch_id}
    directory(roots["sut"], "root", "ecr-sut", 0o750)
    directory(roots["adapter"], "ecr-adapter", "ecr-ledger-readers", 0o750)
    root_json(batch / "execution-manifest.json", manifest)
    root_json(batch / "environment.json", {"python": sys.version, "kernel": platform.release(),
              "sqlite": sqlite3.sqlite_version, "node_sha256": sha(NODE), "mode": args.mode})
    rows, summary_extra = [], {}
    status = "COMPLETED_REQUIRES_HOST_AUDIT"
    for cell in cells:
        result = run_cell(public, cell, batch, roots)
        rows.append({k: v for k, v in result.items() if k != "context"})
        print(f"{cell['run_id']}={result['status']}", flush=True)
        if result["status"] != "OBSERVED":
            status = "FAIL_RETAIN_EVIDENCE"
            break
        if args.mode == "qualify":
            expected = cell["harness_expectation"]
            observed = {"effect_count": result["score"]["effect_count"], "final_state": result["score"]["final_state"]}
            if observed != expected:
                status = "FAIL_RETAIN_EVIDENCE"
                rows[-1]["harness_expectation_mismatch"] = {"expected": expected, "observed": observed}
                break
            if len(rows) == 1:
                summary_extra["scorer_negative_controls"] = negative_controls(public, result, batch / cell["run_id"])
    done = {r["run_id"] for r in rows}
    rows.extend({"run_id": c["run_id"], "status": "NOT_EXECUTED", "sut_started": False} for c in cells if c["run_id"] not in done)
    if args.mode == "qualify" and status == "COMPLETED_REQUIRES_HOST_AUDIT":
        status = "PASS_QUALIFICATION_REQUIRES_HOST_AUDIT"
    summary = {"batch_id": batch_id, "mode": args.mode, "status": status, "cells": rows,
               "observed": sum(r["status"] == "OBSERVED" for r in rows),
               "executed_known": sum(r.get("sut_started") is True for r in rows),
               "manifest_sha256": sha(HERE / "execution-manifest.json"), "utc_ns": time.time_ns(), **summary_extra}
    root_json(batch / "batch-summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("cells", "scorer_negative_controls")}), flush=True)
    raise SystemExit(0 if status != "FAIL_RETAIN_EVIDENCE" else 1)


if __name__ == "__main__":
    main()
