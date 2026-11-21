"""Unit tests T1-T6 for evidence.py and authority.py (design v2, section 7)."""
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import fixtures as fx
from authority import Authority
from evidence import load_state, validate
from protocol import durable_json

SRC = fx.SRC
P = {"P0": {"query": False, "idempotent": False}, "P1": {"query": True, "idempotent": False},
     "P2": {"query": False, "idempotent": True}, "P3": {"query": True, "idempotent": True}}
REQ0 = {"logical_operation_id": "T-op", "attempt_id": "T-a1", "transport_request_id": "T-t1", "generation": 0}


class Endpoint:
    def __init__(self, root: Path, profile: str):
        for d in ("events", "control", "data"):
            (root / d).mkdir(parents=True)
        cfg = {"run_id": "unit", "profile": profile, "db": str(root / "data/effects.sqlite3"), "gated_transport": "none",
               "events": str(root / "events"), "control": str(root / "control")}
        (root / "cfg.json").write_text(json.dumps(cfg))
        self.root = root
        self.proc = subprocess.Popen([sys.executable, str(SRC / "endpoint.py"), str(root / "cfg.json")], cwd=SRC,
                                     stdout=subprocess.DEVNULL, stderr=open(root / "endpoint.stderr", "wb"))
        deadline = time.monotonic() + 15
        while not (root / "events/ready.json").exists():
            if time.monotonic() > deadline:
                raise TimeoutError("endpoint")
            time.sleep(0.01)
        self.port = json.loads((root / "events/ready.json").read_text())["port"]

    def effects(self) -> int:
        with sqlite3.connect(self.root / "data/effects.sqlite3") as db:
            return db.execute("SELECT count(*) FROM effects").fetchone()[0]

    def stop(self):
        durable_json(self.root / "control/stop", {})
        self.proc.wait(timeout=10)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ecr-r2-unit-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def store(self, mode, upto, **kw):
        return fx.write_store(self.tmp, fx.events(mode, upto, **kw))

    def auth(self, store, mode, profile="P0", port=1):
        return Authority(store, mode, "T-op", P[profile], port)

    def write_grant(self, a: Authority, basis="PRE_DISPATCH", first=True, consumed=True, gid="g1", gen=0, send="T-t1"):
        g = a._write("grants", gid, {"kind": "grant", "grant_id": gid, "basis": basis, "logical_operation_id": "T-op",
                                     "generation": gen, "send_attempt_id": send, "first_dispatch": first, "attempt_id": "T-a1"})
        if consumed:
            a._write("consumed", gid, {"kind": "consumed", "grant_id": gid, "logical_operation_id": "T-op",
                                       "send_attempt_id": send, "generation": gen})
        return g

    def dispatched_store(self, mode, profile, outcome=None, level=None):
        """Valid store after a consumed first dispatch (R2B: journal dispatch bound to the grant)."""
        root = self.tmp
        store = fx.write_store(root, fx.events(mode, 3))
        a = self.auth(store, mode, profile)
        g = self.write_grant(a)
        e = fx.events(mode, 4, grant=g, outcome=outcome, level=level)
        for i, ev in enumerate(e[3:], start=4):
            durable_json(store / "journal" / f"{i:04d}-{ev['eventType']}.json", ev)
        return store, a


class T1FirstDispatch(Base):
    def run_mode(self, mode):
        ep = Endpoint(self.tmp / "ep", "P0")
        try:
            store = self.store(mode, 3)
            a = Authority(store, mode, "T-op", P["P0"], ep.port)
            g = a.authorize("PRE_DISPATCH", REQ0, first_dispatch=True)
            self.assertIsNotNone(g, "first dispatch must be authorized before any dispatch evidence exists")

            def writer(grant):
                ev = fx.events(mode, 4, grant=grant)[3]
                durable_json(store / "journal" / "0004-DispatchBoundaryCrossed.json", ev)

            r = a.dispatch(g, journal_writer=writer)
            self.assertEqual(r.get("status"), "COMMITTED")
            self.assertEqual(ep.effects(), 1)
            rep = validate(load_state(store), mode, "T-op")
            self.assertTrue(rep.valid, rep.reasons)
            self.assertTrue(rep.dispatched)
            a2 = Authority(store, mode, "T-op", P["P0"], ep.port)
            self.assertIsNone(a2.authorize("PRE_DISPATCH", {**REQ0, "transport_request_id": "T-t2"}))
        finally:
            ep.stop()

    def test_r2a(self):
        self.run_mode("R2A")

    def test_r2b(self):
        self.run_mode("R2B")


class T2SingleUse(Base):
    def test_grant_consumed_once_and_not_by_other_process(self):
        ep = Endpoint(self.tmp / "ep", "P2")
        try:
            store = self.store("R2B", 3)
            a = Authority(store, "R2B", "T-op", P["P2"], ep.port)
            g = a.authorize("PRE_DISPATCH", REQ0, first_dispatch=True)
            a.dispatch(g, journal_writer=lambda gr: durable_json(
                store / "journal/0004-DispatchBoundaryCrossed.json", fx.events("R2B", 4, grant=gr)[3]))
            with self.assertRaises(PermissionError):
                a.dispatch(g)
            with self.assertRaises(PermissionError):
                Authority(store, "R2B", "T-op", P["P2"], ep.port).dispatch(g)
            self.assertEqual(ep.effects(), 1)
        finally:
            ep.stop()

    def test_orphan_grant_voided_and_not_dispatched(self):
        store = self.store("R2A", 3)
        a = self.auth(store, "R2A")
        g = a.authorize("PRE_DISPATCH", REQ0, first_dispatch=True)
        b = self.auth(store, "R2A")
        self.assertEqual(b.void_orphans(), [g["grant_id"]])
        with self.assertRaises(PermissionError):
            a.dispatch(g)
        rep = b.report()
        self.assertTrue(rep.valid, rep.reasons)
        self.assertFalse(rep.dispatched, "an unconsumed (void) grant is not dispatch evidence")
        self.assertIsNotNone(b.authorize("PRE_DISPATCH", {**REQ0, "transport_request_id": "T-t2"}, first_dispatch=True))

    def test_consumed_marker_without_journal_dispatch_counts_as_dispatched(self):
        store = self.store("R2B", 3)
        a = self.auth(store, "R2B")
        self.write_grant(a)
        rep = a.report()
        self.assertTrue(rep.valid, rep.reasons)
        self.assertTrue(rep.dispatched)
        self.assertIsNone(a.authorize("PRE_DISPATCH", {**REQ0, "transport_request_id": "T-t2"}))


class T3LocalFailure(Base):
    def test_local_failure_never_a_basis(self):
        for profile in ("P0", "P1", "P2", "P3"):
            for mode in ("R2A", "R2B"):
                with self.subTest(profile=profile, mode=mode):
                    shutil.rmtree(self.tmp)
                    self.tmp.mkdir()
                    store, a = self.dispatched_store(mode, profile, outcome="definite_failure", level="adapter_rejected")
                    rep = a.report()
                    self.assertTrue(rep.valid, rep.reasons)
                    self.assertTrue(rep.local_failure)
                    req = {**REQ0, "transport_request_id": "T-r1"}
                    self.assertIsNone(a.authorize("CLASSIFIED_DEFINITE_FAILURE", req))
                    self.assertIsNone(a.authorize("RECORDED_FAILURE", req))
                    self.assertIsNone(a.authorize("PRE_DISPATCH", req))
                    self.assertIsNone(a.authorize("FENCED_NEGATIVE", req), "no fence record exists")
                    g = a.authorize("IDEMPOTENT", req)
                    self.assertEqual(g is not None, P[profile]["idempotent"])


class T4FencedNegative(Base):
    def fence(self, a, result="DEFINITELY_NOT_COMMITTED", gen=1, key="T-op"):
        fid = f"{a._next_seq():05d}"
        a._write("fences", fid, {"kind": "fence", "fence_id": fid, "logical_operation_id": key, "http_status": 200,
                                 "response": {"result": result, "retry_generation": gen}})
        return fid

    def test_conditions(self):
        req = {**REQ0, "transport_request_id": "T-r1", "generation": 1}
        cases = {
            "no fence": (lambda a: None, req, "P1", False),
            "other key": (lambda a: self.fence(a, key="X-op"), req, "P1", False),
            "not negative": (lambda a: self.fence(a, result="UNKNOWN"), req, "P1", False),
            "generation mismatch": (lambda a: self.fence(a, gen=2), req, "P1", False),
            "no query contract": (lambda a: self.fence(a), req, "P2", False),
            "dispatch after fence": (lambda a: (self.fence(a), self.write_grant(a, basis="FENCED_NEGATIVE", first=False,
                                                                               gid="g2", gen=1, send="T-r0")), req, "P1", False),
            "valid": (lambda a: self.fence(a), req, "P1", True),
            "valid P3": (lambda a: self.fence(a), req, "P3", True),
        }
        for name, (setup, r, profile, ok) in cases.items():
            for mode in ("R2A", "R2B"):
                with self.subTest(case=name, mode=mode):
                    shutil.rmtree(self.tmp)
                    self.tmp.mkdir()
                    store, a = self.dispatched_store(mode, profile)
                    setup(a)
                    self.assertEqual(a.authorize("FENCED_NEGATIVE", r) is not None, ok, a.report().reasons)

    def test_fence_authorizes_at_most_one_send(self):
        store, a = self.dispatched_store("R2B", "P1")
        self.fence(a)
        req = {**REQ0, "transport_request_id": "T-r1", "generation": 1}
        g = a.authorize("FENCED_NEGATIVE", req)
        self.assertIsNotNone(g)
        a._write("consumed", g["grant_id"], {"kind": "consumed", "grant_id": g["grant_id"], "logical_operation_id": "T-op",
                                             "send_attempt_id": "T-r1", "generation": 1})
        self.assertIsNone(a.authorize("FENCED_NEGATIVE", {**req, "transport_request_id": "T-r2"}))


class T5Idempotent(Base):
    def test_conditions(self):
        for profile, ok in (("P0", False), ("P1", False), ("P2", True), ("P3", True)):
            for mode in ("R2A", "R2B"):
                with self.subTest(profile=profile, mode=mode):
                    shutil.rmtree(self.tmp)
                    self.tmp.mkdir()
                    store, a = self.dispatched_store(mode, profile)
                    req = {**REQ0, "transport_request_id": "T-r1"}
                    self.assertEqual(a.authorize("IDEMPOTENT", req) is not None, ok)
                    self.assertIsNone(a.authorize("IDEMPOTENT", {**req, "logical_operation_id": "X-op"}))
                    self.assertIsNone(a.authorize("IDEMPOTENT", {**req, "generation": 1}))

    def test_repeated_idempotent_retries_not_blocked_by_earlier_grants(self):
        store, a = self.dispatched_store("R2B", "P2")
        g1 = a.authorize("IDEMPOTENT", {**REQ0, "transport_request_id": "T-r1"})
        a._write("consumed", g1["grant_id"], {"kind": "consumed", "grant_id": g1["grant_id"], "logical_operation_id": "T-op",
                                              "send_attempt_id": "T-r1", "generation": 0})
        self.assertIsNotNone(a.authorize("IDEMPOTENT", {**REQ0, "transport_request_id": "T-r2"}))

    def test_no_send_after_acceptance(self):
        store, a = self.dispatched_store("R2B", "P3", outcome="adapter_accepted", level="adapter_accepted")
        for basis in ("IDEMPOTENT", "PRE_DISPATCH"):
            self.assertIsNone(a.authorize(basis, {**REQ0, "transport_request_id": "T-r1"}))
        self.assertFalse(a.authorize_fence()[0])


class T6Version(Base):
    def test_r2b_version_pinned(self):
        store, a = self.dispatched_store("R2B", "P2")
        task = fx.read(store / "task.json")
        durable_json(store / "task.json", {**task, "taskStateVersion": 2}, replace=True)
        self.assertIsNone(a.authorize("IDEMPOTENT", {**REQ0, "transport_request_id": "T-r1"}))

    def test_r2a_versions_must_agree(self):
        store = self.store("R2A", 3)
        task = fx.read(store / "task.json")
        durable_json(store / "task.json", {**task, "taskStateVersion": 2}, replace=True)
        self.assertIsNone(self.auth(store, "R2A").authorize("PRE_DISPATCH", REQ0, first_dispatch=True))


class ValidatorChecks(Base):
    def test_ev_failures(self):
        good = fx.events("R2B", 4, grant={"grant_id": "g1", "logical_operation_id": "T-op", "generation": 0, "send_attempt_id": "T-t1"})
        cases = {
            "EV3 sequence": lambda e: e[2].__setitem__("journalSequence", 1),
            "EV4 order": lambda e: e.insert(1, dict(e[3], eventId="dup")),
            "EV1 foreign attempt": lambda e: e[3].__setitem__("attemptId", "other"),
            "EV1 missing field": lambda e: e[1].pop("artifactId"),
            "EV5 outcome without dispatch": lambda e: e.__setitem__(3, {**e[3], "eventType": "AdapterOutcomeObserved",
                                                                          "outcome": "definite_failure"}),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                e = json.loads(json.dumps(good))
                mutate(e)
                st = {"events": e, "task": {**fx.ids(), "deliveryState": "pending"},
                      "queue": {**fx.ids(), "queueItemId": "T-queue", "pending": True},
                      "grants": [{"grant_id": "g1", "logical_operation_id": "T-op", "generation": 0,
                                  "send_attempt_id": "T-t1", "first_dispatch": True}],
                      "consumed": [{"grant_id": "g1", "logical_operation_id": "T-op", "authority_seq": 2}],
                      "void": [], "fences": [], "log": []}
                self.assertFalse(validate(st, "R2B", "T-op").valid)

    def test_anomaly_freezes(self):
        store = self.store("R2B", 3)
        a = self.auth(store, "R2B", "P3")
        a._write("consumed", "ghost", {"kind": "consumed", "grant_id": "ghost", "logical_operation_id": "T-op"})
        rep = a.report()
        self.assertTrue(rep.frozen)
        for basis in ("PRE_DISPATCH", "IDEMPOTENT", "FENCED_NEGATIVE"):
            self.assertIsNone(a.authorize(basis, {**REQ0, "transport_request_id": "T-r1"}))
        self.assertFalse(a.authorize_fence()[0])

    def test_r2b_key_must_match_configuration(self):
        store = fx.write_store(self.tmp, fx.events("R2B", 3, key="OTHER-op"))
        rep = self.auth(store, "R2B").report()
        self.assertFalse(rep.valid)
        self.assertTrue(rep.frozen)


if __name__ == "__main__":
    unittest.main()
