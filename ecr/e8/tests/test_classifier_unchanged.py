"""T8: R2-B journal fields do not change the frozen classifier / planner output.

For representative durable states, the plan computed from the R2-B journal must equal the
plan computed from the R2-A (= historical) journal, for the frozen FULL classifier and the
three ablated variants used in Experiment 2.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import fixtures as fx

SRC = fx.SRC
PKG = SRC.parent
NODE = os.environ.get("ECR_NODE", "/opt/ecr-followup-r1-runtime/node-v24.21.0/bin/node")
ARMS = ("FULL", "A_OUTCOME_BOUNDARY", "A_UNKNOWN_PRESERVATION", "A_ACCEPTANCE_LEVEL")
GRANT = {"grant_id": "g1", "logical_operation_id": "T-op", "generation": 0, "send_attempt_id": "T-t1"}


def projection(base):
    keys = fx.KEYS
    return {"kind": "DISCOVERED", "preparationState": "FULLY_PREPARED", "queueBinding": {
        "schemaVersion": 1, "correlationId": base["queueBindingId"], "opaqueMetadata": {
            "queueBindingId": base["queueBindingId"], "attempts": [{k: base[k] for k in keys}]}}}


def plan(arm, snapshot, proj):
    out = subprocess.run([NODE, str(SRC / "plan.mjs"), str(PKG / "arms" / arm)], cwd=PKG / "arms" / arm,
                         input=json.dumps({"snapshot": snapshot, "projection": proj}), capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[-1500:])
    return json.loads(out.stdout)["plan"]


def states():
    base = fx.ids()
    task = {**base, "deliveryState": "pending"}
    queue = {**base, "queueItemId": "T-queue", "pending": True}
    variants = {
        "B1 pre-dispatch": (3, None, None, task, queue),
        "B2-B5 dispatched": (4, None, None, task, queue),
        "B6 accepted": (4, "adapter_accepted", "adapter_accepted", task, queue),
        "F3 weak ack": (4, "adapter_accepted", "transport_ack", task, queue),
        "F7 local failure": (4, "definite_failure", "adapter_rejected", task, queue),
        "F5 superseded": (3, None, None, {**task, "taskStateVersion": 2}, queue),
    }
    for name, (upto, outcome, level, t, q) in variants.items():
        yield name, {m: {"taskContext": t, "journalAttempt": {"events": fx.events(m, upto, grant=GRANT, outcome=outcome, level=level)},
                         "queueItem": q} for m in ("R2A", "R2B")}


class ClassifierUnchanged(unittest.TestCase):
    def test_r2b_fields_do_not_change_plans(self):
        proj = projection(fx.ids())
        for name, snaps in states():
            for arm in ARMS:
                with self.subTest(state=name, arm=arm):
                    a = plan(arm, snaps["R2A"], proj)
                    b = plan(arm, snaps["R2B"], proj)
                    self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
