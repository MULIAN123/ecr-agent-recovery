"""Unit tests for AuthorityV3 quarantine rules (no endpoint, no network). Usage: python3 -B -E -s tests/test_bl6_v3.py
Q1 frozen authority records -> not eligible;  Q2 version conflict -> not eligible;  Q3 R2B key not bound -> not eligible;
foreign record only -> eligible; PRE_DISPATCH never granted in quarantine; FENCED_NEGATIVE needs a negative fence from the
authority records; IDEMPOTENT needs the original first-dispatch key; an endpoint commit blocks every quarantined action.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
import protocol  # noqa: E402

if not hasattr(os, "O_DIRECTORY"):  # Windows host: same write without the directory fsync (test only)
    def _dj(path, value, *, replace=False):
        path = Path(path)
        if path.exists() and not replace:
            raise FileExistsError(path)
        path.write_bytes(protocol.canonical(value))
    protocol.durable_json = _dj
import authority  # noqa: E402
import authority_v3  # noqa: E402
if not hasattr(os, "O_DIRECTORY"):
    authority.durable_json = protocol.durable_json

KEY = "U-op"
BASE = {"taskId": "U-task", "attemptId": "U-a1", "deliverableId": "U-del", "artifactSetId": "U-set", "artifactId": "U-art",
        "bindingGeneration": 1, "taskStateVersion": 1}


class T(unittest.TestCase):
    def store(self, mode="R2A", version_bump=False, foreign=True, frozen=False, r2b_key=KEY, committed=False, negative_fence=False):
        root = Path(tempfile.mkdtemp(prefix="bl6v3-"))
        j = root / "journal"
        j.mkdir()

        def ev(i, t, **x):
            (j / f"{i:04d}-{t}.json").write_text(json.dumps({**BASE, "eventType": t, "eventId": f"U-a1-e{i}", "journalSequence": i, **x}))
        a = authority_v3.AuthorityV3(root, mode, KEY, {"query": True, "idempotent": True}, 0)
        if mode == "R2B":
            ev(1, "AttemptCommitted", **({"logicalOperationId": r2b_key, "operationVersion": 1} if r2b_key else {}))
        else:
            ev(1, "AttemptCommitted")
        ev(2, "QueueBindingPrepared")
        (root / "queue.json").write_text(json.dumps({**BASE, "queueItemId": "U-q", "pending": True}))
        ev(3, "QueueItemPersisted", queueItemId="U-q")
        g = a._write("grants", "g1", {"kind": "grant", "grant_id": "g1", "basis": "PRE_DISPATCH", "logical_operation_id": KEY,
                                      "send_attempt_id": "U-t1", "generation": 0, "first_dispatch": True})
        a._write("consumed", "g1", {"kind": "consumed", "grant_id": "g1", "logical_operation_id": KEY, "send_attempt_id": "U-t1", "generation": 0})
        if mode == "R2B":
            ev(4, "DispatchBoundaryCrossed", queueItemId="U-q", grantId="g1", logicalOperationId=KEY, generation=0, sendAttemptId="U-t1")
        else:
            ev(4, "DispatchBoundaryCrossed", queueItemId="U-q")
        task = {**BASE, "deliveryState": "pending"}
        if version_bump:
            task["taskStateVersion"] = 2
        (root / "task.json").write_text(json.dumps(task))
        if foreign:
            (j / "0005-AdapterOutcomeObserved.json").write_text(json.dumps({**BASE, "attemptId": "U-a1-stale", "artifactId": "U-art-v0",
                "eventType": "AdapterOutcomeObserved", "eventId": "U-a1-stale-e5", "journalSequence": 5, "queueItemId": "U-q",
                "outcome": "definite_failure", "evidenceLevel": "adapter_rejected"}))
        if frozen:
            a._write("consumed", "ghost", {"kind": "consumed", "grant_id": "ghost", "logical_operation_id": KEY})
        if committed:
            a._log("result", {"grant_id": "g1", "logical_operation_id": KEY, "status": "COMMITTED"})
        if negative_fence:
            a._write("fences", "00099", {"kind": "fence", "fence_id": "00099", "logical_operation_id": KEY, "http_status": 200,
                                         "response": {"result": "DEFINITELY_NOT_COMMITTED", "retry_generation": 1}})
        return a

    def req(self, gen=0, n=2):
        return {"logical_operation_id": KEY, "attempt_id": f"U-a1-r{n}", "transport_request_id": f"U-t1-r{n}", "generation": gen}

    def test_foreign_only_is_eligible_and_v2_gate_denies(self):
        a = self.store()
        self.assertTrue(a.quarantine_eligibility()["eligible"])
        self.assertFalse(a.report().valid)  # the v2 validator rejects the local evidence

    def test_version_conflict_blocks(self):
        q = self.store(version_bump=True).quarantine_eligibility()
        self.assertFalse(q["eligible"])
        self.assertTrue(any(b.startswith("Q2") for b in q["blockers"]))

    def test_frozen_blocks(self):
        q = self.store(frozen=True).quarantine_eligibility()
        self.assertFalse(q["eligible"])
        self.assertTrue(any(b.startswith("Q1") for b in q["blockers"]))

    def test_r2b_unbound_key_blocks(self):
        q = self.store(mode="R2B", r2b_key=None).quarantine_eligibility()
        self.assertFalse(q["eligible"])
        self.assertTrue(any(b.startswith("Q3") for b in q["blockers"]))

    def test_r2b_bound_key_eligible(self):
        self.assertTrue(self.store(mode="R2B").quarantine_eligibility()["eligible"])

    def test_pre_dispatch_never_granted(self):
        self.assertIsNone(self.store().authorize_quarantined("PRE_DISPATCH", self.req()))

    def test_fenced_negative_needs_authority_fence(self):
        self.assertIsNone(self.store().authorize_quarantined("FENCED_NEGATIVE", self.req(gen=1)))
        a = self.store(negative_fence=True)
        self.assertIsNone(a.authorize_quarantined("FENCED_NEGATIVE", self.req(gen=0)))  # wrong generation
        g = a.authorize_quarantined("FENCED_NEGATIVE", self.req(gen=1, n=3))
        self.assertIsNotNone(g)
        self.assertTrue(g["quarantined"])

    def test_idempotent_needs_original_key(self):
        g = self.store().authorize_quarantined("IDEMPOTENT", self.req())
        self.assertIsNotNone(g)
        self.assertIsNone(self.store().authorize_quarantined("IDEMPOTENT", self.req(gen=1)))

    def test_endpoint_commit_blocks_everything(self):
        a = self.store(committed=True, negative_fence=True)
        self.assertIsNone(a.authorize_quarantined("IDEMPOTENT", self.req()))
        self.assertIsNone(a.fence_quarantined())

    def test_version_conflict_blocks_sends(self):
        a = self.store(version_bump=True, negative_fence=True)
        self.assertIsNone(a.authorize_quarantined("IDEMPOTENT", self.req()))
        self.assertIsNone(a.authorize_quarantined("FENCED_NEGATIVE", self.req(gen=1)))
        self.assertIsNone(a.fence_quarantined())


if __name__ == "__main__":
    unittest.main(verbosity=1)
