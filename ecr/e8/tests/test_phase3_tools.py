"""Offline self-tests of the phase-3 tools (no VM, no OS identities, no endpoint).

Covers: strip_ts.mjs (success, refusal to overwrite, explicit failure without fallback),
plan_js.mjs (refuses to fall back to TypeScript), TS-vs-JS equivalence on the local Node
(self-test only; the qualification on the pinned guest Node is separate), planner_recorder.py
fixtures (success, non-zero exit, empty output, corrupt JSON, extra keys, timeout, route mismatch,
missing environment), planner_records.check (interruption, tampering, numbering gap, PLAN-count
mismatch), replay.py verdicts (identical, plan mismatch, key order, same-runtime byte difference,
cross-runtime version difference, replay failure, original failure, unparseable original,
incomplete record, refusal to overwrite), p3lib rules, the candidate unit list, the fixed planner
input set, and a static check of run_r2.py.

Environment: ECR_SELFTEST_NODE (Node executable), ECR_SELFTEST_WORK (a directory on the work
drive; each run creates a new sub-directory there and never deletes anything).
Run: python -I -B tests/test_phase3_tools.py
"""
import hashlib
import json
import os
import py_compile
import re
import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
R1 = PKG.parent / "async_sim_r1"
sys.path.insert(0, str(PKG / "tools"))
sys.path.insert(0, str(PKG / "runner"))
import p3lib  # noqa: E402
import planner_records  # noqa: E402
import replay  # noqa: E402

NODE = os.environ["ECR_SELFTEST_NODE"]
WORK = Path(os.environ["ECR_SELFTEST_WORK"]) / time.strftime("run-%Y%m%dT%H%M%S")
WORK.mkdir(parents=True)
PY = sys.executable
ARMS = ["FULL", "A_OUTCOME_BOUNDARY", "A_UNKNOWN_PRESERVATION", "A_ACCEPTANCE_LEVEL"]
RECORDER = PKG / "tools" / "planner_recorder.py"

FAKE_BRIDGE = r"""
import fs from 'node:fs';
import path from 'node:path';
const mode = path.basename(process.argv[2]);
fs.readFileSync(0);
const out = (v) => process.stdout.write(JSON.stringify(v) + '\n');
if (mode === 'ok') out({node: process.version, plan: {a: 1, b: [1, 2]}});
else if (mode === 'diffplan') out({node: process.version, plan: {a: 2, b: [1, 2]}});
else if (mode === 'keyorder') out({node: process.version, plan: {b: [1, 2], a: 1}});
else if (mode === 'othernode') out({node: 'v0.0.0-other', plan: {a: 1, b: [1, 2]}});
else if (mode === 'exit3') { process.stderr.write('boom'); process.exit(3); }
else if (mode === 'empty') { }
else if (mode === 'corrupt') process.stdout.write('{not json');
else if (mode === 'extrakey') out({node: process.version, plan: {}, x: 1});
else if (mode === 'sleep') setTimeout(() => out({node: process.version, plan: {}}), 10000);
else { process.stderr.write('unknown mode'); process.exit(9); }
"""


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


MODES = ("ok", "diffplan", "keyorder", "othernode", "exit3", "empty", "corrupt", "extrakey", "sleep")


def write_manifest(root, path):
    """Candidate-format code manifest {"files": {relative path: sha256}} of every file under root."""
    files = {q.relative_to(root).as_posix(): sha(q) for q in sorted(Path(root).rglob("*"))
             if q.is_file() and "__pycache__" not in q.parts}
    Path(path).write_text(json.dumps({"files": files}, indent=1))
    return Path(path)


def node_stat():
    st = os.stat(NODE)
    return f"{st.st_size}:{st.st_mtime_ns}"


def fake_tree(name):
    """Fake bridges and arms with dummy TS/JS files, a code manifest and a strip manifest (outside the tree)."""
    root = WORK / name
    (root / "src").mkdir(parents=True)
    for bridge in ("plan_js.mjs", "plan.mjs"):
        (root / "src" / bridge).write_text(FAKE_BRIDGE)
    strip = {"status": "COMPLETED", "node_binary_sha256": sha(NODE), "options": {"mode": "strip"}, "arms": {}}
    for mode in MODES:
        arm = root / "arms" / mode
        arm.mkdir(parents=True)
        (arm / "delivery-reconciliation.ts").write_text("export const c: number = 1;\n")
        (arm / "delivery-reconciliation-execution.ts").write_text("export const e: number = 2;\n")
        (arm / "delivery-reconciliation.js").write_text("export const c         = 1;\n")
        (arm / "delivery-reconciliation-execution.js").write_text("export const e         = 2;\n")
        (arm / "package.json").write_text('{"type":"module"}\n')
        strip["arms"][mode] = {
            "inputs": {n: sha(arm / n) for n in ("delivery-reconciliation.ts", "delivery-reconciliation-execution.ts")},
            "outputs": {n: sha(arm / n) for n in ("delivery-reconciliation.js", "delivery-reconciliation-execution.js",
                                                  "package.json")}}
    write_manifest(root, WORK / f"{name}-code-manifest.json")
    (WORK / f"{name}-strip-manifest.json").write_text(json.dumps(strip, indent=1))
    return root


def identity_env(tree):
    cm, jm = WORK / f"{tree.name}-code-manifest.json", WORK / f"{tree.name}-strip-manifest.json"
    return {"ECR_CODE_MANIFEST": str(cm), "ECR_CODE_IDENTITY": sha(cm), "ECR_CODE_ROOT": str(tree),
            "ECR_JS_MANIFEST": str(jm), "ECR_JS_IDENTITY": sha(jm), "ECR_NODE_STAT": node_stat(),
            "ECR_INPUT_IDENTITY": "test-input-set"}


def record_call(tree, rec_dir, mode, route="J", bridge="plan_js.mjs", timeout="15", drop_env=None, env_override=None):
    rec_dir.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT")}
    env.update({"ECR_REAL_NODE": NODE, "ECR_NODE_SHA256": sha(NODE), "ECR_PLANNER_RECORD_DIR": str(rec_dir),
                "ECR_UNIT_ID": "U", "ECR_ATTEMPT_ID": f"U#a1-{rec_dir.name}",
                "ECR_PLANNER_ROUTE": route, "ECR_PLANNER_TIMEOUT_S": timeout, **identity_env(tree)})
    env.update(env_override or {})
    if drop_env:
        env.pop(drop_env)
    arm = tree / "arms" / mode
    return subprocess.run([PY, "-I", str(RECORDER), str(tree / "src" / bridge), str(arm)], input=b'{"x":1}',
                          capture_output=True, cwd=arm, env=env, timeout=60)


class StripTool(unittest.TestCase):
    def test_success_refusal_and_failure(self):
        out = WORK / "strip-ok"
        before = {a: {f: sha(PKG / "arms" / a / f) for f in os.listdir(PKG / "arms" / a)} for a in ARMS}
        p = subprocess.run([NODE, str(PKG / "tools/strip_ts.mjs"), str(PKG / "arms"), str(out), *ARMS], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        m = json.loads((out / "strip-manifest.json").read_text())
        self.assertEqual(m["status"], "COMPLETED")
        self.assertEqual(m["options"], {"mode": "strip"})
        self.assertEqual(m["node_binary_sha256"], sha(NODE))
        for a in ARMS:
            self.assertEqual((out / a / "package.json").read_text(), '{"type":"module"}\n')
            for ts, digest in m["arms"][a]["inputs"].items():
                self.assertEqual(digest, sha(PKG / "arms" / a / ts))
                js = (out / a / ts.replace(".ts", ".js")).read_text(encoding="utf-8")
                self.assertEqual(len(js), len((PKG / "arms" / a / ts).read_text(encoding="utf-8")))
                self.assertNotRegex(js, r"\bexport type\b|\binterface\s+\w+\s*\{")
        after = {a: {f: sha(PKG / "arms" / a / f) for f in os.listdir(PKG / "arms" / a)} for a in ARMS}
        self.assertEqual(before, after, "TypeScript originals must be unchanged")
        # refuses an existing output directory
        p2 = subprocess.run([NODE, str(PKG / "tools/strip_ts.mjs"), str(PKG / "arms"), str(out), "FULL"], capture_output=True, text=True)
        self.assertEqual(p2.returncode, 3)
        self.assertEqual(json.loads((out / "strip-manifest.json").read_text()), m)
        # non-erasable syntax: explicit failure, FAILED manifest, no fallback output for that file
        bad = WORK / "strip-bad-src" / "BAD"
        bad.mkdir(parents=True)
        (bad / "delivery-reconciliation.ts").write_text("export enum E { A }\n")
        (bad / "delivery-reconciliation-execution.ts").write_text("export const x: number = 1;\n")
        out_bad = WORK / "strip-bad-out"
        p3 = subprocess.run([NODE, str(PKG / "tools/strip_ts.mjs"), str(bad.parent), str(out_bad), "BAD"], capture_output=True, text=True)
        self.assertEqual(p3.returncode, 1)
        mb = json.loads((out_bad / "strip-manifest.json").read_text())
        self.assertEqual(mb["status"], "FAILED")
        self.assertFalse((out_bad / "BAD" / "delivery-reconciliation.js").exists())

    def test_plan_js_refuses_typescript_fallback(self):
        arm = PKG / "arms" / "FULL"  # has only .ts files
        p = subprocess.run([NODE, str(PKG / "src/plan_js.mjs"), str(arm)], input="{}", capture_output=True, text=True)
        self.assertEqual(p.returncode, 4)
        self.assertIn("refusing to fall back", p.stderr)


class Equivalence(unittest.TestCase):
    def test_ts_vs_js_on_local_node(self):
        out = WORK / "equiv-strip"
        subprocess.run([NODE, str(PKG / "tools/strip_ts.mjs"), str(PKG / "arms"), str(out), *ARMS], check=True, capture_output=True)
        inputs = WORK / "planner-inputs.json"
        subprocess.run([PY, "-I", "-B", str(PKG / "tools/planner_inputs.py"), str(inputs)], check=True, capture_output=True)
        q = WORK / "equiv-run"
        cm = write_manifest(PKG, WORK / "pkg-code-manifest.json")
        p = subprocess.run([PY, "-I", "-B", str(PKG / "tools/qualify_planner.py"), "--inputs", str(inputs), "--pkg", str(PKG),
                            "--code-manifest", str(cm), "--arms-js", str(out), "--node", NODE, "--node-sha256", sha(NODE),
                            "--reps", "2", "--arms", *ARMS, "--out", str(q)], capture_output=True, text=True, timeout=1800)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        s = json.loads((q / "summary.json").read_text())
        ident = s["identity"]
        self.assertEqual(ident["code_identity"], sha(cm))
        self.assertEqual(ident["input_identity"], sha(inputs))
        self.assertNotEqual(ident["code_identity"], ident["input_identity"])
        self.assertEqual(ident["js_identity"], sha(out / "strip-manifest.json"))
        self.assertEqual((ident["node_sha256_start"], ident["node_sha256_end"]), (sha(NODE), sha(NODE)))
        starts = [json.loads(x.read_text()) for x in (q / "records").glob("*/*/call-*.started.json")]
        self.assertEqual(len(starts), 216)
        self.assertTrue(all(x["identity_check"] == "OK" and x["code_identity"] == sha(cm) for x in starts))
        self.assertEqual(s["design"]["planned_calls_T"], 72)
        self.assertEqual(s["calls_executed"], 72 + 144)
        self.assertEqual(s["Q1_equivalence"]["status"], "EQUIVALENT", s["Q1_equivalence"]["rows_not_equal"])
        self.assertEqual(s["Q1_equivalence"]["raw_equal"], 72)
        self.assertEqual(s["Q4_stability_J"]["repeat_groups_with_differing_bytes"], 0)
        self.assertEqual(s["failures_total"], 0, s["failure_rows"])
        # the 18 inputs must not collapse to one plan: the classifier branches are exercised
        plans = {(c["arm"], c["input_id"]): c["stdout_sha256"] for c in json.loads((q / "calls.json").read_text()) if c["route"] == "J"}
        (WORK / "equiv-distinct-plans.txt").write_text(str(len(set(plans.values()))))
        self.assertGreaterEqual(len(set(plans.values())), 5)
        # same-runtime replay of every record of this run is byte-identical
        # (report and per-call raw evidence are written before any assertion, so a failure keeps its evidence)
        dirs = [str(d) for d in sorted((q / "records").glob("*/*"))]
        rep = replay.run([Path(d) for d in dirs], NODE, "same", [], 30, WORK / "equiv-same-runtime-replay-evidence")
        (WORK / "equiv-same-runtime-replay.json").write_text(json.dumps(rep, indent=1))
        self.assertEqual(rep["status"], "CONSISTENT", rep["verdict_counts"])
        self.assertEqual(rep["verdict_counts"], {"IDENTICAL_BYTES": 216})
        self.assertEqual(rep["identity_counts"], {"VERIFIED": 216})
        self.assertEqual(len(list((WORK / "equiv-same-runtime-replay-evidence").glob("*.stderr.bin"))), 216)


class Recorder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = fake_tree("recorder-fixtures")

    def check_one(self, mode, expect_exit, kind_ok, **kw):
        rec = WORK / f"rec-{mode}-{kw.get('route', 'J')}-{kw.get('drop_env', '')}"
        p = record_call(self.tree, rec, mode, **kw)
        self.assertEqual(p.returncode, expect_exit, p.stderr)
        return rec, p

    def test_success(self):
        rec, p = self.check_one("ok", 0, True)
        fin = json.loads((rec / "call-0001.finished.json").read_text())
        self.assertEqual((rec / "call-0001.stdout.bin").read_bytes(), p.stdout)
        self.assertEqual((rec / "call-0001.input.bin").read_bytes(), b'{"x":1}')
        self.assertEqual(fin["runtime_version_reported"], subprocess.run([NODE, "--version"], capture_output=True, text=True).stdout.strip())
        self.assertEqual(planner_records.check(rec, 1)["status"], "COMPLETE")
        self.assertEqual(planner_records.check(rec, 0)["status"], "INCOMPLETE")  # PLAN-count mismatch
        st = json.loads((rec / "call-0001.started.json").read_text())
        ident = identity_env(self.tree)
        self.assertEqual((st["identity_check"], st["identity_binding"]), ("OK", "v4"))
        self.assertEqual(st["code_identity"], ident["ECR_CODE_IDENTITY"])
        self.assertEqual(st["js_identity"], ident["ECR_JS_IDENTITY"])
        self.assertEqual(st["input_identity"], "test-input-set")
        self.assertNotEqual(st["code_identity"], st["input_sha256"])
        bridge = str((self.tree / "src/plan_js.mjs").resolve())
        self.assertEqual(st["executed_files"][bridge], sha(bridge))
        self.assertEqual(len(st["executed_files"]), 4)  # bridge + 2 generated .js + package.json

    def test_nonzero_exit(self):
        rec, p = self.check_one("exit3", 3, False)
        self.assertEqual((rec / "call-0001.stderr.bin").read_bytes(), b"boom")
        c = planner_records.check(rec, 0)
        self.assertEqual(c["status"], "COMPLETE")
        self.assertEqual(len(c["failures"]), 1)

    def test_empty_corrupt_extrakey_are_malformed(self):
        for mode in ("empty", "corrupt", "extrakey"):
            with self.subTest(mode=mode):
                rec, p = self.check_one(mode, 0, False)
                c = planner_records.check(rec, 0)
                self.assertEqual(c["status"], "INCOMPLETE")
                self.assertTrue(any("not exactly one" in r for r in c["reasons"]))

    def test_timeout(self):
        rec, p = self.check_one("sleep", 124, False, timeout="1")
        fin = json.loads((rec / "call-0001.finished.json").read_text())
        self.assertTrue(fin["timed_out"])
        self.assertEqual(len(planner_records.check(rec, 0)["failures"]), 1)

    def test_route_mismatch_and_missing_env(self):
        rec, p = self.check_one("ok", 5, False, route="T")  # route T expects plan.mjs, got plan_js.mjs
        c = planner_records.check(rec, 0)
        self.assertEqual(c["status"], "INCOMPLETE")
        self.assertTrue(any("route check failed" in r for r in c["reasons"]))
        rec2, p2 = self.check_one("ok", 6, False, drop_env="ECR_UNIT_ID")
        self.assertFalse(any(rec2.iterdir()))

    def test_record_interruption_tamper_gap(self):
        rec = WORK / "rec-interrupt"
        for _ in range(3):
            record_call(self.tree, rec, "ok")
        self.assertEqual(planner_records.check(rec, 3)["status"], "COMPLETE")
        os.rename(rec / "call-0002.finished.json", WORK / "rec-interrupt-moved-finished.json")  # kept, not deleted
        c = planner_records.check(rec, 2)
        self.assertEqual(c["status"], "INCOMPLETE")
        self.assertTrue(any("call-0002: missing finished.json" in r for r in c["reasons"]))
        rec2 = WORK / "rec-tamper"
        record_call(self.tree, rec2, "ok")
        with open(rec2 / "call-0001.stdout.bin", "ab") as f:
            f.write(b" ")
        self.assertTrue(any("hash mismatch" in r for r in planner_records.check(rec2, 1)["reasons"]))
        rec3 = WORK / "rec-gap"
        record_call(self.tree, rec3, "ok")
        record_call(self.tree, rec3, "ok")
        for suffix in ("input.bin", "started.json", "stdout.bin", "stderr.bin", "finished.json"):
            os.rename(rec3 / f"call-0001.{suffix}", WORK / f"rec-gap-moved-call-0001.{suffix}")
        self.assertTrue(any("gaps" in r for r in planner_records.check(rec3, 1)["reasons"]))


class Replay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = fake_tree("replay-fixtures")
        cls.rec = WORK / "replay-rec-ok"
        record_call(cls.tree, cls.rec, "ok")
        cls.rec_fail = WORK / "replay-rec-fail"
        record_call(cls.tree, cls.rec_fail, "exit3")
        cls.rec_corrupt = WORK / "replay-rec-corrupt"
        record_call(cls.tree, cls.rec_corrupt, "corrupt")

    def verdict(self, rec, mode, target=None):
        arms = self.tree / "arms"
        maps = [(str(arms / "ok"), str(arms / target))] if target else []
        if rec is self.rec_fail and target:
            maps = [(str(arms / "exit3"), str(arms / target))]
        r = replay.run([rec], NODE, mode, maps, 5)
        return r["rows"][0]["verdict"], r["status"]

    def test_verdicts(self):
        self.assertEqual(self.verdict(self.rec, "same"), ("IDENTICAL_BYTES", "CONSISTENT"))
        self.assertEqual(self.verdict(self.rec, "same", "diffplan"), ("PLAN_MISMATCH", "STOP_INVESTIGATE"))
        self.assertEqual(self.verdict(self.rec, "same", "keyorder"), ("KEY_ORDER_DIFFERS", "STOP_INVESTIGATE"))
        self.assertEqual(self.verdict(self.rec, "same", "othernode"), ("BYTES_DIFFER_SAME_RUNTIME", "STOP_INVESTIGATE"))
        self.assertEqual(self.verdict(self.rec, "cross", "othernode"), ("EQUAL_EXCEPT_RUNTIME_VERSION", "CONSISTENT"))
        self.assertEqual(self.verdict(self.rec, "cross", "keyorder"), ("KEY_ORDER_DIFFERS", "STOP_INVESTIGATE"))
        self.assertEqual(self.verdict(self.rec, "same", "exit3"), ("REPLAY_FAILED", "STOP_INVESTIGATE"))
        self.assertEqual(self.verdict(self.rec, "same", "corrupt"), ("REPLAY_FAILED", "STOP_INVESTIGATE"))
        self.assertEqual(self.verdict(self.rec_fail, "same"), ("ORIGINAL_FAILED_STILL_FAILS", "CONSISTENT"))
        self.assertEqual(self.verdict(self.rec_fail, "same", "ok"), ("ORIGINAL_FAILED_NOW_SUCCEEDS", "CONSISTENT"))
        self.assertEqual(self.verdict(self.rec_corrupt, "same"), ("ORIGINAL_UNPARSEABLE", "STOP_INVESTIGATE"))

    def test_failed_replay_keeps_raw_evidence(self):
        ev = WORK / "replay-evidence-failed"
        arms = self.tree / "arms"
        r = replay.run([self.rec], NODE, "same", [(str(arms / "ok"), str(arms / "exit3"))], 5, ev)
        row = r["rows"][0]
        self.assertEqual(row["verdict"], "REPLAY_FAILED")
        self.assertEqual(row["replay_exit"], 3)
        self.assertEqual(Path(row["evidence_prefix"] + ".stderr.bin").read_bytes(), b"boom")
        self.assertEqual(Path(row["evidence_prefix"] + ".stdout.bin").read_bytes(), b"")
        meta = json.loads(Path(row["evidence_prefix"] + ".execution.json").read_text())
        self.assertEqual((meta["replay_exit"], meta["source_record"], meta["argv"][1]),
                         (3, str(self.rec / "call-0001"), str(arms / "exit3")))
        self.assertEqual(meta["replay_stderr_sha256"], hashlib.sha256(b"boom").hexdigest())
        with self.assertRaises(FileExistsError):  # an evidence directory is never reused
            replay.run([self.rec], NODE, "same", [], 5, ev)

    def test_incomplete_record_is_attributed(self):
        import finalize_states
        rec = WORK / "replay-rec-incomplete-attributed"
        record_call(self.tree, rec, "ok")
        record_call(self.tree, rec, "ok")
        os.rename(rec / "call-0002.finished.json", WORK / "replay-rec-incomplete-attributed-moved-finished.json")
        r = replay.run([rec], NODE, "same", [], 5)
        self.assertEqual([x["verdict"] for x in r["rows"]], ["IDENTICAL_BYTES", "RECORD_INCOMPLETE"])
        grouped, unattributed = finalize_states.rows_by_attempt(r)
        self.assertFalse(unattributed)
        aid = f"U#a1-{rec.name}"
        self.assertEqual(len(grouped[aid]), 2)
        ok_cross = [{"call_id": "call-0001", "verdict": "IDENTICAL_BYTES"}, {"call_id": "call-0002", "verdict": "IDENTICAL_BYTES"}]
        self.assertEqual(p3lib.finalize_attempt("OBSERVED_PENDING_REPLAY", 2, grouped[aid], ok_cross, False)[0], "INTEGRITY_UNKNOWN")

    def test_incomplete_record_and_no_overwrite(self):
        rec = WORK / "replay-rec-incomplete"
        record_call(self.tree, rec, "ok")
        os.rename(rec / "call-0001.finished.json", WORK / "replay-rec-incomplete-moved-finished.json")
        r = replay.run([rec], NODE, "same", [], 5)
        self.assertEqual((r["rows"][0]["verdict"], r["status"]), ("RECORD_INCOMPLETE", "STOP_INVESTIGATE"))
        out = WORK / "replay-report.json"
        args = [PY, "-I", "-B", str(PKG / "tools/replay.py"), "--records", str(self.rec), "--node", NODE, "--mode", "same", "--out", str(out)]
        self.assertEqual(subprocess.run(args, capture_output=True).returncode, 0)
        first = out.read_bytes()
        p = subprocess.run(args, capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("refusing to overwrite", p.stderr)
        self.assertEqual(out.read_bytes(), first)
        p2 = subprocess.run(args[:-1] + ["-"], capture_output=True, text=True)
        self.assertEqual(json.loads(p2.stdout)["status"], "CONSISTENT")


class StateRules(unittest.TestCase):
    OK = {"harness_integrity_ok": True, "recovery_exit": 0, "final_state_present": True, "planner_records": "COMPLETE",
          "planner_failures": 0, "infra_events": [], "scorer_faults": [], "replay": "PENDING"}

    def ev(self, **kw):
        return p3lib.evaluate_attempt({**self.OK, **kw})[0]

    def test_ids(self):
        u = p3lib.unit_id("P3A", "F7_B4_P0", "FULL", "v2", "R2B")
        self.assertEqual(u, "P3A:F7_B4_P0:FULL:v2:R2B")
        a1, a2 = p3lib.attempt_id(u, 1), p3lib.attempt_id(u, 2)
        self.assertNotEqual(p3lib.attempt_dirname(a1), p3lib.attempt_dirname(a2))
        self.assertEqual(p3lib.attempt_dirname(a1), "P3A__F7_B4_P0__FULL__v2__R2B--a1")
        with self.assertRaises(ValueError):
            p3lib.attempt_dirname("P3A:X__Y:FULL:v1:-#a1")
        with self.assertRaises(ValueError):
            p3lib.attempt_dirname("P3A:../x:FULL:v1:-#a1")
        with self.assertRaises(ValueError):
            p3lib.attempt_id(u, 0)

    def test_evaluate(self):
        self.assertEqual(self.ev(), "OBSERVED_PENDING_REPLAY")
        self.assertEqual(self.ev(replay="CONSISTENT"), "VALID_COMPLETED")
        self.assertEqual(self.ev(replay="STOP_INVESTIGATE"), "INTEGRITY_UNKNOWN")
        self.assertEqual(self.ev(harness_integrity_ok=False), "INTEGRITY_UNKNOWN")
        self.assertEqual(self.ev(planner_records="INCOMPLETE"), "INTEGRITY_UNKNOWN")
        self.assertEqual(self.ev(scorer_faults=["primary:X"]), "INTEGRITY_UNKNOWN")
        self.assertEqual(self.ev(recovery_exit=1, final_state_present=False, planner_failures=1), "INFRA_FAILED")
        self.assertEqual(self.ev(recovery_exit=None, final_state_present=False, infra_events=["recovery timeout"]), "INFRA_FAILED")
        self.assertEqual(self.ev(recovery_exit=1, final_state_present=False), "INTEGRITY_UNKNOWN")
        self.assertEqual(self.ev(planner_failures=1), "INTEGRITY_UNKNOWN")
        self.assertEqual(self.ev(infra_events=["endpoint error"]), "INTEGRITY_UNKNOWN")

    def test_retry_rules(self):
        self.assertEqual(p3lib.may_add_attempt([], False)[0], True)
        self.assertEqual(p3lib.may_add_attempt(["INFRA_FAILED"], False)[0], False)  # no automatic retry
        self.assertEqual(p3lib.may_add_attempt(["INFRA_FAILED"], True)[0], True)
        self.assertEqual(p3lib.may_add_attempt(["INTEGRITY_UNKNOWN"], True)[0], True)
        self.assertEqual(p3lib.may_add_attempt(["INFRA_FAILED", "INFRA_FAILED"], True)[0], False)  # at most one extra
        self.assertEqual(p3lib.may_add_attempt(["OBSERVED_PENDING_REPLAY"], True)[0], False)
        self.assertEqual(p3lib.may_add_attempt(["VALID_COMPLETED"], True)[0], False)
        self.assertEqual(p3lib.unit_state([]), "NOT_STARTED")
        self.assertEqual(p3lib.unit_state(["INFRA_FAILED", "VALID_COMPLETED"]), "VALID_COMPLETED")
        self.assertEqual(p3lib.unit_state(["INFRA_FAILED", "INTEGRITY_UNKNOWN"]), "INTEGRITY_UNKNOWN")
        self.assertEqual(p3lib.unit_state(["OBSERVED_PENDING_REPLAY"]), "INTEGRITY_UNKNOWN")
        self.assertTrue(p3lib.batch_should_stop("INFRA_FAILED"))
        self.assertFalse(p3lib.batch_should_stop("OBSERVED_PENDING_REPLAY"))

    def test_finalize(self):
        same = [{"call_id": "call-0001", "verdict": "IDENTICAL_BYTES"}]
        cross = [{"call_id": "call-0001", "verdict": "EQUAL_EXCEPT_RUNTIME_VERSION"}]
        f = p3lib.finalize_attempt
        self.assertEqual(f("OBSERVED_PENDING_REPLAY", 1, same, cross, False)[0], "VALID_COMPLETED")
        self.assertEqual(f("OBSERVED_PENDING_REPLAY", 0, [], [], False)[0], "VALID_COMPLETED")
        self.assertEqual(f("OBSERVED_PENDING_REPLAY", 2, same, cross, False)[0], "INTEGRITY_UNKNOWN")
        self.assertEqual(f("OBSERVED_PENDING_REPLAY", 1, cross, cross, False)[0], "INTEGRITY_UNKNOWN")
        self.assertEqual(f("OBSERVED_PENDING_REPLAY", 1, same, [{"call_id": "call-0001", "verdict": "PLAN_MISMATCH"}], False)[0],
                         "INTEGRITY_UNKNOWN")
        self.assertEqual(f("OBSERVED_PENDING_REPLAY", 1, same, cross, True)[0], "INTEGRITY_UNKNOWN")
        self.assertEqual(f("INFRA_FAILED", 1, same, cross, False)[0], "INFRA_FAILED")


class UnitList(unittest.TestCase):
    def test_counts_truth_and_exposure(self):
        cells = json.loads((PKG / "private/cells_p3.json").read_text())
        r1 = json.loads((R1 / "pkg_r1/private/cells.json").read_text())
        self.assertEqual(cells["r1_cells_sha256"], sha(R1 / "pkg_r1/private/cells.json"))
        hist = {c["scenario"]["scenario_id"]: (c["scenario"], c["truth"]) for c in r1["formal_cells"]}
        units = cells["units"]
        a = [u for u in units if u["family"] == "P3A"]
        self.assertEqual(len(a), 588)
        self.assertEqual(sum(u.get("conv_extra", False) for u in a), 12)
        x1 = [u for u in a if u.get("x1")]
        self.assertEqual(len(x1), 48)
        self.assertEqual(sorted({(u["classifier_arm"], u["executor"], u["r2_mode"]) for u in x1}, key=str),
                         sorted({("FULL", "v1", None), ("-", "CONV", None), ("FULL", "v2", "R2A"), ("FULL", "v2", "R2B")}, key=str))
        self.assertTrue(all(u["scenario"]["overlay"] == "F7_LOCAL_FAILURE_AFTER_DISPATCH" for u in x1))
        for u in a:
            if u["group"] == "hist":
                self.assertEqual((u["scenario"], u["truth"]), hist[u["scenario"]["scenario_id"]])
            else:
                t = u["truth"]
                self.assertEqual((t["dispatched"], t["max_allowed_effects"], t["send_permitted"], t["safe_opportunity"]),
                                 (True, 1, True, t["profile"] != "P0"))
        self.assertEqual(len([u for u in units if u["family"] == "P3C"]), 52)
        self.assertEqual(len(cells["qualification_unit_ids"]), 20)
        self.assertTrue(all(i.startswith("P3Q:") for i in cells["qualification_unit_ids"]))
        ids = [u["unit_id"] for u in units]
        self.assertEqual(len(ids), len(set(ids)))
        for u in units:
            p3lib.attempt_dirname(p3lib.attempt_id(u["unit_id"], 2))
        seen_new = {u["exposure"] for u in a if u["classifier_arm"] in ("A_OUTCOME_BOUNDARY", "A_UNKNOWN_PRESERVATION") and u["executor"] == "v2"}
        self.assertEqual(seen_new, {"NEW"})
        self.assertTrue(all(u["exposure"].startswith("SEEN") for u in a if u["classifier_arm"] == "FULL" and u["executor"] == "v2"))

    def test_planner_inputs_deterministic(self):
        import planner_inputs
        a, b = planner_inputs.build(), planner_inputs.build()
        self.assertEqual(a, b)
        self.assertEqual(len(a["inputs"]), 18)
        self.assertEqual(len({i["payload_sha256"] for i in a["inputs"]}), 18)


class ScorerControls(unittest.TestCase):
    """runner/controls.py on a synthetic completed attempt (B6_P0-like: original committed, no recovery
    send), with both frozen scorers called in-process. os.geteuid is absent on Windows; it is
    stubbed for this test only (the scorers only report it)."""

    def attempt(self, name):
        root = WORK / name
        (root / "sut/store/recovery").mkdir(parents=True)
        (root / "sut/events").mkdir(parents=True)
        (root / "endpoint-events").mkdir()
        ledger = root / "effects.sqlite3"
        with sqlite3.connect(ledger) as db:
            db.executescript("""
                CREATE TABLE fences (logical_operation_id TEXT NOT NULL, generation INTEGER NOT NULL,
                    PRIMARY KEY(logical_operation_id,generation));
                CREATE TABLE effects (seq INTEGER PRIMARY KEY AUTOINCREMENT, logical_operation_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL, transport_request_id TEXT NOT NULL, committed INTEGER NOT NULL);
                INSERT INTO effects(logical_operation_id,attempt_id,transport_request_id,committed) VALUES ('C-op','C-a1','C-t1',1);
            """)
        db.close()
        (root / "endpoint-events/log-0001-send.json").write_text(json.dumps(
            {"index": 1, "kind": "send", "gated": True, "request": {"logical_operation_id": "C-op", "attempt_id": "C-a1",
             "transport_request_id": "C-t1", "generation": 0}, "result": {"status": "COMMITTED"}}))
        (root / "sut/store/final.json").write_text(json.dumps({"policy": "EBRQ", "task": {"deliveryState": "adapter_accepted"}}))
        (root / "sut/store/recovery/001.json").write_text(json.dumps({"index": 1, "action": "RECOVERY_START"}))
        for probe in ("ledger-access-send.json", "ledger-access-recover.json"):
            (root / "sut/events" / probe).write_text(json.dumps([{"action": "read", "observed": "DENIED"}]))
        (root / "injection-receipt.json").write_text(json.dumps({"exit_status": -9, "barrier_id": "B6"}))
        truth = {"scenario_id": "B6_P0", "barrier_id": "B6", "profile": "P0", "dispatched": True,
                 "max_allowed_effects": 1, "send_permitted": True, "safe_opportunity": True}
        ctx = {"run_id": name, "arm": "test", "truth": truth, "logical_operation_id": "C-op", "ledger": str(ledger),
               "endpoint_events": str(root / "endpoint-events"), "sut_copy": str(root / "sut"),
               "injection": str(root / "injection-receipt.json")}
        return root, ctx

    def test_controls_detected_by_both_scorers(self):
        import controls
        sys.path.insert(0, str(PKG / "src"))
        import scorer as scorer_r1
        import scorer_v2
        stub = not hasattr(os, "geteuid")
        if stub:
            os.geteuid = lambda: 0
        try:
            root, ctx = self.attempt("controls-attempt")
            run = {"scorer_v2.py": scorer_v2.score, "scorer.py": scorer_r1.score}
            base_v2, base_r1 = scorer_v2.score(ctx), scorer_r1.score(ctx)
            self.assertEqual((base_v2["faults"], base_r1["faults"]), ([], []))
            self.assertTrue(base_v2["true_completion"] and base_r1["true_completion"])
            ledger_before = sha(ctx["ledger"])
            planted = controls.plant(ctx, root / "negative-controls")
            scores = {name: run[s](c) for name, s, c in controls.contexts(ctx, ctx, planted)}
            result = controls.judge(base_v2, scores)
            (root / "controls.json").write_text(json.dumps(result, indent=1))
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(result["unsafe_send_v2_s4_before_after"], [0, 1])
            self.assertEqual(sha(ctx["ledger"]), ledger_before, "original evidence must not change")
            with self.assertRaises(FileExistsError):
                controls.plant(ctx, root / "negative-controls")
            # a blind scorer (returns the unplanted result for everything) must be reported, not pass
            blind = {name: (base_v2 if s == "scorer_v2.py" else base_r1) for name, s, _ in controls.contexts(ctx, ctx, planted)}
            self.assertEqual(sorted(controls.judge(base_v2, blind)["missed"]),
                             sorted(["duplicate-v2", "false-completion-v2", "duplicate-r1", "false-completion-r1",
                                     "unsafe-auth-r1", "unsafe-send-v2"]))
        finally:
            if stub:
                del os.geteuid


class ExecutionManifest(unittest.TestCase):
    def test_manifest_contents_and_refusals(self):
        import shutil as sh
        copy = WORK / "manifest-pkg"
        sh.copytree(PKG, copy, ignore=sh.ignore_patterns("__pycache__"))
        cand = write_manifest(copy, WORK / "manifest-pkg-candidate.json")
        tool = [PY, "-I", "-B", str(PKG / "tools/make_execution_manifest.py"), "--pkg", str(copy), "--vm-uuid", "X-UUID",
                "--python-version", "3.12.3", "--kernel", "k", "--node-sha256", sha(NODE)]
        cflag = ["--candidate-manifest", str(cand)]
        p = subprocess.run(tool + cflag + ["--route", "J"], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)  # no JS artifacts yet: route J refused
        self.assertIn("strip-manifest", p.stderr)
        subprocess.run([NODE, str(PKG / "tools/strip_ts.mjs"), str(copy / "arms"), str(copy / "arms_js"), *ARMS], check=True,
                       capture_output=True)
        p = subprocess.run(tool + cflag + ["--route", "J"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        m = json.loads((copy / "execution-manifest-r2.json").read_text())
        self.assertEqual(m["candidate_manifest_sha256"], sha(cand))
        self.assertEqual(m["vm_uuids"], ["x-uuid"])
        self.assertTrue(set(m["public_files"]) <= set(m["files"]))
        for rel in ("tools/planner_recorder.py", "tools/replay.py", "src/plan_js.mjs", "arms_js/FULL/delivery-reconciliation.js",
                    "arms_js/FULL/package.json"):
            self.assertIn(rel, m["public_files"])
        for rel in ("runner/run_r2.py", "private/cells_p3.json", "tools/strip_ts.mjs"):
            self.assertIn(rel, m["files"])
            self.assertNotIn(rel, m["public_files"])
        self.assertNotIn("execution-manifest-r2.json", m["files"])
        self.assertEqual(m["files"]["runner/run_r2.py"], sha(PKG / "runner/run_r2.py"))
        p = subprocess.run(tool + cflag + ["--route", "J"], capture_output=True, text=True)
        self.assertIn("refusing to overwrite", p.stderr)
        # a strip manifest from another Node binary is refused
        bad = WORK / "manifest-pkg-other-node"
        sh.copytree(copy, bad, ignore=sh.ignore_patterns("execution-manifest-r2.json"))
        p = subprocess.run([*tool[:5], str(bad), *tool[6:-1], "0" * 64, *cflag, "--route", "J"], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("another Node binary", p.stderr)
        # a package that differs from the candidate manifest is refused (changed file, extra file)
        changed = WORK / "manifest-pkg-changed"
        sh.copytree(copy, changed, ignore=sh.ignore_patterns("execution-manifest-r2.json"))
        with open(changed / "runner/p3lib.py", "ab") as f:
            f.write(b"# changed\n")
        p = subprocess.run([*tool[:5], str(changed), *tool[6:], *cflag, "--route", "J"], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("candidate file runner/p3lib.py missing or differs", p.stderr)
        self.assertFalse((changed / "execution-manifest-r2.json").exists())
        extra = WORK / "manifest-pkg-extra"
        sh.copytree(copy, extra, ignore=sh.ignore_patterns("execution-manifest-r2.json"))
        (extra / "tools/extra.py").write_text("x = 1\n")
        p = subprocess.run([*tool[:5], str(extra), *tool[6:], *cflag, "--route", "J"], capture_output=True, text=True)
        self.assertIn("package file tools/extra.py not in candidate manifest", p.stderr)


class CompareWithR1(unittest.TestCase):
    MATRIX = R1.parent / "environment-runs/20261012-async-sim-r1/outcome-matrix.csv"
    MATRIX_SHA = "d81ad998c775ae054552ef9aed44a80fcac1e4e68a3c3873158520abb94f023b"

    def score(self, **kw):
        s = {"duplicate_effect": False, "unauthorized_effect": False, "false_completion": False, "s4_invalid_sends": 0,
             "true_completion": True, "safe_opportunity": True, "effect_count": 1}
        s.update(kw)
        return s

    def test_code_matches_dev_suite_definition(self):
        import compare_qualification_r1 as cq
        src = (PKG / "tests/run_dev_suite.py").read_text()
        ns = {}
        exec(src[src.index("def code("):src.index("def safety_failures(")], ns)  # the E3 definition, without importing the suite
        variants = [self.score(), self.score(safe_opportunity=False), self.score(true_completion=False),
                    self.score(true_completion=False, safe_opportunity=False, effect_count=0),
                    self.score(duplicate_effect=True, effect_count=2, s4_invalid_sends=1),
                    self.score(unauthorized_effect=True, true_completion=False), self.score(false_completion=True, true_completion=False, effect_count=0)]
        for v in variants:
            self.assertEqual(cq.code(v), ns["code"](v))

    def test_compare_pass_fail_and_missing(self):
        import compare_qualification_r1 as cq
        self.assertEqual(sha(self.MATRIX), self.MATRIX_SHA)
        cells = json.loads((PKG / "private/cells_p3.json").read_text())
        with open(self.MATRIX, newline="", encoding="utf-8") as f:
            matrix = {r["scenario"]: r for r in __import__("csv").DictReader(f)}
        units = [u for u in cells["units"] if u["family"] == "P3Q" and u.get("compare_with_r1")]
        self.assertEqual(len(units), 13)
        batch = WORK / "compare-batch"
        for i, u in enumerate(units):
            sid = u["scenario"]["scenario_id"]
            expected = matrix[sid]["EBRQ"]
            # build a primary score whose code equals the historical one (parsed from the expected string)
            e = int(expected.split("e=")[1])
            s = self.score(effect_count=e, safe_opportunity=u["truth"]["safe_opportunity"],
                           true_completion=expected.startswith("DONE"), s4_invalid_sends=0)
            if "UNSAFE_AUTH" in expected:
                s["s4_invalid_sends"] = int(expected.split("UNSAFE_AUTH")[1].split()[0].split("+")[0])
            if expected.startswith("DUP"):
                s.update(duplicate_effect=True, true_completion=True)
            self.assertEqual(cq.code(s), expected, sid)
            aid = p3lib.attempt_id(u["unit_id"], 1)
            d = batch / "attempts" / p3lib.attempt_dirname(aid)
            d.mkdir(parents=True)
            (d / "attempt-result.json").write_text(json.dumps({"unit_id": u["unit_id"], "attempt_id": aid,
                                                               "state": "OBSERVED_PENDING_REPLAY", "primary_score": s}))
        r = cq.compare(batch, cells, matrix)
        self.assertEqual((r["status"], r["matched"]), ("PASS", 13))
        # one changed outcome -> FAIL; one missing attempt -> FAIL
        first = sorted((batch / "attempts").iterdir())[0] / "attempt-result.json"
        f = json.loads(first.read_text())
        f["primary_score"]["effect_count"] += 1
        first.write_text(json.dumps(f))
        self.assertEqual(cq.compare(batch, cells, matrix)["status"], "FAIL")
        os.rename(first, first.with_name("attempt-result.moved.json"))
        r = cq.compare(batch, cells, matrix)
        self.assertEqual((r["status"], r["matched"]), ("FAIL", 12))


class IdentityBinding(unittest.TestCase):
    """Candidate v4: code identity bound to an explicit manifest, separate from input identity."""

    def mismatch(self, case, mutate=None, route="J", bridge="plan_js.mjs", env_override=None, expect=""):
        tree = fake_tree(f"id-{case}")
        if mutate:
            mutate(tree)
        rec = WORK / f"id-{case}-rec"
        p = record_call(tree, rec, "ok", route=route, bridge=bridge, env_override=env_override)
        self.assertEqual(p.returncode, 7, (case, p.stderr))
        self.assertEqual(p.stdout, b"")  # Node was not started
        st = json.loads((rec / "call-0001.started.json").read_text())
        fin = json.loads((rec / "call-0001.finished.json").read_text())
        self.assertEqual((st["identity_check"], fin["identity_rejected"], fin["exit_code"]), ("MISMATCH", True, 7))
        self.assertTrue(any(expect in x for x in st["identity_problems"]), (case, st["identity_problems"]))
        c = planner_records.check(rec, 0)
        self.assertEqual(c["status"], "INCOMPLETE")
        self.assertTrue(any("identity check MISMATCH" in r for r in c["reasons"]))

    def test_identity_mismatches_are_recorded_and_refused(self):
        def append(rel):
            return lambda t: open(t / rel, "a").write("// changed\n")

        def ts_and_manifest(t):
            append("arms/ok/delivery-reconciliation.ts")(t)
            write_manifest(t, WORK / f"{t.name}-code-manifest.json")  # manifest follows the TS, strip manifest does not
        self.mismatch("code-identity", env_override={"ECR_CODE_IDENTITY": "0" * 64}, expect="ECR_CODE_IDENTITY")
        self.mismatch("bridge", append("src/plan_js.mjs"), expect="bridge src/plan_js.mjs")
        self.mismatch("js-output", append("arms/ok/delivery-reconciliation.js"), expect="differs from strip manifest")
        self.mismatch("strip-input", ts_and_manifest, expect="strip input arms/ok/delivery-reconciliation.ts")
        self.mismatch("ts-route", append("arms/ok/delivery-reconciliation.ts"), route="T", bridge="plan.mjs",
                      expect="TS source arms/ok/delivery-reconciliation.ts")
        self.mismatch("js-identity", env_override={"ECR_JS_IDENTITY": "0" * 64}, expect="ECR_JS_IDENTITY")
        self.mismatch("node-stat", env_override={"ECR_NODE_STAT": "1:1"}, expect="Node binary size/mtime")
        self.mismatch("outside-root", env_override={"ECR_CODE_ROOT": str(WORK / "id-outside-root" / "arms")},
                      expect="outside the code root")

    def test_route_t_identity_ok(self):
        tree = fake_tree("id-route-t")
        rec = WORK / "id-route-t-rec"
        p = record_call(tree, rec, "ok", route="T", bridge="plan.mjs")
        self.assertEqual(p.returncode, 0, p.stderr)
        st = json.loads((rec / "call-0001.started.json").read_text())
        self.assertEqual(st["identity_check"], "OK")
        self.assertEqual(sorted(Path(k).name for k in st["executed_files"]),
                         ["delivery-reconciliation-execution.ts", "delivery-reconciliation.ts", "plan.mjs"])

    def test_two_code_identities_in_one_attempt_are_incomplete(self):
        a, b = fake_tree("id-two-a"), fake_tree("id-two-b")
        with open(b / "src/plan_js.mjs", "a") as f:
            f.write("// other code\n")
        write_manifest(b, WORK / "id-two-b-code-manifest.json")
        rec = WORK / "id-two-rec"
        self.assertEqual(record_call(a, rec, "ok").returncode, 0)
        self.assertEqual(record_call(b, rec, "ok").returncode, 0)
        c = planner_records.check(rec, 2)
        self.assertEqual(c["status"], "INCOMPLETE")
        self.assertTrue(any("more than one (code, js) identity" in r for r in c["reasons"]))

    def test_replay_refuses_changed_code_and_other_runtime(self):
        tree = fake_tree("id-replay")
        rec = WORK / "id-replay-rec"
        self.assertEqual(record_call(tree, rec, "ok").returncode, 0)
        r = replay.run([rec], NODE, "same", [], 5)
        self.assertEqual((r["rows"][0]["verdict"], r["rows"][0]["identity"], r["rows"][0]["runtime_identity"]),
                         ("IDENTICAL_BYTES", "VERIFIED", "SAME"))
        # recorded Node differs from the replay Node: refused in same mode, executed (and reported) in cross mode
        other = WORK / "id-replay-rec-other-node"
        other.mkdir()
        for f in rec.iterdir():
            (other / f.name).write_bytes(f.read_bytes())
        st = json.loads((other / "call-0001.started.json").read_text())
        st["node_sha256_expected"] = "0" * 64
        (other / "call-0001.started.json").write_text(json.dumps(st))
        r = replay.run([other], NODE, "same", [], 5)
        self.assertEqual((r["rows"][0]["verdict"], r["status"]), ("RUNTIME_IDENTITY_MISMATCH", "STOP_INVESTIGATE"))
        r = replay.run([other], NODE, "cross", [], 5)
        self.assertEqual((r["rows"][0]["verdict"], r["rows"][0]["runtime_identity"]), ("IDENTICAL_BYTES", "DIFFERENT"))
        # code changed after recording: not re-run, evidence says so
        with open(tree / "src/plan_js.mjs", "a") as f:
            f.write("// changed after recording\n")
        ev = WORK / "id-replay-evidence"
        r = replay.run([rec], NODE, "same", [], 5, ev)
        row = r["rows"][0]
        self.assertEqual((row["verdict"], row["identity"], r["status"]), ("CODE_IDENTITY_MISMATCH", "MISMATCH", "STOP_INVESTIGATE"))
        self.assertEqual(row["identity_mismatches"][0]["path"], str((tree / "src/plan_js.mjs").resolve()))
        self.assertFalse("replay_exit" in row)
        meta = json.loads(Path(row["evidence_prefix"] + ".execution.json").read_text())
        self.assertFalse(meta["executed"])
        self.assertFalse(Path(row["evidence_prefix"] + ".stdout.bin").exists())

    def test_record_without_binding_is_marked_not_backfilled(self):
        tree = fake_tree("id-old")
        rec = WORK / "id-old-rec"
        self.assertEqual(record_call(tree, rec, "ok").returncode, 0)
        old = WORK / "id-old-rec-format-v3"
        old.mkdir()
        for f in rec.iterdir():
            (old / f.name).write_bytes(f.read_bytes())
        st = json.loads((old / "call-0001.started.json").read_text())
        for k in ("executed_files", "identity_check", "identity_problems", "identity_binding", "code_manifest", "js_identity"):
            st.pop(k)
        (old / "call-0001.started.json").write_text(json.dumps(st))
        r = replay.run([old], NODE, "same", [], 5)
        self.assertEqual((r["rows"][0]["verdict"], r["rows"][0]["identity"]),
                         ("IDENTICAL_BYTES", "UNVERIFIABLE_RECORD_PREDATES_BINDING"))
        c = planner_records.check(old, 1)
        self.assertEqual(c["status"], "INCOMPLETE")
        self.assertTrue(any("identity check ABSENT" in x for x in c["reasons"]))

    def test_qualify_refuses_code_or_js_mismatch_before_any_call(self):
        js = WORK / "id-qualify-strip"
        subprocess.run([NODE, str(PKG / "tools/strip_ts.mjs"), str(PKG / "arms"), str(js), *ARMS], check=True, capture_output=True)
        inputs = WORK / "id-qualify-inputs.json"
        subprocess.run([PY, "-I", "-B", str(PKG / "tools/planner_inputs.py"), str(inputs)], check=True, capture_output=True)
        cm = write_manifest(PKG, WORK / "id-qualify-manifest-good.json")
        bad = json.loads(cm.read_text())
        bad["files"]["src/plan.mjs"] = "0" * 64
        bad_cm = WORK / "id-qualify-manifest-bad.json"
        bad_cm.write_text(json.dumps(bad))
        base = [PY, "-I", "-B", str(PKG / "tools/qualify_planner.py"), "--inputs", str(inputs), "--pkg", str(PKG),
                "--arms-js", str(js), "--node", NODE, "--node-sha256", sha(NODE), "--reps", "1", "--arms", "FULL"]
        p = subprocess.run(base + ["--code-manifest", str(bad_cm), "--out", str(WORK / "id-qualify-out-bad")],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 3, p.stderr)
        self.assertIn("package file src/plan.mjs missing or differs", p.stderr)
        self.assertFalse((WORK / "id-qualify-out-bad").exists())
        with open(js / "FULL/delivery-reconciliation.js", "a") as f:
            f.write("// changed\n")
        p = subprocess.run(base + ["--code-manifest", str(cm), "--out", str(WORK / "id-qualify-out-js")],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 3, p.stderr)
        self.assertIn("generated FULL/delivery-reconciliation.js differs from strip manifest", p.stderr)


class RunnerStatic(unittest.TestCase):
    def test_compiles_and_never_deletes(self):
        target = WORK / "run_r2.pyc"
        py_compile.compile(str(PKG / "runner/run_r2.py"), cfile=str(target), doraise=True)
        src = (PKG / "runner/run_r2.py").read_text()
        self.assertNotIn("rmtree", src)
        self.assertEqual(len(re.findall(r"\.unlink\(", src)), 1)  # only the historical F6 overlay
        self.assertNotIn("replace=True)\n", src.split("def apply_overlay")[0])
        self.assertIn('"tools/planner_recorder.py"', src)
        self.assertIn("crash_first", src)  # first-dispatch crash cells are refused
        self.assertIn("controls.plant(", src)  # qualification runs the planted scorer controls
        for key in ("ECR_CODE_MANIFEST", "ECR_CODE_ROOT", "ECR_NODE_STAT", "ECR_JS_MANIFEST", "ECR_JS_IDENTITY"):
            self.assertIn(key, src)  # the formal runner passes the v4 identity binding to the recorder


if __name__ == "__main__":
    unittest.main(verbosity=2)
