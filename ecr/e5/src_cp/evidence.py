"""Read-only Evidence Validator for EBRQ-v2 (ECR technical fix, async_sim_r2).

Independent of the frozen classifier: it neither imports nor calls it. It checks the
evidence that a send authorization depends on (design v2, section 2):
  EV1 identity, EV2 version, EV3 journal sequence and event ids, EV4 event order,
  EV5 dispatch evidence (and, in R2-B, its binding to a grant), EV6 outcome/confirmation
  records, EV7 consistency of the authority records.
It reports facts; it never authorizes anything by itself (see authority.py).

Modes
  R2B: the logical operation key K and the operation version V are read from the durable
       AttemptCommitted event and must agree with the configured key; the journal
       DispatchBoundaryCrossed event must name its grant.
  R2A: the old journal format; K comes from configuration (assumption A4), V is not
       pinned (versions must only agree), and the journal dispatch event is linked to the
       first-dispatch grant by record order, not by id. R2A is therefore not a fully
       independent contract executor.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

RANK = {"AttemptCommitted": 1, "QueueBindingPrepared": 2, "QueueItemPersisted": 3,
        "DispatchBoundaryCrossed": 4, "AdapterOutcomeObserved": 5}
STRING_IDS = ("taskId", "attemptId", "deliverableId", "artifactSetId", "artifactId")
INT_IDS = ("bindingGeneration", "taskStateVersion")
IDENTITY_FIELDS = STRING_IDS + INT_IDS
AGREE_FIELDS = IDENTITY_FIELDS + ("queueItemId",)


def _read_json(path: Path):
    return json.loads(path.read_text())


def load_state(store: Path) -> dict:
    """Read the durable state the executor is allowed to read. Never the ledger or truth."""
    journal = store / "journal"
    authority = store / "authority"

    def records(sub: str) -> list[dict]:
        folder = authority / sub
        return [_read_json(p) for p in sorted(folder.glob("*.json"))] if folder.is_dir() else []

    return {
        "events": [_read_json(p) for p in sorted(journal.glob("*.json"))] if journal.is_dir() else [],
        "task": _read_json(store / "task.json") if (store / "task.json").is_file() else None,
        "queue": _read_json(store / "queue.json") if (store / "queue.json").is_file() else None,
        "grants": records("grants"),
        "consumed": records("consumed"),
        "void": records("void"),
        "fences": records("fences"),
        "log": records("log"),
    }


@dataclass
class Report:
    mode: str
    valid: bool = True
    reasons: list = field(default_factory=list)
    frozen: bool = False            # authority-record anomaly: no authorization of any kind
    key: str | None = None          # bound logical operation key K
    version: int | None = None      # bound operation version V (R2B) or agreed version (R2A)
    journal_dispatch: dict | None = None
    dispatched: bool = False        # any evidence that a send under K may have happened
    accepted: bool = False          # an adapter-level acceptance or endpoint-reported commit exists
    local_failure: bool = False     # a locally recorded definite failure exists (never authoritative)
    weak_outcome: bool = False
    queue_pending: bool = False
    consumed_grants: list = field(default_factory=list)
    unconsumed_fence: dict | None = None
    original_key: str | None = None  # key under which the first dispatch was made

    def fail(self, reason: str, anomaly: bool = False) -> None:
        self.valid = False
        self.reasons.append(reason)
        if anomaly:
            self.frozen = True

    def summary(self) -> dict:
        return {k: getattr(self, k) for k in ("mode", "valid", "reasons", "frozen", "key", "version", "dispatched",
                                               "accepted", "local_failure", "weak_outcome", "queue_pending",
                                               "original_key")} | {
            "consumed_grants": [g["grant_id"] for g in self.consumed_grants],
            "unconsumed_fence": (self.unconsumed_fence or {}).get("fence_id")}


def validate(state: dict, mode: str, config_key: str) -> Report:
    if mode not in ("R2A", "R2B"):
        raise ValueError("mode must be R2A or R2B")
    r = Report(mode=mode)
    events, task, queue = state["events"], state["task"], state["queue"]

    # ---------------------------------------------------------------- EV1 identity
    if not isinstance(task, dict):
        r.fail("EV1 task context missing or unreadable")
        task = {}
    for name, rec in (("task", task), ("queue", queue)):
        if rec is None:
            continue
        for f in ("attemptId", "taskStateVersion"):
            if f not in rec:
                r.fail(f"EV1 {name} lacks {f}")
    for i, e in enumerate(events):
        if not isinstance(e, dict):
            r.fail(f"EV1 event {i} unreadable")
            continue
        for f in STRING_IDS:
            if not isinstance(e.get(f), str) or not e.get(f):
                r.fail(f"EV1 event {i} lacks identity field {f}")
        for f in INT_IDS:
            if not isinstance(e.get(f), int) or isinstance(e.get(f), bool):
                r.fail(f"EV1 event {i} lacks identity field {f}")
    facts = [x for x in (task, *events, queue) if isinstance(x, dict)]
    for f in AGREE_FIELDS:
        values = {json.dumps(x[f]) for x in facts if f in x}
        if len(values) > 1:
            r.fail(f"EV1 identity field {f} disagrees across records")

    # ---------------------------------------------------------------- key and version binding
    first = events[0] if events and isinstance(events[0], dict) else {}
    if mode == "R2B":
        key = first.get("logicalOperationId") if first.get("eventType") == "AttemptCommitted" else None
        op_version = first.get("operationVersion") if first.get("eventType") == "AttemptCommitted" else None
        if not isinstance(key, str) or not key:
            r.fail("EV1 R2B AttemptCommitted lacks logicalOperationId")
        elif key != config_key:
            r.fail("EV1 R2B durable logicalOperationId differs from configured key", anomaly=True)
        if not isinstance(op_version, int) or isinstance(op_version, bool):
            r.fail("EV2 R2B AttemptCommitted lacks operationVersion")
        r.key, r.version = key, op_version
    else:
        r.key = config_key
        r.version = task.get("taskStateVersion")

    # ---------------------------------------------------------------- EV2 version
    versions = {x.get("taskStateVersion") for x in facts if "taskStateVersion" in x}
    if len(versions) > 1:
        r.fail("EV2 taskStateVersion disagrees across records")
    if mode == "R2B" and r.version is not None and task.get("taskStateVersion") != r.version:
        r.fail("EV2 current task version differs from the operation version bound at AttemptCommitted")

    # ---------------------------------------------------------------- EV3 sequence, EV4 order
    seen_types, seen_ids, prev_seq, prev_rank = [], set(), 0, 0
    for i, e in enumerate(events):
        if not isinstance(e, dict):
            continue
        t, s, eid = e.get("eventType"), e.get("journalSequence"), e.get("eventId")
        if t not in RANK:
            r.fail(f"EV4 event {i} has unknown type")
            continue
        if not isinstance(s, int) or isinstance(s, bool) or s <= prev_seq:
            r.fail(f"EV3 event {i} journalSequence not strictly increasing")
        else:
            prev_seq = s
        if not isinstance(eid, str) or eid in seen_ids:
            r.fail(f"EV3 event {i} eventId missing or duplicated")
        seen_ids.add(eid)
        if RANK[t] < prev_rank or t in seen_types:
            r.fail(f"EV4 event {i} out of protocol order or repeated")
        prev_rank = max(prev_rank, RANK[t])
        seen_types.append(t)
    if events and (not isinstance(events[0], dict) or events[0].get("eventType") != "AttemptCommitted"):
        r.fail("EV4 journal does not begin with AttemptCommitted")
    if "QueueItemPersisted" in seen_types and "QueueBindingPrepared" not in seen_types:
        r.fail("EV4 queue item without binding")
    if "DispatchBoundaryCrossed" in seen_types and "QueueBindingPrepared" in seen_types and "QueueItemPersisted" not in seen_types:
        r.fail("EV4 dispatch without queue item")
    if queue is not None and "QueueItemPersisted" not in seen_types:
        r.fail("EV4 queue item without journal confirmation")

    by_type = {e.get("eventType"): e for e in events if isinstance(e, dict)}
    dbc = by_type.get("DispatchBoundaryCrossed")
    outcome = by_type.get("AdapterOutcomeObserved")
    r.journal_dispatch = dbc
    r.queue_pending = bool(isinstance(queue, dict) and queue.get("pending") is True)

    # ---------------------------------------------------------------- EV7 authority records
    grants = {g.get("grant_id"): g for g in state["grants"]}
    consumed = state["consumed"]
    void_ids = {v.get("grant_id") for v in state["void"]}
    for c in consumed:
        gid = c.get("grant_id")
        g = grants.get(gid)
        if g is None:
            r.fail(f"EV7 consumed marker {gid} has no grant", anomaly=True)
            continue
        if gid in void_ids:
            r.fail(f"EV7 grant {gid} is both void and consumed", anomaly=True)
        if c.get("logical_operation_id") != g.get("logical_operation_id"):
            r.fail(f"EV7 consumed marker {gid} key differs from its grant", anomaly=True)
    for g in state["grants"]:
        if g.get("logical_operation_id") != r.key:
            r.fail(f"EV7 grant {g.get('grant_id')} is for another logical operation", anomaly=True)
    consumed_grants = [grants[c["grant_id"]] for c in consumed if c.get("grant_id") in grants]
    r.consumed_grants = consumed_grants

    # ---------------------------------------------------------------- EV5 dispatch evidence
    first_grants = [g for g in consumed_grants if g.get("first_dispatch")]
    if dbc is not None:
        if mode == "R2B":
            gid = dbc.get("grantId")
            g = grants.get(gid)
            if g is None or gid not in {c.get("grant_id") for c in consumed}:
                r.fail("EV5 R2B journal dispatch names no consumed grant", anomaly=True)
            elif (dbc.get("logicalOperationId") != g.get("logical_operation_id") or dbc.get("generation") != g.get("generation")
                  or dbc.get("sendAttemptId") != g.get("send_attempt_id") or not g.get("first_dispatch")):
                r.fail("EV5 R2B journal dispatch does not match its grant", anomaly=True)
            else:
                r.original_key = dbc.get("logicalOperationId")
        else:
            if len(first_grants) != 1:
                r.fail("EV5 R2A journal dispatch without exactly one consumed first-dispatch grant", anomaly=True)
            else:
                r.original_key = first_grants[0].get("logical_operation_id")
    elif first_grants:
        # consumed first-dispatch marker without a journal dispatch: a crash between the marker
        # and the journal write. Not an anomaly; the key counts as possibly dispatched.
        r.original_key = first_grants[0].get("logical_operation_id")

    # ---------------------------------------------------------------- EV6 outcome / confirmation records
    if outcome is not None:
        if outcome.get("outcome") == "adapter_accepted" and outcome.get("evidenceLevel") == "adapter_accepted":
            r.accepted = True
        elif outcome.get("outcome") == "definite_failure":
            r.local_failure = True
        else:
            r.weak_outcome = True
    for entry in state["log"]:
        if entry.get("kind") == "result" and entry.get("status") == "COMMITTED" and entry.get("logical_operation_id") == r.key:
            r.accepted = True
    for f in state["fences"]:
        if f.get("logical_operation_id") == r.key and (f.get("response") or {}).get("result") == "COMMITTED":
            r.accepted = True

    r.dispatched = dbc is not None or outcome is not None or bool(consumed_grants)

    # latest negative fence not followed by any consumed grant for K
    last_consumed_seq = max((c.get("authority_seq", 0) for c in consumed), default=0)
    negatives = [f for f in state["fences"] if f.get("logical_operation_id") == r.key
                 and (f.get("response") or {}).get("result") == "DEFINITELY_NOT_COMMITTED"
                 and f.get("authority_seq", 0) > last_consumed_seq]
    r.unconsumed_fence = max(negatives, key=lambda f: f["authority_seq"]) if negatives else None
    return r
