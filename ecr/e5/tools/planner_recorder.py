#!/usr/bin/python3 -I
"""Planner-call recorder (phase 3). The runner sets the agents' configured `node` executable to this
file, so every planner call of the frozen v1 agent and of agent_v2 passes through it unchanged:
    argv[1:] = [plan_bridge, arm_dir], stdin = planner input JSON (bytes as sent by the agent).

For each call it writes, in ECR_PLANNER_RECORD_DIR, before and after invoking the real Node:
    call-NNNN.input.bin      raw stdin bytes                       (written first)
    call-NNNN.started.json   identities, argv, route, hashes        (written before the call)
    call-NNNN.stdout.bin     raw stdout bytes of the real Node
    call-NNNN.stderr.bin     raw stderr bytes
    call-NNNN.finished.json  exit code, signal, timeout, hashes, reported runtime version (written last)
and then relays stdout, stderr and the exit status to the caller unchanged (timeout -> 124,
death by signal s -> 128+s). A call whose finished record is missing or whose hashes do not
match is incomplete; planner_records.check() turns that into INTEGRITY_UNKNOWN.

Identity binding (candidate v4; records written by earlier versions carry no such binding and are
never back-filled). Code identity and input identity are separate:
  * code identity  = SHA-256 of the code manifest file ECR_CODE_MANIFEST (a candidate manifest or
                     the execution manifest; {"files": {relative path: sha256}}), which must equal
                     ECR_CODE_IDENTITY. Before every call the recorder hashes the files that call
                     executes: the bridge (argv[0]) and, for route T, the arm's two .ts files; their
                     paths relative to ECR_CODE_ROOT must be listed in the manifest with equal hashes.
  * generated JS   = route J: ECR_JS_MANIFEST (strip-manifest.json of strip_ts.mjs) must hash to
                     ECR_JS_IDENTITY, be COMPLETED, be produced by the Node binary ECR_NODE_SHA256,
                     list for this arm the hashes of the .js/package.json files actually present,
                     and record as its TS inputs exactly the hashes the code manifest lists for
                     arms/<arm>/*.ts. If the code manifest also lists the arm's JS files (execution
                     manifest), those entries must match too.
  * Node           = ECR_NODE_SHA256 (full hash, verified by the caller before and after a run) and
                     ECR_NODE_STAT "<size>:<mtime_ns>" checked against the binary on every call.
  * input identity = per-call SHA-256 of stdin (always); ECR_INPUT_IDENTITY optionally names a fixed
                     input set (qualification). It is never used as a code identity.
Every executed file and its hash is written to started.json ("executed_files"), so replay can refuse
to re-run against different code. Any identity mismatch is recorded (identity_check), Node is not
started, and the recorder exits 7.

Required environment: ECR_REAL_NODE, ECR_NODE_SHA256, ECR_NODE_STAT, ECR_PLANNER_RECORD_DIR,
ECR_UNIT_ID, ECR_ATTEMPT_ID, ECR_CODE_IDENTITY, ECR_CODE_MANIFEST, ECR_CODE_ROOT, ECR_PLANNER_ROUTE
("J" -> bridge must be plan_js.mjs, also ECR_JS_MANIFEST and ECR_JS_IDENTITY; "T" -> plan.mjs).
Optional: ECR_INPUT_IDENTITY, ECR_PLANNER_TIMEOUT_S (default 15, below the agent's own 20 s limit).
Exit codes without calling Node: 6 missing environment, 5 route mismatch, 7 identity mismatch
(5 and 7 are recorded so that they cannot pass silently).
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REQUIRED = ("ECR_REAL_NODE", "ECR_NODE_SHA256", "ECR_NODE_STAT", "ECR_PLANNER_RECORD_DIR", "ECR_UNIT_ID",
            "ECR_ATTEMPT_ID", "ECR_CODE_IDENTITY", "ECR_CODE_MANIFEST", "ECR_CODE_ROOT", "ECR_PLANNER_ROUTE")
REQUIRED_J = ("ECR_JS_MANIFEST", "ECR_JS_IDENTITY")
BRIDGE = {"J": "plan_js.mjs", "T": "plan.mjs"}
TS_FILES = ("delivery-reconciliation.ts", "delivery-reconciliation-execution.ts")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def node_stat(path: str) -> str:
    st = os.stat(path)
    return f"{st.st_size}:{st.st_mtime_ns}"


def write_exclusive(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o640)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    if hasattr(os, "O_DIRECTORY"):
        dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def jdump(value) -> bytes:
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def identity_check(env: dict, argv: list[str], route: str) -> tuple[list[str], dict]:
    """Returns (problems, executed_files {absolute path: sha256}). Empty problems = identity OK."""
    problems, executed = [], {}
    raw = Path(env["ECR_CODE_MANIFEST"]).read_bytes()
    if sha(raw) != env["ECR_CODE_IDENTITY"]:
        return ["code manifest hash differs from ECR_CODE_IDENTITY"], executed
    files = json.loads(raw)["files"]
    root = Path(env["ECR_CODE_ROOT"]).resolve()

    def check_listed(path: Path, what: str) -> None:
        digest = sha(path.read_bytes())
        executed[str(path)] = digest
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            problems.append(f"{what} {path} is outside the code root")
            return
        if files.get(rel) != digest:
            problems.append(f"{what} {rel} not in code manifest or hash differs")

    if len(argv) != 2:
        return [f"expected argv [bridge, arm_dir], got {len(argv)} arguments"], executed
    check_listed(Path(argv[0]).resolve(), "bridge")
    arm_dir = Path(argv[1]).resolve()
    arm = arm_dir.name
    if route == "T":
        for name in TS_FILES:
            check_listed(arm_dir / name, "TS source")
    else:
        jraw = Path(env["ECR_JS_MANIFEST"]).read_bytes()
        if sha(jraw) != env["ECR_JS_IDENTITY"]:
            return problems + ["strip manifest hash differs from ECR_JS_IDENTITY"], executed
        jm = json.loads(jraw)
        if jm.get("status") != "COMPLETED" or jm.get("node_binary_sha256") != env["ECR_NODE_SHA256"]:
            problems.append("strip manifest not COMPLETED or produced by another Node binary")
        entry = (jm.get("arms") or {}).get(arm)
        if entry is None:
            return problems + [f"strip manifest has no arm {arm}"], executed
        for name, digest in entry["outputs"].items():
            path = arm_dir / name
            actual = sha(path.read_bytes()) if path.is_file() else None
            executed[str(path)] = actual
            if actual != digest:
                problems.append(f"generated file {arm}/{name} differs from strip manifest")
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = None
            if rel is not None and rel in files and files[rel] != actual:
                problems.append(f"generated file {rel} differs from code manifest")
        for name, digest in entry["inputs"].items():
            if files.get(f"arms/{arm}/{name}") != digest:
                problems.append(f"strip input arms/{arm}/{name} not in code manifest or hash differs")
    if node_stat(env["ECR_REAL_NODE"]) != env["ECR_NODE_STAT"]:
        problems.append("Node binary size/mtime differs from ECR_NODE_STAT")
    return problems, executed


def main() -> int:
    route = os.environ.get("ECR_PLANNER_ROUTE", "")
    needed = REQUIRED + (REQUIRED_J if route == "J" else ())
    missing = [k for k in needed if not os.environ.get(k)]
    if missing:
        sys.stderr.write("planner_recorder: missing environment " + ",".join(missing) + "\n")
        return 6
    env = {k: os.environ[k] for k in needed}
    record_dir = Path(env["ECR_PLANNER_RECORD_DIR"])
    argv = sys.argv[1:]
    raw_input = sys.stdin.buffer.read()

    # allocate a call number exclusively (no reuse, no overwrite)
    n = 1
    while True:
        prefix = record_dir / f"call-{n:04d}"
        try:
            write_exclusive(Path(str(prefix) + ".input.bin"), raw_input)
            break
        except FileExistsError:
            n += 1

    bridge = Path(argv[0]).name if argv else ""
    started = {"call_id": f"call-{n:04d}", "unit_id": env["ECR_UNIT_ID"], "attempt_id": env["ECR_ATTEMPT_ID"],
               "code_identity": env["ECR_CODE_IDENTITY"], "code_manifest": env["ECR_CODE_MANIFEST"],
               "code_root": env["ECR_CODE_ROOT"], "input_identity": os.environ.get("ECR_INPUT_IDENTITY") or None,
               "js_identity": env.get("ECR_JS_IDENTITY"), "js_manifest": env.get("ECR_JS_MANIFEST"),
               "route": route, "argv": argv, "real_node": env["ECR_REAL_NODE"],
               "node_sha256_expected": env["ECR_NODE_SHA256"], "node_stat_expected": env["ECR_NODE_STAT"],
               "input_sha256": sha(raw_input), "input_bytes": len(raw_input),
               "utc_ns_start": time.time_ns(), "recorder_pid": os.getpid(), "identity_binding": "v4"}
    route_ok = BRIDGE.get(route) == bridge
    started["route_check"] = "OK" if route_ok else f"MISMATCH: route {route} expects {BRIDGE.get(route)}, got {bridge}"
    problems, executed = [], {}
    if route_ok:
        try:
            problems, executed = identity_check(env, argv, route)
        except (OSError, ValueError, KeyError, TypeError) as error:
            problems = [f"identity check could not be completed: {type(error).__name__}: {error}"]
    started["identity_check"] = ("NOT_RUN_ROUTE_MISMATCH" if not route_ok else "MISMATCH" if problems else "OK")
    started["identity_problems"] = problems
    started["executed_files"] = executed
    write_exclusive(Path(str(prefix) + ".started.json"), jdump(started))
    if not route_ok or problems:
        message = (started["route_check"] if not route_ok else "identity mismatch: " + "; ".join(problems)).encode()
        code = 5 if not route_ok else 7
        write_exclusive(Path(str(prefix) + ".stdout.bin"), b"")
        write_exclusive(Path(str(prefix) + ".stderr.bin"), message)
        write_exclusive(Path(str(prefix) + ".finished.json"), jdump(
            {"call_id": started["call_id"], "exit_code": code, "signal": None, "timed_out": False,
             "route_rejected": not route_ok, "identity_rejected": route_ok,
             "stdout_sha256": sha(b""), "stdout_bytes": 0, "stderr_sha256": sha(message), "stderr_bytes": len(message),
             "runtime_version_reported": None, "utc_ns_end": time.time_ns()}))
        sys.stderr.write("planner_recorder: " + message.decode(errors="replace") + "\n")
        return code

    timeout = float(os.environ.get("ECR_PLANNER_TIMEOUT_S", "15"))
    t0 = time.monotonic()
    timed_out, signal_no = False, None
    try:
        proc = subprocess.run([env["ECR_REAL_NODE"], *argv], input=raw_input, capture_output=True, timeout=timeout)
        out, err, code = proc.stdout, proc.stderr, proc.returncode
        if code < 0:
            signal_no = -code
    except subprocess.TimeoutExpired as e:
        out, err, code, timed_out = e.stdout or b"", e.stderr or b"", None, True
    duration_ms = round((time.monotonic() - t0) * 1000, 3)
    write_exclusive(Path(str(prefix) + ".stdout.bin"), out)
    write_exclusive(Path(str(prefix) + ".stderr.bin"), err)
    reported = None
    try:
        parsed = json.loads(out)
        if isinstance(parsed, dict):
            reported = parsed.get("node")
    except (ValueError, UnicodeDecodeError):
        pass
    write_exclusive(Path(str(prefix) + ".finished.json"), jdump(
        {"call_id": started["call_id"], "exit_code": code, "signal": signal_no, "timed_out": timed_out,
         "route_rejected": False, "identity_rejected": False, "stdout_sha256": sha(out), "stdout_bytes": len(out),
         "stderr_sha256": sha(err), "stderr_bytes": len(err), "runtime_version_reported": reported,
         "duration_ms": duration_ms, "utc_ns_end": time.time_ns()}))
    sys.stdout.buffer.write(out)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(err)
    sys.stderr.buffer.flush()
    if timed_out:
        return 124
    if signal_no:
        return 128 + signal_no
    return code


if __name__ == "__main__":
    sys.exit(main())
