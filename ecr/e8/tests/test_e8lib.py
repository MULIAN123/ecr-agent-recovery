"""Self-tests for the E8 runner helpers and server tools (E8_FREEZE_PLAN r1 item 4: every r1 runner behaviour has a
self-test). Pure file operations; Linux-only parts are skipped elsewhere. Run: python -I tests/test_e8lib.py
Layout-independent: works in the assembled package (runner/, tools/, src/) and in the development tree
(server/runner, server/tools, tools, c0/src)."""
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CAND = [(ROOT / "runner", ROOT / "tools", ROOT / "src"),
        (ROOT / "runner", ROOT / "tools", ROOT.parent / "c0/src")]  # dev tree: server/{runner,tools} + package c0/src
RUNNER, TOOLS, SRC = next(c for c in CAND if (c[0] / "e8lib.py").is_file() and (c[2] / "endpoint.py").is_file())
DIAG_DIR = TOOLS if (TOOLS / "e8_diag.py").is_file() else ROOT.parent / "tools"
for p in (RUNNER, TOOLS, DIAG_DIR):
    sys.path.insert(0, str(p))
import e8lib  # noqa: E402
import e8_diag  # noqa: E402
import verify_exec_manifest as vem  # noqa: E402


def excl(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o640)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


class Snapshot(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.store, self.audit = self.t / "sut/store", self.t / "attempt/diag/audit"
        self.store.mkdir(parents=True); self.audit.mkdir(parents=True)

    def test_copy_hash_equal_and_outside_store(self):
        (self.store / "task.json").write_bytes(b'{"deliveryState": "adapter_accepted"}')
        (self.store / "queue.json").write_bytes(b'{"pending": false}')
        e = e8lib.snapshot(self.store, self.audit, 1, excl)
        self.assertEqual(e["source_sha256"], e["copy_sha256"])
        self.assertEqual(e["queue_source_sha256"], e["queue_copy_sha256"])
        self.assertEqual((self.audit / "task.R1.json").read_bytes(), (self.store / "task.json").read_bytes())
        self.assertFalse(str(self.audit.resolve()).startswith(str(self.store.resolve())))

    def test_absent_task_recorded(self):
        e = e8lib.snapshot(self.store, self.audit, 2, excl)
        self.assertEqual(e["source_sha256"], "ABSENT")

    def test_never_overwrites(self):
        (self.store / "task.json").write_bytes(b"{}")
        e8lib.snapshot(self.store, self.audit, 1, excl)
        with self.assertRaises(FileExistsError):
            e8lib.snapshot(self.store, self.audit, 1, excl)

    def test_regression_seen_by_snapshots_only(self):
        """W9-like: task.json goes adapter_accepted -> review across recoveries; source (c) must see it."""
        (self.store / "task.json").write_bytes(b'{"deliveryState": "adapter_accepted"}')
        s1 = e8lib.snapshot(self.store, self.audit, 1, excl)
        (self.store / "task.json").write_bytes(b'{"deliveryState": "review"}')
        s2 = e8lib.snapshot(self.store, self.audit, 2, excl)
        excl(self.audit / "snapshots.json", json.dumps([s1, s2]).encode())
        seq = [json.loads((self.audit / f"task.R{k}.json").read_text())["deliveryState"] for k in (1, 2)]
        self.assertEqual(seq, ["adapter_accepted", "review"])


class ArchiveFinal(unittest.TestCase):
    def setUp(self):
        self.store = Path(tempfile.mkdtemp())

    def test_between_recoveries_only(self):
        (self.store / "final.json").write_text('{"task": {"deliveryState": "delivered"}}')
        r = e8lib.archive_final(self.store, 1, 2)
        self.assertEqual(r["action"], "ARCHIVED")
        self.assertTrue((self.store / "final.R1.json").is_file())
        self.assertFalse((self.store / "final.json").exists())
        (self.store / "final.json").write_text('{"task": {"deliveryState": "review"}}')
        r = e8lib.archive_final(self.store, 2, 2)
        self.assertEqual(r["action"], "KEPT_LAST")
        self.assertTrue((self.store / "final.json").is_file())

    def test_absent_between(self):
        self.assertEqual(e8lib.archive_final(self.store, 1, 3)["action"], "NONE_ABSENT")

    def test_refuses_existing_archive(self):
        (self.store / "final.json").write_text("{}")
        (self.store / "final.R1.json").write_text("{}")
        with self.assertRaises(FileExistsError):
            e8lib.archive_final(self.store, 1, 2)

    def test_single_recovery_kept(self):
        (self.store / "final.json").write_text("{}")
        self.assertEqual(e8lib.archive_final(self.store, 1, 1)["action"], "KEPT_LAST")


class ProcessOutcome(unittest.TestCase):
    TB = "Traceback (most recent call last):\n  File \"x\", line 1, in <module>\n{}\n"

    def test_exception_type(self):
        self.assertIsNone(e8lib.exception_type("plain stderr"))
        self.assertEqual(e8lib.exception_type(self.TB.format("json.decoder.JSONDecodeError: Expecting value")), "JSONDecodeError")
        self.assertEqual(e8lib.exception_type(self.TB.format("http.client.RemoteDisconnected: Remote end closed")), "RemoteDisconnected")
        self.assertEqual(e8lib.exception_type(self.TB.format("ConnectionResetError: [Errno 104]")), "ConnectionResetError")

    def test_table_and_agreement_with_e8_diag(self):
        cases = [  # plan, code, exc, fired -> expected
            ({"expected_exit": 137, "crash_at": "after_set_task"}, 137, None, True, "EXPECTED_HOOK"),
            ({"expected_exit": 137, "crash_at": "after_set_task"}, 0, None, False, "UNEXPECTED"),
            ({"expected_exit": 0, "crash_at": None}, 0, None, False, "CLEAN"),
            ({"expected_exit": 0, "crash_at": None}, 0, "KeyError", False, "UNEXPECTED"),
            ({"expected_exit": 0, "crash_at": None}, 1, "MemoryError", False, "UNEXPECTED"),
            ({"expected_exit": 1, "expected_exception": ["JSONDecodeError"], "crash_at": None}, 1, "JSONDecodeError", False, "EXPECTED_INJECTED_EXCEPTION"),
            ({"expected_exit": 1, "expected_exception": ["JSONDecodeError"], "crash_at": None}, 1, "KeyError", False, "UNEXPECTED"),
            ({"expected_exit": 1, "expected_exception": ["RemoteDisconnected", "ConnectionResetError"], "crash_at": None}, 1, "ConnectionResetError", False, "EXPECTED_INJECTED_EXCEPTION"),
            ({"expected_exit": None, "crash_at": None}, 1, "KeyError", False, "UNDECLARED"),
            ({"expected_exit": 0, "crash_at": None}, None, None, False, "UNEXPECTED"),  # timeout
        ]
        for plan, code, exc, fired, want in cases:
            self.assertEqual(e8lib.process_outcome(plan, code, exc, fired), want, (plan, code, exc))
            receipt = {"exit_code": code, "exception_type": exc, "crash_at": plan.get("crash_at") if code == 137 else None}
            self.assertEqual(e8_diag.process_outcome(plan, receipt), want, ("e8_diag", plan, code, exc))


class Sends(unittest.TestCase):
    def test_gated_original_not_counted(self):
        ev = Path(tempfile.mkdtemp())
        (ev / "log-0001-send.json").write_text(json.dumps({"kind": "send", "gated": True}))
        (ev / "log-0002-fence.json").write_text(json.dumps({"kind": "fence"}))
        self.assertEqual(e8lib.count_recovery_sends(ev), 0)
        (ev / "log-0003-send.json").write_text(json.dumps({"kind": "send", "gated": False}))
        self.assertEqual(e8lib.count_recovery_sends(ev), 1)

    def test_wait_until(self):
        self.assertTrue(e8lib.wait_until(lambda: True, 0.1))
        self.assertFalse(e8lib.wait_until(lambda: False, 0.05))


class SeedEffect(unittest.TestCase):
    def test_seed_on_endpoint_schema(self):
        script = re.search(r'db\.executescript\("""(.*?)"""\)', (SRC / "endpoint.py").read_text(), re.S).group(1)
        t = Path(tempfile.mkdtemp()) / "effects.sqlite3"
        with sqlite3.connect(t) as db:
            db.executescript(script)
            db.execute("CREATE UNIQUE INDEX effect_once ON effects(logical_operation_id)")  # P2/P3 profile
            db.execute("INSERT INTO effects(logical_operation_id,attempt_id,transport_request_id,committed) VALUES('K','a','t',1)")
        seed = TOOLS / "seed_effect.py"
        out = subprocess.run([sys.executable, "-I", str(seed), str(t), "K-other", "o-a1", "o-t1"], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(out.stdout)["seeded_logical_operation_id"], "K-other")
        with sqlite3.connect(t) as db:
            rows = db.execute("SELECT logical_operation_id, committed FROM effects ORDER BY seq").fetchall()
            self.assertEqual(rows, [("K", 1), ("K-other", 1)])
            self.assertEqual(db.execute("SELECT count(*) FROM effects WHERE logical_operation_id='K'").fetchone()[0], 1)


class ManifestVerify(unittest.TestCase):
    def test_verify_and_compare(self):
        root = Path(tempfile.mkdtemp())
        (root / "src").mkdir()
        (root / "src/a.py").write_text("x = 1\n")
        m = {"files": {"src/a.py": vem.sha(root / "src/a.py")}, "public_files": ["src/a.py"]}
        self.assertEqual(vem.verify(root, m, True)["status"], "OK")
        (root / "src/b.py").write_text("y\n")
        self.assertEqual(vem.verify(root, m, False)["status"], "FAIL")  # unlisted file
        (root / "src/b.py").unlink()
        (root / "src/a.py").write_text("x = 2\n")
        self.assertEqual(vem.verify(root, m, False)["status"], "FAIL")  # hash drift
        self.assertEqual(vem.compare(m, json.loads(json.dumps(m)))["status"], "EQUIVALENT")
        self.assertEqual(vem.compare(m, {**m, "kernel": "other"})["status"], "DIFFERENT")


class StaticRules(unittest.TestCase):
    SUT = {"agent_v1", "agent_v2", "agent_v3", "authority", "authority_v3", "evidence", "endpoint", "endpoint_fi",
           "scorer", "scorer_v2", "protocol", "plan"}

    def test_diagnostics_import_no_sut_module(self):
        import ast
        for name in ("e8_diag.py", "w6_order.py", "aggregate_e8.py", "vq_e8_checks.py"):
            path = next(p for p in (TOOLS / name, DIAG_DIR / name) if p.is_file())
            mods = set()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    mods |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods.add(node.module.split(".")[0])
            self.assertFalse(mods & self.SUT, (name, mods & self.SUT))

    def test_runner_final_and_snapshot_order(self):
        """run_e8: per recovery, snapshot BEFORE archive, archive via e8lib only, and never a final.json write."""
        text = (RUNNER / "run_e8.py").read_text(encoding="utf-8")
        i_snap = text.index('facts["e8"]["snapshots"].append(e8lib.snapshot(')
        i_arch = text.index('facts["e8"]["archive"].append(e8lib.archive_final(')
        self.assertLess(i_snap, i_arch)
        self.assertNotIn('"final.json").write', text)
        self.assertNotIn("final.json\").unlink", text)

    def test_lanes_give_each_repeat_its_own_attempt_index(self):
        """VQ q1/q2 and formal b1/c1 must be first attempts in separate indices (bl6b/bl6c pattern)."""
        import ast
        tree = ast.parse((RUNNER / "run_e8.py").read_text(encoding="utf-8"))
        lanes = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "LANES" for t in n.targets))
        self.assertEqual(set(lanes), {"ecr-e8q-srv-q1", "ecr-e8q-srv-q2", "ecr-e8x-srv-b1", "ecr-e8x-srv-c1"})
        self.assertNotEqual(lanes["ecr-e8q-srv-q1"][0], lanes["ecr-e8q-srv-q2"][0])
        self.assertNotEqual(lanes["ecr-e8x-srv-b1"][0], lanes["ecr-e8x-srv-c1"][0])
        self.assertEqual({v[1] for k, v in lanes.items() if "e8q" in k}, {"VQ"})
        self.assertEqual({v[1] for k, v in lanes.items() if "e8x" in k}, {"E8"})
        text = (RUNNER / "run_e8.py").read_text(encoding="utf-8")
        self.assertIn('B.BATCH_PARENT = B.BASE / f"async-sim-{lane}"', text)
        self.assertIn("set_lane(args.batch_id", text)

    def test_index_unit_id_safe_and_injective_for_all_units(self):
        """Frozen p3lib.attempt_dirname rejects the apostrophe of the 8 W6' unit IDs; run_e8.index_unit_id must make
        every formal and VQ unit ID index-safe, keep them distinct, and be reversible."""
        import ast
        sys.path.insert(0, str(RUNNER))
        import p3lib
        tree = ast.parse((RUNNER / "run_e8.py").read_text(encoding="utf-8"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "index_unit_id")
        ns = {"p3lib": p3lib}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "run_e8.index_unit_id", "exec"), ns)
        cells_path = next(p for p in (ROOT / "private/cells_e8.json", ROOT.parent / "private/cells_e8.json") if p.is_file())
        cells = json.loads(cells_path.read_text(encoding="utf-8"))
        ids = [u["unit_id"] for u in cells["units"] + cells["qualification_units"]]
        self.assertEqual(len(ids), 286)
        raw_unsafe = 0
        for uid in ids:
            try:
                p3lib.attempt_dirname(p3lib.attempt_id(uid, 1))
            except ValueError:
                raw_unsafe += 1
        self.assertEqual(raw_unsafe, 8)  # the bug this function exists for
        keys = [ns["index_unit_id"](u) for u in ids]
        names = {p3lib.attempt_dirname(p3lib.attempt_id(k, n)) for k in keys for n in (1, 2)}
        self.assertEqual(len(set(keys)), 286)
        self.assertEqual(len(names), 572)
        self.assertEqual([k.replace(".prime", "'") for k in keys], ids)
        self.assertEqual([k for k, u in zip(keys, ids) if k != u and "W6'" not in u], [])

    def test_w6_receipt_order_in_runner(self):
        text = (RUNNER / "run_e8.py").read_text(encoding="utf-8")
        a = text.index('"step": "held_marker_seen"')
        b_ = text.index('"step": "release_late_written"')
        c = text.index('"step": "held_send_logged"')
        self.assertLess(a, b_); self.assertLess(b_, c)


if __name__ == "__main__":
    unittest.main(verbosity=1)
