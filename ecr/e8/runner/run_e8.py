"""E8 root supervisor (ECR-GA crash-window verification). Derived from the frozen runner/run_bl6b.py, which is
IMPORTED (not modified) for: guard(), identity/directory/json helpers, process start, endpoint request, original-
request settling, apply_overlay_bl6, copy_tree, score(), the global attempt index and same-runtime replay.

New in E8 (protocol r1/r2):
  * code version per unit: C0 = src/agent_v3.py (frozen), C1 = src_c1/agent_v3.py (frozen + 6 test-hook lines);
    endpoint per unit: E0 = src/endpoint.py (frozen), E1 = src_e1/endpoint_fi.py (fault injection, "fi" config);
  * several recoveries per unit, each with its own ECR_TEST_CRASH_AT (or clean); exit codes, stderr exception type
    and process outcome recorded per recovery (G-K-proc);
  * final.json archived ONLY between recoveries (final.R{k}.json); the last recovery's final.json is kept;
  * after every recovery task.json / queue.json are snapshotted to <attempt>/diag/audit (root-owned, outside the
    SUT working directory) with source and copy SHA-256;
  * W6/W7: the next recovery starts only after the endpoint log shows the crashed recovery's send (<= 30 s);
  * W6': wait for E1's late-held receipt (<= 30 s) -> run recovery 2 -> release-late -> wait for the held send's
    endpoint log (<= 30 s) -> stop endpoint; ordered receipts with utc_ns;
  * G6 pre-ops: remove task.json after the overlay (receipt with the removed bytes' hash); A4: seed one committed
    effect of another operation K' directly in the ledger as ecr-adapter (tools/seed_effect.py) and give the recovery
    configuration K' (receipt);
  * scoring: frozen scorer_v2 only (primary), then tools/e8_diag.py and, for W6' units, tools/w6_order.py, all as
    ecr-scorer. A scorer fault listed in the cell's expected_scorer_faults (A4: FOREIGN_EFFECT_IN_LEDGER) is recorded
    but does not gate.
Subcommands (sudo in the dedicated guest; guard() of run_bl6b):
  run    --set VQ|E8 --batch-id ID [--continuation AUTH.json]
  replay --batch-id ID
Batch IDs and lanes (LANES): q1 and b1 use /var/lib/ecr-followup-r1/async-sim-e8a and /opt/ecr-followup-r1-async-e8a;
q2 and c1 use ...-e8b. Any other batch ID is refused.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RUNNER = Path(__file__).resolve().parent
HERE = RUNNER.parent
sys.path.insert(0, str(RUNNER))
import run_bl6b as B  # noqa: E402  (frozen; module globals re-pointed below, file unchanged)
import e8lib  # noqa: E402
from protocol import PHASES, digest, durable_json, wait_file  # noqa: E402
import p3lib  # noqa: E402
import planner_records  # noqa: E402

# Lanes (the bl6b/bl6c pattern): each lane has its own batch parent, public parent and global attempt index, so the
# second qualification run and the second formal run are first attempts in their own index. Only these four batch IDs
# are accepted; the lane follows from the ID and is never chosen by hand.
LANES = {"ecr-e8q-srv-q1": ("e8a", "VQ"), "ecr-e8x-srv-b1": ("e8a", "E8"),
         "ecr-e8q-srv-q2": ("e8b", "VQ"), "ecr-e8x-srv-c1": ("e8b", "E8")}


def set_lane(batch_id: str, unit_set: str | None) -> str:
    if batch_id not in LANES or (unit_set is not None and LANES[batch_id][1] != unit_set):
        raise RuntimeError(f"batch id {batch_id!r} is not an E8 batch for set {unit_set}")
    lane = LANES[batch_id][0]
    B.BATCH_PARENT = B.BASE / f"async-sim-{lane}"
    B.PUBLIC_PARENT = Path(f"/opt/ecr-followup-r1-async-{lane}")
    return lane


MANIFEST_NAME = "execution-manifest-e8.json"


def index_unit_id(uid: str) -> str:
    """Attempt-index key of a unit. The frozen p3lib.attempt_dirname accepts only [A-Za-z0-9_.-] after replacing
    ':' and '#'; the 8 W6' unit IDs contain an apostrophe. The apostrophe is encoded as '.prime' (injective: no
    E8 unit ID contains '.prime'). Used ONLY for the attempt index and attempt directory names; cells, predictions,
    attempt-result unit_id and every report keep the original unit ID."""
    if ".prime" in uid:
        raise ValueError("unit id already contains .prime: " + uid)
    key = uid.replace("'", ".prime")
    p3lib.attempt_dirname(p3lib.attempt_id(key, 1))  # raises if still unsafe
    return key
AGENT = {"C0_frozen_bl6": "src/agent_v3.py", "C1_e8_hooks": "src_c1/agent_v3.py"}
ENDPOINT = {"E0_frozen_endpoint": "src/endpoint.py", "E1_fault_endpoint": "src_e1/endpoint_fi.py"}
SYNC_S = 30


def deploy_e8(batch_id: str, manifest: dict) -> Path:
    """run_bl6b.deploy with the E8 manifest name (logic otherwise identical)."""
    if not B.PUBLIC_PARENT.exists():
        B.directory(B.PUBLIC_PARENT, "root", "root", 0o755)
    info = B.PUBLIC_PARENT.lstat()
    if B.PUBLIC_PARENT.is_symlink() or (info.st_uid, info.st_gid, info.st_mode & 0o7777) != (0, 0, 0o755):
        raise RuntimeError("Unsafe public parent")
    public = B.PUBLIC_PARENT / batch_id
    B.directory(public, "root", "root", 0o755)
    for rel in manifest["public_files"]:
        target = public / rel
        for parent in reversed(Path(rel).parents[:-1]):
            if not (public / parent).exists():
                B.directory(public / parent, "root", "root", 0o755)
        shutil.copyfile(HERE / rel, target)
        os.chown(target, 0, 0)
        os.chmod(target, 0o755 if rel == "tools/planner_recorder.py" else 0o644)
        if B.sha(target) != manifest["files"][rel]:
            raise RuntimeError("Deployed file differs from manifest " + rel)
    target = public / MANIFEST_NAME
    shutil.copyfile(HERE / MANIFEST_NAME, target)
    os.chown(target, 0, 0)
    os.chmod(target, 0o644)
    if B.sha(target) != B.sha(HERE / MANIFEST_NAME):
        raise RuntimeError("Deployed execution manifest differs")
    return public


def root_bytes(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.chown(path, 0, B.gid("ecr-scorer"))


def run_attempt_e8(public: Path, manifest: dict, unit: dict, attempt: dict, batch: Path, roots: dict) -> dict:
    label = p3lib.attempt_dirname(attempt["attempt_id"])
    scenario, truth, ids = unit["scenario"], unit["truth"], unit["ids"]
    folder = batch / "attempts" / label
    B.directory(folder, "root", "ecr-scorer", 0o750)
    B.root_json(folder / "attempt-start.json", attempt)
    B.root_json(folder / "cell.json", unit)
    B.directory(folder / "diag", "root", "ecr-scorer", 0o750)
    audit = folder / "diag" / "audit"
    B.directory(audit, "root", "ecr-scorer", 0o750)
    sut_root, adapter_root = roots["sut"] / label, roots["adapter"] / label
    B.directory(sut_root, "root", "ecr-sut", 0o750)
    B.directory(sut_root / "events", "ecr-sut", "ecr-sut", 0o750)
    B.directory(sut_root / "control", "root", "ecr-sut", 0o750)
    for name in ("store", "store/journal", "store/recovery", "planner-calls"):
        B.directory(sut_root / name, "ecr-sut", "ecr-sut", 0o750)
    B.directory(adapter_root, "ecr-adapter", "ecr-ledger-readers", 0o750)
    B.directory(adapter_root / "events", "ecr-adapter", "ecr-ledger-readers", 0o2750)
    B.directory(adapter_root / "control", "root", "ecr-adapter", 0o750)
    B.directory(adapter_root / "data", "ecr-adapter", "ecr-ledger-readers", 0o2750)
    present = sorted(str(p) for r in (sut_root, adapter_root) for p in r.rglob("*") if not p.is_dir())
    B.root_json(folder / "initial-state.json", {"files_present": present, "sut_root": str(sut_root), "adapter_root": str(adapter_root)})
    if present:
        raise RuntimeError("attempt directories not empty at start")
    db = adapter_root / "data/effects.sqlite3"
    events = adapter_root / "events"
    facts = {"attempt_id": attempt["attempt_id"], "unit_id": unit["unit_id"], "label": label,
             "harness_integrity_ok": True, "recovery_exit": None, "final_state_present": False,
             "planner_records": "INCOMPLETE", "planner_failures": 0, "infra_events": [], "scorer_faults": [],
             "replay": "PENDING", "sut_started": False, "errors": [], "e8": {"recoveries": [], "archive": [], "snapshots": [],
             "pre_ops": [], "sync": [], "w6_order_receipts": []}}
    endpoint_config = {"run_id": label, "profile": scenario["profile"], "db": str(db),
                       "gated_transport": ids["transport_request_id"], "events": str(events), "control": str(adapter_root / "control")}
    if unit["endpoint_variant"] == "E1_fault_endpoint":
        endpoint_config["fi"] = unit.get("fi") or {}
    B.root_json(adapter_root / "configuration.json", endpoint_config, "ecr-adapter")
    endpoint = B.start(str(public / ENDPOINT[unit["endpoint_variant"]]), [str(adapter_root / "configuration.json")], "ecr-adapter",
                       public / Path(ENDPOINT[unit["endpoint_variant"]]).parent, folder / "endpoint")
    processes, scored = {}, False
    written = {"receipts": False, "snapshots": False}
    try:
        wait_file(events / "ready.json", 15)
        ready = json.loads((events / "ready.json").read_text())
        if ready["pid"] != endpoint.pid or ready["uid"] != B.identity("ecr-adapter")[0]:
            raise B.IntegrityError("Endpoint process identity mismatch")
        if B.request(ready["port"], "/health")["run_id"] != label:
            raise B.IntegrityError("Endpoint identity mismatch")
        common = {"run_id": label, "store": str(sut_root / "store"), "events": str(sut_root / "events"),
                  "control": str(sut_root / "control"), "port": ready["port"], "ledger": str(db),
                  "identity": ids["identity"], "queue_item_id": ids["queue_item_id"],
                  "logical_operation_id": ids["logical_operation_id"], "attempt_id": ids["identity"]["attemptId"],
                  "transport_request_id": ids["transport_request_id"], "endpoint_contract": unit["endpoint_contract"],
                  "r2_mode": unit["r2_mode"]}
        B.root_json(sut_root / "send-configuration.json", common, "ecr-sut")
        agent_rel = AGENT[unit["code_version"]]
        agent = str(public / agent_rel)
        agent_cwd = public / Path(agent_rel).parent
        sut = processes["agent_send"] = B.start(agent, ["send", str(sut_root / "send-configuration.json")], "ecr-sut",
                                                agent_cwd, folder / "agent-send")
        facts["sut_started"] = True
        # ---- barrier walk and SIGKILL injection: verbatim logic of run_bl6b.run_attempt L444-479 ----
        target = B.BARRIER_INDEX[scenario["barrier"]]
        for index in range(1, target + 1):
            actor_root, process, actor = (adapter_root, endpoint, "ecr-adapter") if index in (3, 4) else (sut_root, sut, "ecr-sut")
            try:
                wait_file(actor_root / f"events/B{index}-ready.json", 25)
            except TimeoutError as error:
                raise B.InfraError(f"barrier B{index} not reached: {error}")
            receipt = json.loads((actor_root / f"events/B{index}.json").read_text())
            marker = json.loads((actor_root / f"events/B{index}-ready.json").read_text())
            expected_hash = digest({k: v for k, v in receipt.items() if k != "receipt_sha256"})
            if (receipt["receipt_sha256"] != expected_hash or marker["barrier_sha256"] != expected_hash
                    or receipt["run_id"] != label or receipt["actor_pid"] != process.pid
                    or receipt["actor_euid"] != B.identity(actor)[0] or receipt["durable_sequence"] != index
                    or receipt["phase"] != PHASES[index - 1] or sut.poll() is not None):
                raise B.IntegrityError("Barrier provenance mismatch B" + str(index))
            B.root_json(folder / f"B{index}-observed.json", receipt)
            if index == target:
                process_uid = int(Path(f"/proc/{sut.pid}/status").read_text().split("Uid:\t", 1)[1].split()[1])
                if process_uid != B.identity("ecr-sut")[0] or sut.poll() is not None:
                    raise B.IntegrityError("Injection target is not the live agent")
                injection = {"run_id": label, "barrier_id": f"B{index}", "phase": PHASES[index - 1],
                             "basis_receipt_sha256": expected_hash, "durable_sequence": index,
                             "durable_timestamp_utc_ns": time.time_ns(), "injection_action": "SIGKILL",
                             "durable_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                             "target_pid": sut.pid, "target_uid": process_uid}
                B.root_json(folder / "injection-intent.json", injection)
                os.kill(sut.pid, signal.SIGKILL)
                injection["exit_status"] = sut.wait(timeout=10)
                if injection["exit_status"] != -signal.SIGKILL:
                    raise B.IntegrityError("Actual agent exit is not SIGKILL")
                injection["receipt_sha256"] = digest(injection)
                B.root_json(folder / "injection-receipt.json", injection)
            else:
                B.release(actor_root / "control", f"release-B{index}")
                if index == 2:
                    B.release(adapter_root / "control", "allow-accept")
        barrier_name = scenario["barrier"]
        fate_before = None
        if barrier_name == "B2_DROP":
            B.release(adapter_root / "control", "drop-request")
            fate_before = B.settle_original(adapter_root)
        elif barrier_name == "B4":
            fate_before = B.settle_original(adapter_root)
        elif barrier_name != "B1":
            raise B.IntegrityError("E8 uses only base barriers B1, B2_DROP and B4")
        B.root_json(folder / "original-before-recovery.json", {"fate": fate_before})
        overlay = B.apply_overlay_bl6(sut_root / "store", scenario.get("overlay"), ids)
        B.root_json(folder / "overlay.json", {"applied": overlay})
        # ---- G6 pre-ops (receipted) ----
        recover_key = ids["logical_operation_id"]
        if "remove_task_json" in unit["pre_ops"]:
            removed = (sut_root / "store/task.json").read_bytes()
            (sut_root / "store/task.json").unlink()
            facts["e8"]["pre_ops"].append({"op": "remove_task_json", "removed_sha256": e8lib.sha_bytes(removed), "utc_ns": time.time_ns()})
        if "recovery_key_override_with_seeded_commit" in unit["pre_ops"]:
            recover_key = ids["recovery_logical_operation_id"]
            seed = B.start(str(public / "tools/seed_effect.py"), [str(db), recover_key, label + "-other-a1", label + "-other-t1"],
                           "ecr-adapter", public / "tools", folder / "seed-effect")
            if seed.wait(timeout=20) != 0:
                raise B.InfraError("seed_effect failed")
            facts["e8"]["pre_ops"].append({"op": "seed_commit_and_override_key", "key": recover_key, "utc_ns": time.time_ns(),
                                           "seed_stdout": (folder / "seed-effect.stdout").read_text()})
        bridge, arms_root = B.BRIDGE[manifest["planner_route"]]
        recover_config = {**common, "logical_operation_id": recover_key, "policy": unit["policy"],
                          "node": str(public / "tools/planner_recorder.py"), "plan_bridge": str(public / bridge),
                          "arm_dir": str(public / arms_root / "FULL")}
        B.root_json(sut_root / "recover-configuration.json", recover_config, "ecr-sut")
        recorder_env = {"ECR_REAL_NODE": str(B.NODE), "ECR_NODE_SHA256": manifest["node_binary_sha256"],
                        "ECR_PLANNER_RECORD_DIR": str(sut_root / "planner-calls"), "ECR_UNIT_ID": unit["unit_id"],
                        "ECR_ATTEMPT_ID": attempt["attempt_id"], "ECR_CODE_IDENTITY": attempt["manifest_sha256"],
                        "ECR_CODE_MANIFEST": str(public / MANIFEST_NAME), "ECR_CODE_ROOT": str(public),
                        "ECR_NODE_STAT": B.NODE_STAT, "ECR_PLANNER_ROUTE": manifest["planner_route"]}
        if manifest["planner_route"] == "J":
            recorder_env.update(ECR_JS_MANIFEST=str(public / "arms_js/strip-manifest.json"),
                                ECR_JS_IDENTITY=B.sha(public / "arms_js/strip-manifest.json"))
        # ---- recoveries ----
        total = len(unit["recoveries"])
        late = bool((unit.get("fi") or {}).get("late_release_first_recovery_send"))
        for plan in unit["recoveries"]:
            k = plan["index"]
            env = dict(recorder_env)
            if plan["crash_at"]:
                env.update(ECR_TEST_CRASH_AT=plan["crash_at"], ECR_TEST_CRASH_SCOPE="recovery")
            sends_before = e8lib.count_recovery_sends(events)
            name = f"agent-recover-{k}"
            start_ns = time.time_ns()
            proc = processes[name] = B.start(agent, ["recover", str(sut_root / "recover-configuration.json")], "ecr-sut",
                                             agent_cwd, folder / name, env)
            try:
                code = proc.wait(timeout=B.RECOVERY_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                facts["infra_events"].append(f"{name} timeout after {B.RECOVERY_TIMEOUT_S}s")
                code = None
            end_ns = time.time_ns()
            stderr = (folder / f"{name}.stderr").read_text(errors="replace")
            exc = e8lib.exception_type(stderr)
            outcome = e8lib.process_outcome(plan, code, exc, fired_hook=(code == 137))
            rec = {"index": k, "crash_at": plan["crash_at"] if code == 137 else None, "planned_crash_at": plan["crash_at"],
                   "exit_code": code, "exception_type": exc, "process_outcome": outcome, "start_utc_ns": start_ns, "end_utc_ns": end_ns}
            facts["e8"]["recoveries"].append(rec)
            # audit snapshot after every recovery, then archive final.json only between recoveries
            facts["e8"]["snapshots"].append(e8lib.snapshot(sut_root / "store", audit, k, root_bytes))
            facts["e8"]["archive"].append(e8lib.archive_final(sut_root / "store", k, total))
            if k < total and plan["crash_at"] in ("after_request", "after_response") and not late:
                ok = e8lib.wait_until(lambda: e8lib.count_recovery_sends(events) > sends_before, SYNC_S)
                facts["e8"]["sync"].append({"after_recovery": k, "endpoint_send_logged": ok, "utc_ns": time.time_ns()})
                if not ok:
                    raise B.InfraError(f"W6/W7 sync: endpoint did not log the send of recovery {k}")
            if late and k == 1:
                held = lambda: sorted(events.glob("late-held-*.json"))
                ok = e8lib.wait_until(lambda: bool(held()), SYNC_S)
                facts["e8"]["w6_order_receipts"].append({"step": "held_marker_seen", "ok": ok, "utc_ns": time.time_ns()})
                if not ok:
                    raise B.InfraError("W6': held request not delivered to the endpoint")
        facts["recovery_exit"] = facts["e8"]["recoveries"][-1]["exit_code"]
        if late:
            B.release(adapter_root / "control", "release-late")
            facts["e8"]["w6_order_receipts"].append({"step": "release_late_written", "utc_ns": time.time_ns()})
            tid = json.loads(sorted(events.glob("late-held-*.json"))[0].read_text())["transport_request_id"]
            def held_logged():
                for p in events.glob("log-*-send.json"):
                    try:
                        if json.loads(p.read_text())["request"].get("transport_request_id") == tid:
                            return True
                    except (OSError, ValueError, KeyError):
                        pass
                return False
            ok = e8lib.wait_until(held_logged, SYNC_S)
            facts["e8"]["w6_order_receipts"].append({"step": "held_send_logged", "ok": ok, "utc_ns": time.time_ns()})
            if not ok:
                raise B.InfraError("W6': released request was not logged by the endpoint")
        B.release(adapter_root / "control", "stop")
        try:
            endpoint_code = endpoint.wait(timeout=15)
        except subprocess.TimeoutExpired:
            raise B.InfraError("endpoint did not stop")
        if endpoint_code != 0 or list(events.glob("error-*.json")):
            facts["infra_events"].append(f"endpoint error or nonzero exit ({endpoint_code})")
        # Erratum E8-E1 (B3): runner receipts and audit snapshots are written BEFORE scoring, because e8_diag.py and
        # w6_order.py read them (diag/receipts.json, diag/audit/snapshots.json). Each file is written exactly once.
        write_runner_evidence(folder, audit, facts, written)
        facts.update(score_attempt_e8(public, unit, label, folder, sut_root, adapter_root, ids, db))
        scored = True
    except B.IntegrityError as error:
        facts["harness_integrity_ok"] = False
        facts["errors"].append(f"IntegrityError: {error}")
    except B.InfraError as error:
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
            for name in ("allow-accept", "release-B3", "release-B4", "release-late", "stop"):
                B.release(adapter_root / "control", name)
            try:
                cleanup["endpoint_exit_status"] = endpoint.wait(timeout=15)
            except subprocess.TimeoutExpired:
                endpoint.kill()
                cleanup["endpoint_residue_killed"] = endpoint.wait(timeout=5)
        facts["cleanup"] = cleanup
    # failure before scoring: write whatever runner evidence was not written yet (never a second time)
    write_runner_evidence(folder, audit, facts, written)
    if not scored and facts["sut_started"]:
        try:
            facts["failed_attempt_observation"] = score_attempt_e8(public, unit, label, folder, sut_root, adapter_root, ids, db, suffix="-after-failure")
        except Exception as error:
            facts["failed_attempt_observation"] = {"error": f"{type(error).__name__}: {error}"}
    state, reasons = p3lib.evaluate_attempt(facts)
    unexpected = [r["index"] for r in facts["e8"]["recoveries"] if r["process_outcome"] == "UNEXPECTED"]
    if unexpected and state in ("OBSERVED_PENDING_REPLAY", "VALID_COMPLETED"):
        state = "INFRA_FAILED" if facts["infra_events"] else "INTEGRITY_UNKNOWN"
        reasons = reasons + [f"G-K-proc: unexpected process outcome in recovery {unexpected}"]
    facts.update(state=state, state_reasons=reasons)
    B.root_json(folder / "attempt-result.json", facts)
    return facts


def write_runner_evidence(folder: Path, audit: Path, facts: dict, written: dict) -> None:
    """diag/receipts.json and diag/audit/snapshots.json from the runner's own receipts; each written once (O_EXCL)."""
    if not written["receipts"]:
        root_bytes(folder / "diag" / "receipts.json", json.dumps({"recoveries": facts["e8"]["recoveries"], "pre_ops": facts["e8"]["pre_ops"],
                                                                "w6_order_receipts": facts["e8"]["w6_order_receipts"]}, indent=1).encode())
        written["receipts"] = True
    if not written["snapshots"]:
        root_bytes(audit / "snapshots.json", json.dumps(facts["e8"]["snapshots"], indent=1).encode())
        written["snapshots"] = True


def score_attempt_e8(public, unit, label, folder, sut_root, adapter_root, ids, db, suffix="") -> dict:
    """Scoring view: diag/ (normal) or diag-after-failure/ (failed attempt). The runner receipts and audit snapshots
    must already exist in diag/ (write_runner_evidence); no placeholder is ever written (Erratum E8-E1, B3)."""
    view = folder / ("diag" if not suffix else "diag" + suffix)
    if suffix:
        B.directory(view, "root", "ecr-scorer", 0o750)
        shutil.copytree(folder / "diag" / "audit", view / "audit")
        root_bytes(view / "receipts.json", (folder / "diag" / "receipts.json").read_bytes())
    elif not ((view / "receipts.json").is_file() and (view / "audit" / "snapshots.json").is_file()):
        raise RuntimeError("runner evidence must be written before scoring (diag/receipts.json, diag/audit/snapshots.json)")
    B.copy_tree(sut_root, view / "sut")
    B.copy_tree(adapter_root / "events", view / "endpoint_events")
    root_bytes(view / "ledger.sqlite3", db.read_bytes())
    B.copy_tree(sut_root / "planner-calls", folder / f"planner-calls{suffix}")
    actions = [json.loads(p.read_text()) for p in sorted((view / "sut/store/recovery").glob("*.json"))]
    plan_records = sum(a.get("action") == "PLAN" for a in actions)
    records = planner_records.check(folder / f"planner-calls{suffix}", plan_records)
    B.root_json(folder / f"planner-records-check{suffix}.json", records)
    context = {"run_id": label, "arm": unit["unit_id"], "truth": unit["truth"], "logical_operation_id": ids["logical_operation_id"],
               "ledger": str(db), "endpoint_events": str(adapter_root / "events"), "sut_copy": str(view / "sut"),
               "injection": str(folder / "injection-receipt.json")}
    primary = B.score(public, "scorer_v2.py", context, folder, f"primary-scorer_v2{suffix}")
    expected = set(unit.get("expected_scorer_faults", []))
    faults = [f"primary:{f}" for f in primary["faults"] if f not in expected]
    diag = run_tool(public, "tools/e8_diag.py", [str(view), str(folder / "cell.json"), str(folder / f"primary-scorer_v2{suffix}-score.json")],
                    folder, f"e8-diag{suffix}")
    w6 = run_tool(public, "tools/w6_order.py", [str(view)], folder, f"w6-order{suffix}") if unit["window"] == "W6'" else None
    return {"final_state_present": (view / "sut/store/final.json").is_file(), "planner_records": records["status"],
            "planner_record_reasons": records["reasons"], "planner_failures": len(records["failures"]), "planner_calls": len(records["calls"]),
            "agent_plan_records": plan_records, "scorer_faults": faults,
            "expected_scorer_faults_recorded_not_gating": sorted(set(primary["faults"]) & expected),
            "primary_score": primary, "e8_diag": diag, "w6_order": w6}


def run_tool(public: Path, rel: str, args: list[str], folder: Path, label: str) -> dict:
    process = B.start(str(public / rel), args, "ecr-scorer", public, folder / label)
    code = process.wait(timeout=60)
    if code != 0 or (folder / f"{label}.stderr").read_bytes():
        raise B.InfraError(f"{label} failed: " + (folder / f"{label}.stderr").read_text()[-500:])
    out = json.loads((folder / f"{label}.stdout").read_text())
    B.root_json(folder / f"{label}.json", out)
    return out


def cmd_run(args, manifest: dict, manifest_sha: str) -> int:
    cells_path = HERE / "private/cells_e8.json"
    if B.sha(cells_path) != manifest["files"]["private/cells_e8.json"]:
        raise RuntimeError("cell table drift")
    cells = json.loads(cells_path.read_text())
    if args.set == "VQ":
        units = cells["qualification_units"]
    else:
        authorization = json.loads((HERE / "qualification-authorization-e8.json").read_text())
        if authorization.get("status") != "PASS_HOST_AUDIT" or authorization.get("manifest_sha256") != manifest_sha:
            raise RuntimeError("E8 qualification authorization missing or for another manifest")
        units = cells["units"]
    extra_allowed, finalized = set(), {}
    if args.continuation:
        cont = json.loads(Path(args.continuation).read_text())
        if cont.get("manifest_sha256") != manifest_sha or cont.get("approved") is not True:
            raise RuntimeError("Continuation authorization missing approval or for another manifest")
        extra_allowed = set(cont.get("extra_attempt_unit_ids", []))
        finalized = cont.get("finalized_attempt_states", {})
    if not B.BATCH_PARENT.exists():
        B.directory(B.BATCH_PARENT, "root", "ecr-scorer", 0o750)
    batch = B.BATCH_PARENT / args.batch_id
    B.directory(batch, "root", "ecr-scorer", 0o750)
    B.directory(batch / "attempts", "root", "ecr-scorer", 0o750)
    public = deploy_e8(args.batch_id, manifest)
    roots = {"sut": B.BASE / "sut-state" / ("e8-" + args.batch_id), "adapter": B.BASE / "ledger" / ("e8-" + args.batch_id)}
    B.directory(roots["sut"], "root", "ecr-sut", 0o750)
    B.directory(roots["adapter"], "ecr-adapter", "ecr-ledger-readers", 0o750)
    B.root_json(batch / "execution-manifest.json", manifest)
    B.root_json(batch / "environment.json", {"python": sys.version, "kernel": B.platform.release(), "sqlite": B.sqlite3.sqlite_version,
                "node_sha256": B.sha(B.NODE), "set": args.set, "continuation": args.continuation, "manifest_sha256": manifest_sha})
    rows, status = [], "COMPLETED_PENDING_REPLAY"
    for unit in units:
        key = index_unit_id(unit["unit_id"])
        history = B.attempts_of(key, finalized)
        states = [h["state"] for h in history]
        allowed, why = p3lib.may_add_attempt(states, unit["unit_id"] in extra_allowed)
        if not allowed:
            rows.append({"unit_id": unit["unit_id"], "run_in_batch": False, "reason": why, "unit_state": p3lib.unit_state(states)})
            continue
        retry_of = history[-1]["attempt_id"] if history else None
        attempt = B.claim_attempt(key, len(history) + 1, args.batch_id, manifest_sha, retry_of, why)
        try:
            facts = run_attempt_e8(public, manifest, unit, attempt, batch, roots)
        except Exception as error:
            facts = {"state": "INTEGRITY_UNKNOWN", "state_reasons": [f"runner aborted attempt: {type(error).__name__}: {error}"]}
            result = batch / "attempts" / p3lib.attempt_dirname(attempt["attempt_id"]) / "attempt-result.json"
            if result.parent.is_dir() and not result.exists():
                B.root_json(result, {**attempt, **facts})
        rows.append({"unit_id": unit["unit_id"], "index_unit_id": key, "run_in_batch": True, "attempt_id": attempt["attempt_id"],
                     "state": facts["state"], "state_reasons": facts["state_reasons"]})
        print(f"{attempt['attempt_id']}={facts['state']}", flush=True)
        if p3lib.batch_should_stop(facts["state"]):
            status = "STOPPED_INVESTIGATE"
            break
    done = {r["unit_id"] for r in rows}
    rows.extend({"unit_id": u["unit_id"], "run_in_batch": False, "reason": "batch stopped before this unit"} for u in units if u["unit_id"] not in done)
    # execution safety: a fresh batch (no continuation) must start every unit; a skipped unit means the attempt index
    # already held it (wrong lane or reused batch) and the batch must not look complete
    if status == "COMPLETED_PENDING_REPLAY" and not args.continuation and any(r.get("run_in_batch") is not True for r in rows):
        status = "INCOMPLETE_UNITS_SKIPPED"
    summary = {"batch_id": args.batch_id, "set": args.set, "status": status, "units": rows,
               "attempts_started": sum(r.get("run_in_batch") is True for r in rows), "manifest_sha256": manifest_sha, "utc_ns": time.time_ns(),
               "note": "OBSERVED_PENDING_REPLAY attempts become VALID_COMPLETED only after same- and cross-runtime replay"}
    B.root_json(batch / "batch-summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "units"}), flush=True)
    return 0 if status == "COMPLETED_PENDING_REPLAY" else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--set", choices=["VQ", "E8"], required=True)
    run.add_argument("--batch-id", required=True)
    run.add_argument("--continuation")
    rep = sub.add_parser("replay")
    rep.add_argument("--batch-id", required=True)
    args = parser.parse_args()
    manifest_path = HERE / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest_sha = B.sha(manifest_path)
    B.guard(manifest)
    st = B.NODE.stat()
    B.NODE_STAT = f"{st.st_size}:{st.st_mtime_ns}"
    os.umask(0o027)
    if not args.batch_id.replace("-", "").replace("_", "").isalnum():
        raise RuntimeError("unsafe batch id")
    set_lane(args.batch_id, args.set if args.command == "run" else None)
    raise SystemExit((cmd_run if args.command == "run" else B.cmd_replay)(args, manifest, manifest_sha))


if __name__ == "__main__":
    main()
