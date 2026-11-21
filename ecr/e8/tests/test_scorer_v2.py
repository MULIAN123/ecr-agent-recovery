"""T9: scorer_v2 judges send bases from endpoint logs, parameters and truth, never from labels."""
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import fixtures as fx
from scorer_v2 import score

K = "T-op"


class Fixture:
    def __init__(self, root: Path, profile: str, dispatched: bool = True, authority: bool = False):
        self.root, self.profile, self.dispatched = root, profile, dispatched
        (root / "ev").mkdir(parents=True)
        self.n = 0
        self.db = root / "ledger.sqlite3"
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE effects (seq INTEGER PRIMARY KEY AUTOINCREMENT, logical_operation_id TEXT, attempt_id TEXT,"
                       " transport_request_id TEXT, committed INTEGER)")
        self.store = root / "sut/store"
        (self.store / "recovery").mkdir(parents=True)
        (self.store / "journal").mkdir()
        if authority:
            for d in ("grants", "consumed", "log"):
                (self.store / "authority" / d).mkdir(parents=True)
        self.final("adapter_accepted")

    def final(self, state):
        p = self.store / "final.json"
        p.write_text(json.dumps({"task": {"deliveryState": state}}))

    def log(self, kind, **value):
        self.n += 1
        (self.root / "ev" / f"log-{self.n:04d}-{kind}.json").write_text(json.dumps({"index": self.n, "kind": kind, **value}))

    def send(self, tid, gen=0, key=K, gated=False, effect=True, label=None):
        self.log("send", request={"logical_operation_id": key, "attempt_id": "a", "transport_request_id": tid,
                                  "generation": gen}, gated=gated, result={"status": "COMMITTED"})
        if effect:
            with sqlite3.connect(self.db) as db:
                db.execute("INSERT INTO effects(logical_operation_id,attempt_id,transport_request_id,committed) VALUES (?,?,?,1)",
                           (key, "a", tid))
        if label:
            (self.store / "recovery" / f"{self.n:03d}.json").write_text(json.dumps(
                {"action": "AUTHORIZE_SEND", "guarantee": label, "request": {"transport_request_id": tid}}))

    def fence(self, result="DEFINITELY_NOT_COMMITTED", gen=1):
        self.log("fence", logical_operation_id=K, result={"result": result, "retry_generation": gen})

    def score(self, opp=True, permitted=True):
        truth = {"scenario_id": "x", "barrier_id": "B2", "profile": self.profile, "dispatched": self.dispatched,
                 "max_allowed_effects": 1, "send_permitted": permitted, "safe_opportunity": opp}
        return score({"run_id": "x", "truth": truth, "logical_operation_id": K, "ledger": str(self.db),
                      "endpoint_events": str(self.root / "ev"), "sut_copy": str(self.root / "sut"), "injection": "",
                      "dev_mode": True})


class ScorerV2(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ecr-r2-scorer-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fx(self, name, profile, **kw):
        return Fixture(self.tmp / name, profile, **kw)

    def test_valid_fenced_send(self):
        f = self.fx("a", "P1")
        f.send("t1", gated=True, effect=False)
        f.fence(gen=1)
        f.send("r1", gen=1, label="FENCED_NEGATIVE")
        s = f.score()
        self.assertEqual(s["s4_invalid_sends"], 0)
        self.assertEqual(s["sends_judged"][0]["objective_basis"], "FENCED_NEGATIVE")
        self.assertTrue(s["sends_judged"][0]["claimed_basis_verified"])
        self.assertTrue(s["opportunity_completed"])

    def test_generation_mismatch_invalid(self):
        f = self.fx("b", "P1")
        f.send("t1", gated=True, effect=False)
        f.fence(gen=1)
        f.send("r1", gen=0, label="FENCED_NEGATIVE")
        s = f.score()
        self.assertEqual(s["s4_invalid_sends"], 1)
        self.assertFalse(s["sends_judged"][0]["claimed_basis_verified"])

    def test_fence_used_twice_invalid(self):
        f = self.fx("c", "P1")
        f.send("t1", gated=True, effect=False)
        f.fence(gen=1)
        f.send("r1", gen=1, effect=False, label="FENCED_NEGATIVE")
        f.send("r2", gen=1, label="FENCED_NEGATIVE")
        self.assertEqual(f.score()["s4_invalid_sends"], 1)

    def test_forged_label_without_fence(self):
        f = self.fx("d", "P1")
        f.send("t1", gated=True)
        f.send("r1", gen=0, label="FENCED_NEGATIVE")
        s = f.score()
        self.assertEqual(s["s4_invalid_sends"], 1)
        self.assertTrue(s["duplicate_effect"])
        self.assertFalse(s["opportunity_completed"])

    def test_idempotent_needs_contract_and_key(self):
        for profile, ok in (("P1", False), ("P2", True), ("P3", True)):
            f = self.fx("e" + profile, profile)
            f.send("t1", gated=True, effect=False)
            f.send("r1", label="IDEMPOTENT")
            self.assertEqual(f.score()["s4_invalid_sends"], 0 if ok else 1, profile)
        f = self.fx("e-key", "P2")
        f.send("t1", key="OTHER-op", gated=True, effect=False)
        f.send("r1", label="IDEMPOTENT")
        self.assertEqual(f.score()["s4_invalid_sends"], 1, "original request used another key")

    def test_pre_dispatch_only_when_not_dispatched(self):
        f = self.fx("f", "P0", dispatched=True)
        f.send("r1", label="PRE_DISPATCH")
        self.assertEqual(f.score()["s4_invalid_sends"], 1)
        g = self.fx("g", "P0", dispatched=False)
        g.send("r1", label="PRE_DISPATCH")
        self.assertEqual(g.score()["s4_invalid_sends"], 0)

    def test_not_permitted(self):
        f = self.fx("h", "P3", dispatched=False)
        f.send("r1", label="PRE_DISPATCH")
        self.assertEqual(f.score(permitted=False)["s4_invalid_sends"], 1)

    def test_send_without_grant_is_integrity_fault(self):
        f = self.fx("i", "P2", authority=True)
        f.send("t1", gated=True, effect=False)
        f.send("r1", label="IDEMPOTENT")
        s = f.score()
        self.assertIn("SEND_WITHOUT_GRANT", s["faults"])

    def test_unclaimed_send_is_invalid_and_diagnostics_unverifiable(self):
        f = self.fx("j", "P2")
        f.send("t1", gated=True, effect=False)
        f.send("r1")
        s = f.score()
        j = s["sends_judged"][0]
        self.assertEqual(s["s4_invalid_sends"], 1)
        self.assertEqual(j["objective_basis"], "IDEMPOTENT")
        self.assertEqual(j["label_grant_consistent"], "UNVERIFIABLE")
        self.assertEqual(j["grant_binding"], "UNVERIFIABLE")

    def test_blind_resend_deduplicated_is_still_s4_violation(self):
        f = self.fx("k", "P2")
        f.send("t1", gated=True)
        f.send("r1", label="NONE", effect=False)
        s = f.score()
        self.assertEqual(s["s4_invalid_sends"], 1)
        self.assertEqual(s["s4_objective_basis_existed_but_not_claimed"], 1)
        self.assertFalse(s["duplicate_effect"])


if __name__ == "__main__":
    unittest.main()
