"""Replay of recorded planner calls (phase 3): same-runtime and cross-runtime consistency checks.

For every recorded call (planner_recorder.py format) the recorded input bytes are fed again to
a given Node binary with the recorded bridge and arm directory (paths can be mapped for a
different host) and the new stdout is compared with the recorded stdout. Two comparisons are
recorded separately and nothing is normalised beyond what is stated:
  raw          : stdout bytes identical (expected for same-runtime replay)
  semantic     : both stdouts parse to exactly one object with keys {"node","plan"}; the "plan"
                 values are equal as parsed JSON; in addition the key order of every object in
                 "plan" is compared and a difference is reported (not hidden); the "node" field
                 (runtime version string) is the only field allowed to differ, and only in
                 cross-runtime mode.
Per-call verdicts
  IDENTICAL_BYTES                         raw equal
  EQUAL_EXCEPT_RUNTIME_VERSION            cross-runtime only: plan equal incl. key order, node field differs
  KEY_ORDER_DIFFERS                       plan equal as data, key order differs           -> STOP
  PLAN_MISMATCH                           plan values differ                               -> STOP
  BYTES_DIFFER_SAME_RUNTIME               same-runtime mode, semantic equal, bytes differ  -> STOP
  REPLAY_FAILED                           replay exit != 0, signal, timeout or unparseable -> STOP
  ORIGINAL_UNPARSEABLE                    recorded successful call was not well formed     -> STOP
  ORIGINAL_FAILED_NOW_SUCCEEDS / ORIGINAL_FAILED_STILL_FAILS   for calls that failed when recorded (reported)
  CODE_IDENTITY_MISMATCH                  a file the call executed (started.json "executed_files",
                                          recorded since candidate v4) is missing or has another hash
                                          at the (mapped) path; the call is NOT re-run      -> STOP
  RUNTIME_IDENTITY_MISMATCH               same-runtime mode, but the replay Node binary's SHA-256 is not
                                          the one recorded for the call; NOT re-run          -> STOP
Identity: records written before candidate v4 have no "executed_files"; they are replayed and marked
identity = UNVERIFIABLE_RECORD_PREDATES_BINDING (never back-filled). Records with the binding are
marked VERIFIED only after every executed file was re-hashed at its (mapped) path.
Overall status: "CONSISTENT" or "STOP_INVESTIGATE" (any STOP verdict). Consistency across runtimes
is not a proof of correctness: both runtimes can share a defect or receive the same wrong input.

Evidence (added after VM qualification Q2 run 1, where two REPLAY_FAILED events left no per-call
output): with --evidence-dir (a new directory) every replayed call stores its raw replay stdout,
raw stderr and an execution record (argv, cwd, exit code, signal, timeout, duration, hashes,
source record) as NNNN.stdout.bin / NNNN.stderr.bin / NNNN.execution.json, written before the
verdict is computed. Every report row also carries the replay output hashes and its source record.

Usage: python -I replay.py --records DIR [DIR ...] --node NODE --mode same|cross --out REPORT.json|-
                           [--evidence-dir NEW_DIR] [--map OLD_PREFIX=NEW_PREFIX ...] [--timeout 15]
("--out -" prints the full report to stdout; used by the runner, which stores it as root.)
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def key_order(value):
    if isinstance(value, dict):
        return [[k, key_order(v)] for k, v in value.items()]
    if isinstance(value, list):
        return [key_order(v) for v in value]
    return None


def parse(raw: bytes):
    try:
        v = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(v, dict) and set(v) == {"node", "plan"} and isinstance(v["plan"], dict):
        return v
    return None


def remap(path: str, maps: list[tuple[str, str]]) -> str:
    for old, new in maps:
        if path.startswith(old):
            return new + path[len(old):]
    return path


def write_new(path: Path, data: bytes) -> None:
    with open(path, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def replay_call(prefix: Path, node: str, mode: str, maps, timeout: float, evidence: Path | None = None, seq: int = 0,
                node_sha: str | None = None) -> dict:
    started = json.loads(Path(str(prefix) + ".started.json").read_bytes())
    finished = json.loads(Path(str(prefix) + ".finished.json").read_bytes())
    raw_in = Path(str(prefix) + ".input.bin").read_bytes()
    rec_out = Path(str(prefix) + ".stdout.bin").read_bytes()
    argv = [remap(a, maps) for a in started["argv"]]
    cwd = argv[1] if len(argv) > 1 else None
    row = {"seq": seq, "source_record": str(prefix), "call_id": started["call_id"], "attempt_id": started["attempt_id"],
           "unit_id": started["unit_id"], "input_sha256": hashlib.sha256(raw_in).hexdigest(),
           "recorded_exit": finished.get("exit_code"), "code_identity": started.get("code_identity"),
           "js_identity": started.get("js_identity")}
    executed = started.get("executed_files")
    if executed is None:
        row["identity"] = "UNVERIFIABLE_RECORD_PREDATES_BINDING"
    else:
        mismatches = []
        for path, digest in executed.items():
            target = Path(remap(path, maps))
            actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
            if actual != digest:
                mismatches.append({"path": str(target), "recorded": digest, "found": actual})
        row["identity"] = "MISMATCH" if mismatches else "VERIFIED"
        row["identity_mismatches"] = mismatches
    expected_node = started.get("node_sha256_expected")
    row["runtime_identity"] = ("UNRECORDED" if not expected_node else "SAME" if expected_node == node_sha else "DIFFERENT")
    refused = None
    if row["identity"] == "MISMATCH":
        refused = "CODE_IDENTITY_MISMATCH"
    elif mode == "same" and row["runtime_identity"] == "DIFFERENT":
        refused = "RUNTIME_IDENTITY_MISMATCH"
    if refused:
        row["verdict"] = refused
        if evidence is not None:
            base = evidence / f"{seq:04d}"
            write_new(Path(str(base) + ".execution.json"), (json.dumps(
                {**row, "node": node, "argv": argv, "cwd": cwd, "mode": mode, "executed": False}, sort_keys=True) + "\n").encode())
            row["evidence_prefix"] = str(base)
        return row
    t0 = time.monotonic()
    timed_out = False
    try:
        p = subprocess.run([node, *argv], input=raw_in, capture_output=True, timeout=timeout, cwd=cwd)
        new_out, new_err, new_code = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        new_out, new_err, new_code, timed_out = e.stdout or b"", e.stderr or b"", None, True
    row.update(replay_exit=new_code, replay_signal=-new_code if new_code is not None and new_code < 0 else None,
               replay_timed_out=timed_out, replay_duration_ms=round((time.monotonic() - t0) * 1000, 3),
               replay_stdout_sha256=hashlib.sha256(new_out).hexdigest(), replay_stdout_bytes=len(new_out),
               replay_stderr_sha256=hashlib.sha256(new_err).hexdigest(), replay_stderr_bytes=len(new_err))
    if evidence is not None:
        base = evidence / f"{seq:04d}"
        write_new(Path(str(base) + ".stdout.bin"), new_out)
        write_new(Path(str(base) + ".stderr.bin"), new_err)
        execution = {**row, "node": node, "argv": argv, "cwd": cwd, "mode": mode}
        write_new(Path(str(base) + ".execution.json"), (json.dumps(execution, sort_keys=True) + "\n").encode())
        row["evidence_prefix"] = str(base)
    original_ok = finished.get("exit_code") == 0 and not finished.get("timed_out") and not finished.get("signal")
    if not original_ok:
        row["verdict"] = "ORIGINAL_FAILED_NOW_SUCCEEDS" if (new_code == 0 and parse(new_out)) else "ORIGINAL_FAILED_STILL_FAILS"
        return row
    a, b = parse(rec_out), parse(new_out)
    if a is None:
        row["verdict"] = "ORIGINAL_UNPARSEABLE"
        return row
    if new_code != 0 or b is None:
        row["verdict"] = "REPLAY_FAILED"
        return row
    row["raw_equal"] = rec_out == new_out
    row["plan_equal"] = a["plan"] == b["plan"]
    row["key_order_equal"] = key_order(a["plan"]) == key_order(b["plan"])
    row["runtime_recorded"], row["runtime_replay"] = a["node"], b["node"]
    if row["raw_equal"]:
        row["verdict"] = "IDENTICAL_BYTES"
    elif not row["plan_equal"]:
        row["verdict"] = "PLAN_MISMATCH"
    elif not row["key_order_equal"]:
        row["verdict"] = "KEY_ORDER_DIFFERS"
    elif mode == "same":
        row["verdict"] = "BYTES_DIFFER_SAME_RUNTIME"
    elif a["node"] != b["node"]:
        row["verdict"] = "EQUAL_EXCEPT_RUNTIME_VERSION"
    else:
        row["verdict"] = "BYTES_DIFFER_SAME_RUNTIME"
    return row


STOP = {"KEY_ORDER_DIFFERS", "PLAN_MISMATCH", "BYTES_DIFFER_SAME_RUNTIME", "REPLAY_FAILED", "ORIGINAL_UNPARSEABLE",
        "CODE_IDENTITY_MISMATCH", "RUNTIME_IDENTITY_MISMATCH"}


def run(records: list[Path], node: str, mode: str, maps, timeout: float, evidence_dir: Path | None = None) -> dict:
    rows = []
    node_sha = hashlib.sha256(Path(node).read_bytes()).hexdigest()
    if evidence_dir is not None:
        Path(evidence_dir).mkdir()  # must be new: evidence is never overwritten
    seq = 0
    for d in records:
        for started in sorted(Path(d).glob("call-*.started.json")):
            prefix = Path(str(started)[: -len(".started.json")])
            seq += 1
            if not Path(str(prefix) + ".finished.json").exists():
                row = {"seq": seq, "source_record": str(prefix), "call_id": prefix.name, "verdict": "RECORD_INCOMPLETE"}
                try:  # attribute the incomplete record to its attempt when the started record is readable
                    s = json.loads(started.read_bytes())
                    row.update(attempt_id=s.get("attempt_id"), unit_id=s.get("unit_id"))
                except ValueError:
                    pass
                rows.append(row)
                continue
            rows.append(replay_call(prefix, node, mode, maps, timeout, Path(evidence_dir) if evidence_dir else None, seq,
                                    node_sha))
    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    stop = any(r["verdict"] in STOP or r["verdict"] == "RECORD_INCOMPLETE" for r in rows)
    identity_counts = {}
    for r in rows:
        identity_counts[r.get("identity", "NOT_CHECKED")] = identity_counts.get(r.get("identity", "NOT_CHECKED"), 0) + 1
    return {"mode": mode, "node": node, "node_sha256": node_sha, "calls": len(rows), "verdict_counts": counts,
            "identity_counts": identity_counts,
            "evidence_dir": str(evidence_dir) if evidence_dir else None,
            "status": "STOP_INVESTIGATE" if stop else "CONSISTENT",
            "note": "cross-runtime consistency is not an independent proof of correctness", "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", nargs="+", required=True)
    ap.add_argument("--node", required=True)
    ap.add_argument("--mode", choices=("same", "cross"), required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--map", action="append", default=[])
    ap.add_argument("--timeout", type=float, default=15)
    ap.add_argument("--evidence-dir")
    args = ap.parse_args()
    maps = [tuple(m.split("=", 1)) for m in args.map]
    if args.out != "-" and Path(args.out).exists():
        sys.exit(f"refusing to overwrite {args.out}")
    report = run([Path(r) for r in args.records], args.node, args.mode, maps, args.timeout,
                 Path(args.evidence_dir) if args.evidence_dir else None)
    if args.out == "-":
        print(json.dumps(report, sort_keys=True))
        sys.exit(0 if report["status"] == "CONSISTENT" else 1)
    out = Path(args.out)
    if out.exists():
        sys.exit(f"refusing to overwrite {out}")
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}))
    sys.exit(0 if report["status"] == "CONSISTENT" else 1)


if __name__ == "__main__":
    main()
