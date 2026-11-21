"""Planner qualification driver (phase 3): Q1 TS/JS behavioural equivalence, Q2 recorder fidelity,
Q4 finite-run stability. Q3 (replay) is run afterwards with replay.py on the record directories
this tool writes.

Fixed design (no early stop, no retry, no rerun inside the tool):
  * inputs: the fixed input set (planner_inputs.py), every input x every arm given;
  * route T (frozen plan.mjs + .ts) once, route J (plan_js.mjs + generated .js) `--reps` times;
    order: rep 1 (T then J), then J reps 2..N; arms and inputs in file order;
  * every call goes through tools/planner_recorder.py exactly as in the formal runner;
  * the run stops early only on a tool fault (recorder exit 5/6, a caller-visible output that
    differs from the recorded bytes, or an incomplete record set): then the result is
    TOOL_FAULT and nothing about the planner may be concluded.
Outputs (all new files in a new directory): records/<route>-r<rep>/<arm>/call-NNNN.*, summary.json.
The summary reports failures (non-zero exit, signal, timeout, empty or malformed stdout) by route,
arm and input; T-vs-J comparisons (raw bytes and parsed plan, separately); J repeat consistency.
Zero failures in n calls only bounds the per-call failure rate under these conditions
(one-sided 95 % upper bound 1 - 0.05**(1/n), about 3/n); it does not show the absence of failures
and does not cover formal-batch conditions (OS identities, concurrent endpoint, duration).

Identity (candidate v4; run-1 records of 10-15 used the inputs-file hash as "code identity" and
are not changed): before any call the tool verifies
  * code: every file listed in --code-manifest (a candidate manifest {"files": {path: sha256}})
    exists under --pkg with that hash; the manifest's own SHA-256 is the code identity passed to
    the recorder, which re-checks the executed files on every call;
  * generated JS: --arms-js/strip-manifest.json is COMPLETED, was produced by this Node binary,
    lists as TS inputs the hashes the code manifest gives for arms/<arm>/*.ts, and every listed
    output file has the listed hash; its SHA-256 is the JS identity;
  * Node: full SHA-256 equals --node-sha256 before the first and after the last call; the
    recorder checks size and mtime on every call;
  * inputs: the SHA-256 of --inputs is recorded as input identity, separately from code identity.
Any identity problem before the run exits 3 without calling the planner; a mismatch during the
run is a recorder exit 7 and is treated as a tool fault.

Usage: python -I qualify_planner.py --inputs FILE --pkg DIR --code-manifest FILE --arms-js DIR
                                    --node NODE --node-sha256 HEX --reps N --arms A [A ...] --out NEWDIR
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import planner_records  # noqa: E402


def sha_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def verify_code(pkg: Path, manifest: dict) -> list[str]:
    problems = []
    for rel, digest in manifest["files"].items():
        path = pkg / rel
        if not path.is_file() or sha_file(path) != digest:
            problems.append(f"package file {rel} missing or differs from code manifest")
    return problems


def verify_js(arms_js: Path, manifest: dict, node_sha: str, arms: list[str]) -> list[str]:
    jm = json.loads((arms_js / "strip-manifest.json").read_text())
    problems = []
    if jm.get("status") != "COMPLETED" or jm.get("node_binary_sha256") != node_sha or jm.get("options") != {"mode": "strip"}:
        problems.append("strip manifest not COMPLETED, other Node binary, or other options")
    for arm in arms:
        entry = (jm.get("arms") or {}).get(arm)
        if entry is None:
            problems.append(f"strip manifest has no arm {arm}")
            continue
        for name, digest in entry["inputs"].items():
            if manifest["files"].get(f"arms/{arm}/{name}") != digest:
                problems.append(f"strip input arms/{arm}/{name} differs from code manifest")
        for name, digest in entry["outputs"].items():
            path = arms_js / arm / name
            if not path.is_file() or sha_file(path) != digest:
                problems.append(f"generated {arm}/{name} differs from strip manifest")
    return problems


def classify(code, out: bytes):
    if code == 124:
        return "TIMEOUT"
    if code is None or code > 128:
        return "SIGNAL_OR_ABNORMAL"
    if code != 0:
        return f"EXIT_{code}"
    if not out:
        return "EMPTY_OUTPUT"
    try:
        v = json.loads(out)
        if isinstance(v, dict) and set(v) == {"node", "plan"} and isinstance(v["plan"], dict):
            return "OK"
    except (ValueError, UnicodeDecodeError):
        pass
    return "MALFORMED_OUTPUT"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--pkg", required=True)
    ap.add_argument("--code-manifest", required=True)
    ap.add_argument("--arms-js", required=True)
    ap.add_argument("--node", required=True)
    ap.add_argument("--node-sha256", required=True)
    ap.add_argument("--reps", type=int, required=True)
    ap.add_argument("--arms", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", default="15")
    args = ap.parse_args()
    if sha_file(args.node) != args.node_sha256:
        sys.exit("node binary hash differs from --node-sha256")
    pkg, arms_js, out = Path(args.pkg).resolve(), Path(args.arms_js).resolve(), Path(args.out)
    code_manifest = Path(args.code_manifest).resolve()
    manifest = json.loads(code_manifest.read_text())
    problems = verify_code(pkg, manifest) + verify_js(arms_js, manifest, args.node_sha256, args.arms)
    if problems:
        sys.stderr.write("identity check failed before the run:\n" + "\n".join(problems) + "\n")
        sys.exit(3)
    st = os.stat(args.node)
    identity = {"code_identity": sha_file(code_manifest), "code_manifest": str(code_manifest),
                "code_manifest_file_count": len(manifest["files"]),
                "js_identity": sha_file(arms_js / "strip-manifest.json"), "js_manifest": str(arms_js / "strip-manifest.json"),
                "input_identity": sha_file(Path(args.inputs)), "node": str(Path(args.node).resolve()),
                "node_sha256_start": args.node_sha256, "node_stat": f"{st.st_size}:{st.st_mtime_ns}"}
    out.mkdir()  # must be new
    inputs = json.loads(Path(args.inputs).read_text())["inputs"]
    recorder = pkg / "tools" / "planner_recorder.py"
    routes = {"T": (pkg / "src" / "plan.mjs", pkg / "arms"), "J": (pkg / "src" / "plan_js.mjs", arms_js)}
    schedule =[("T", 1), ("J", 1)] + [("J", r) for r in range(2, args.reps + 1)]
    calls, tool_fault = [], None
    for route, rep in schedule:
        bridge, arms_root = routes[route]
        for arm in args.arms:
            rec_dir = out / "records" / f"{route}-r{rep}" / arm
            rec_dir.mkdir(parents=True)
            arm_dir = arms_root / arm
            env = {**{k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "LANG")},
                   "ECR_REAL_NODE": str(Path(args.node).resolve()), "ECR_NODE_SHA256": args.node_sha256,
                   "ECR_PLANNER_RECORD_DIR": str(rec_dir), "ECR_UNIT_ID": f"QUAL:{arm}",
                   "ECR_ATTEMPT_ID": f"QUAL:{route}:r{rep}:{arm}", "ECR_CODE_IDENTITY": identity["code_identity"],
                   "ECR_CODE_MANIFEST": identity["code_manifest"], "ECR_CODE_ROOT": str(pkg),
                   "ECR_JS_MANIFEST": identity["js_manifest"], "ECR_JS_IDENTITY": identity["js_identity"],
                   "ECR_NODE_STAT": identity["node_stat"], "ECR_INPUT_IDENTITY": identity["input_identity"],
                   "ECR_PLANNER_ROUTE": route, "ECR_PLANNER_TIMEOUT_S": args.timeout}
            seen_ok = 0
            for i, item in enumerate(inputs, start=1):
                p = subprocess.run([sys.executable, "-I", str(recorder), str(bridge), str(arm_dir)],
                                   input=item["payload"].encode(), capture_output=True, cwd=arm_dir, env=env, timeout=120)
                kind = classify(p.returncode, p.stdout)
                seen_ok += kind == "OK"
                recorded = (rec_dir / f"call-{i:04d}.stdout.bin")
                row = {"route": route, "rep": rep, "arm": arm, "input_id": item["input_id"], "call": f"call-{i:04d}",
                       "exit": p.returncode, "result": kind, "stdout_sha256": hashlib.sha256(p.stdout).hexdigest()}
                calls.append(row)
                if p.returncode in (5, 6, 7):
                    tool_fault = f"recorder configuration error at {row}"
                elif not recorded.is_file() or recorded.read_bytes() != p.stdout:
                    tool_fault = f"caller-visible stdout differs from recorded stdout at {row}"
                if tool_fault:
                    break
            if not tool_fault:
                check = planner_records.check(rec_dir, seen_ok)
                if check["status"] != "COMPLETE":
                    tool_fault = f"record set incomplete for {route}-r{rep}/{arm}: {check['reasons']}"
            if tool_fault:
                break
        if tool_fault:
            break
    failures = [c for c in calls if c["result"] != "OK"]
    by = lambda key: {k: sum(1 for c in failures if c[key] == k) for k in sorted({c[key] for c in failures})}
    index = {(c["route"], c["rep"], c["arm"], c["input_id"]): c for c in calls}
    equivalence = []
    for arm in args.arms:
        for item in inputs:
            t, j = index.get(("T", 1, arm, item["input_id"])), index.get(("J", 1, arm, item["input_id"]))
            if not t or not j:
                continue
            row = {"arm": arm, "input_id": item["input_id"], "t_result": t["result"], "j_result": j["result"],
                   "raw_equal": t["stdout_sha256"] == j["stdout_sha256"]}
            if t["result"] == j["result"] == "OK":
                tp = json.loads((out / "records" / "T-r1" / arm / f"{t['call']}.stdout.bin").read_bytes())
                jp = json.loads((out / "records" / "J-r1" / arm / f"{j['call']}.stdout.bin").read_bytes())
                row["plan_equal"] = tp["plan"] == jp["plan"]
                row["node_field_equal"] = tp["node"] == jp["node"]
            equivalence.append(row)
    repeat_groups = {}
    for c in calls:
        if c["route"] == "J" and c["result"] == "OK":
            repeat_groups.setdefault((c["arm"], c["input_id"]), set()).add(c["stdout_sha256"])
    j_calls = [c for c in calls if c["route"] == "J"]
    n = len(j_calls)
    identity["node_sha256_end"] = sha_file(args.node)
    if identity["node_sha256_end"] != args.node_sha256 and not tool_fault:
        tool_fault = "Node binary hash changed during the run"
    summary = {
        "status": "TOOL_FAULT" if tool_fault else "COMPLETED", "tool_fault": tool_fault,
        "design": {"inputs": len(inputs), "arms": args.arms, "reps_J": args.reps, "planned_calls_T": len(inputs) * len(args.arms),
                   "planned_calls_J": len(inputs) * len(args.arms) * args.reps},
        "identity": identity,
        "node": str(Path(args.node).resolve()), "node_sha256": args.node_sha256, "inputs_sha256": sha_file(Path(args.inputs)),
        "calls_executed": len(calls), "failures_total": len(failures),
        "failures_by_route": by("route"), "failures_by_arm": by("arm"), "failures_by_input": by("input_id"),
        "failures_by_kind": by("result"), "failure_rows": failures,
        "Q1_equivalence": {"pairs": len(equivalence), "raw_equal": sum(r["raw_equal"] for r in equivalence),
                           "plan_equal": sum(r.get("plan_equal", False) for r in equivalence),
                           "status": "EQUIVALENT" if equivalence and all(r["raw_equal"] for r in equivalence) else "NOT_EQUIVALENT",
                           "rows_not_equal": [r for r in equivalence if not r["raw_equal"]]},
        "Q4_stability_J": {"calls": n, "failures": sum(c["result"] != "OK" for c in j_calls),
                           "upper_bound_95_if_zero": (1 - 0.05 ** (1 / n)) if n else None,
                           "repeat_groups": len(repeat_groups),
                           "repeat_groups_with_differing_bytes": sum(len(v) > 1 for v in repeat_groups.values())},
        "note": "zero failures bound the per-call rate under these conditions only; replay (Q3) is a separate step",
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    (out / "calls.json").write_text(json.dumps(calls, indent=1), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("status", "calls_executed", "failures_total")} |
                     {"Q1": summary["Q1_equivalence"]["status"], "Q4_failures": summary["Q4_stability_J"]["failures"]}))
    sys.exit(0 if summary["status"] == "COMPLETED" else 2)


if __name__ == "__main__":
    main()
