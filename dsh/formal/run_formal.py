#!/usr/bin/env python3
"""ECR experiment 3 formal batch runner (`dsh-20261013-r1`). No automatic reruns.

Installs the frozen profile into a fresh DSH_HOME, verifies the installed DSH and
the package against manifest.json, then runs every predeclared trial: start the
installed DSH (scripted model, no network), hard-kill it at the trial's kill point,
resume it, and retain raw artifacts. It does not score; score_formal.py does.
Usage: python run_formal.py <fresh_dsh_home>
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path(sys.argv[1])
APP = Path(os.environ["LOCALAPPDATA"]) / "Programs/DeepSeek Harness"
EXE = APP / "DeepSeek Harness.exe"
ASAR = APP / "resources/app.asar"
CLI = APP / "resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js"
NODE_MODULES = APP / "resources/app.asar/dsh/node_modules"
MANIFEST = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
PRE_HOLD_MS, PRE_KILL_AFTER_MS = 100, 20


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_identity() -> None:
    if sha(ASAR) != MANIFEST["dsh_app_asar_sha256"] or sha(EXE) != MANIFEST["dsh_exe_sha256"]:
        raise SystemExit("DSH installation changed (auto-update?); stop and re-pin")
    for rel, expected in MANIFEST["files"].items():
        if sha(HERE / rel) != expected:
            raise SystemExit("package file drift: " + rel)


def launch(run: Path, mode: str, trial: dict) -> subprocess.Popen:
    env = {**os.environ, "DSH_HOME": str(HOME), "ELECTRON_RUN_AS_NODE": "1", "ECR_DSH_NODE_MODULES": str(NODE_MODULES),
           "ECR_ARM": trial["arm"], "ECR_MODE": mode, "ECR_SESSION_ID": trial["trial_id"], "ECR_OPERATION_ID": "op-" + trial["trial_id"],
           "ECR_EFFECT_LEDGER": str(run / "effects.jsonl"), "ECR_SIGNAL_DIR": str(run / "signals"), "ECR_MODEL_POLICY": trial["policy"],
           "ECR_LATENCY_BEFORE_CALL_MS": "500", "ECR_LATENCY_AFTER_TOOL_MS": "2000",
           "ECR_TOOL_HOLD_MS": "1000" if trial["tool"] == "slow" else "0",
           "ECR_PRE_EFFECT_HOLD_MS": str(PRE_HOLD_MS) if trial["kill"] == "pre" and mode == "start" else "0"}
    out = open(run / f"{mode}.out", "xb")
    err = open(run / f"{mode}.err", "xb")
    return subprocess.Popen([str(EXE), "--expose-internals", str(CLI), "--profile", "ecrformal"], cwd=run, env=env, stdout=out, stderr=err)


def wait_marker(run: Path, proc: subprocess.Popen, pattern: str) -> bool:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and proc.poll() is None:
        if list((run / "signals").glob(pattern)):
            return True
        time.sleep(0.001)
    return bool(list((run / "signals").glob(pattern)))


def run_trial(trial: dict) -> dict:
    run = HOME / "runs" / trial["trial_id"]
    (run / "signals").mkdir(parents=True)
    proc = launch(run, "start", trial)
    pattern = "pre-effect-*.marker" if trial["kill"] == "pre" else "effect-*.marker"
    if not wait_marker(run, proc, pattern):
        proc.kill()
        return {**trial, "status": "BLOCKED_NO_MARKER", "start_exit": proc.wait()}
    seen = time.perf_counter_ns()
    delay = PRE_KILL_AFTER_MS if trial["kill"] == "pre" else int(trial["kill"])
    while (time.perf_counter_ns() - seen) < delay * 1_000_000:
        time.sleep(0.0005)
    exited_before = proc.poll() is not None
    proc.kill()
    killed = time.perf_counter_ns()
    start_exit = proc.wait(timeout=10)
    effects_at_kill = len((run / "effects.jsonl").read_text(encoding="utf-8").splitlines()) if (run / "effects.jsonl").exists() else 0
    session_files = [p for p in (HOME / "sessions").rglob("session.v*.jsonl") if p.parent.name == trial["trial_id"]]
    if len(session_files) == 1:
        shutil.copyfile(session_files[0], run / "session-at-kill.jsonl")
    resume = launch(run, "resume", trial)
    try:
        resume_exit = resume.wait(timeout=90)
    except subprocess.TimeoutExpired:
        resume.kill()
        resume_exit = "TIMEOUT"
    if len(session_files) == 1:
        shutil.copyfile(session_files[0], run / "session-final.jsonl")
    status = "OBSERVED" if not exited_before and start_exit != 0 and resume_exit == 0 else "FAIL"
    return {**trial, "status": status, "kill_after_marker_ms": round((killed - seen) / 1e6, 1), "process_exited_before_kill": exited_before,
            "start_exit": start_exit, "resume_exit": resume_exit, "effects_at_kill": effects_at_kill, "run_dir": str(run)}


def main() -> None:
    if HOME.exists():
        raise SystemExit("DSH_HOME must be fresh; no reuse or overwrite")
    check_identity()
    profile = HOME / "profiles/ecrformal"
    profile.mkdir(parents=True)
    for name in ("package.json", "cordis.patch.yml"):
        shutil.copyfile(HERE / "profile" / name, profile / name)
    trials = MANIFEST["trials"]
    rows, status = [], "COMPLETED_REQUIRES_AUDIT"
    for index, trial in enumerate(trials, 1):
        if index % 21 == 1:
            check_identity()
        row = run_trial(trial)
        rows.append(row)
        print(f"{trial['trial_id']}={row['status']}", flush=True)
        if row["status"] != "OBSERVED":
            status = "FAIL_RETAIN_EVIDENCE"
            break
    check_identity()
    done = {r["trial_id"] for r in rows}
    rows.extend({**t, "status": "NOT_EXECUTED"} for t in trials if t["trial_id"] not in done)
    summary = {"batch_id": MANIFEST["batch_id"], "status": status, "dsh_home": str(HOME), "trials": rows,
               "observed": sum(r["status"] == "OBSERVED" for r in rows), "finished_utc": time.time()}
    (HOME / "batch-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "trials"}))


if __name__ == "__main__":
    main()
