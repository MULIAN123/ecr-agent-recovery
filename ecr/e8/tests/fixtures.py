"""Builders for durable stores in the exact formats written by agent_v1.send / agent_v2.send."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
from protocol import durable_json  # noqa: E402

KEYS = ("taskId", "attemptId", "deliverableId", "artifactSetId", "artifactId", "bindingGeneration", "taskStateVersion")


def ids(name="T"):
    return {"artifactId": f"{name}-artifact", "artifactSetId": f"{name}-set", "attemptId": f"{name}-a1",
            "bindingGeneration": 1, "deliverableId": f"{name}-deliverable", "queueBindingId": f"{name}-binding",
            "taskId": f"{name}-task", "taskStateVersion": 1}


def events(mode: str, upto: int, *, base=None, key="T-op", grant=None, outcome=None, level=None, queue_item="T-queue"):
    """Journal events 1..upto (4 = dispatch). R2B adds the key/version and grant binding."""
    base = base or ids()
    out = []

    def ev(seq, etype, **extra):
        out.append({**base, "eventType": etype, "eventId": f"{base['attemptId']}-e{seq}", "journalSequence": seq, **extra})

    if upto >= 1:
        ev(1, "AttemptCommitted", **({"logicalOperationId": key, "operationVersion": base["taskStateVersion"]} if mode == "R2B" else {}))
    if upto >= 2:
        ev(2, "QueueBindingPrepared")
    if upto >= 3:
        ev(3, "QueueItemPersisted", queueItemId=queue_item)
    if upto >= 4:
        extra = {"queueItemId": queue_item}
        if mode == "R2B" and grant is not None:
            extra.update(grantId=grant["grant_id"], logicalOperationId=grant["logical_operation_id"],
                         generation=grant["generation"], sendAttemptId=grant["send_attempt_id"])
        ev(4, "DispatchBoundaryCrossed", **extra)
    if outcome is not None:
        ev(5, "AdapterOutcomeObserved", queueItemId=queue_item, outcome=outcome, evidenceLevel=level)
    return out


def write_store(root: Path, evs: list[dict], *, base=None, queue_pending=True, with_queue=True) -> Path:
    base = base or ids()
    store = root / "store"
    (store / "journal").mkdir(parents=True)
    (store / "recovery").mkdir()
    durable_json(store / "task.json", {**base, "deliveryState": "pending"})
    if with_queue:
        durable_json(store / "queue.json", {**base, "queueItemId": "T-queue", "pending": queue_pending})
    for i, e in enumerate(evs, start=1):
        durable_json(store / "journal" / f"{i:04d}-{e['eventType']}.json", e)
    return store


def state(store: Path) -> dict:
    from evidence import load_state
    return load_state(store)


def read(path: Path):
    return json.loads(Path(path).read_text())
