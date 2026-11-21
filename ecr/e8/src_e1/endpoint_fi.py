# E8 fault-injection variant (E1) of the frozen endpoint.py; see FaultPlan. Faults are off unless configured.
"""Simulated external endpoint for the asynchronous simulation (ECR experiment 2).

Derived from qualification_v2/adapter.py (platform-qualified 12/12). Contract and
ledger semantics are unchanged: P1/P3 offer an authoritative fenced query, P2/P3
deduplicate by logical_operation_id, not_found is never negative proof. Changes:
- only the original transport request is gated, so recovery requests are not held;
- the gated request can also be dropped in transit (`drop-request`) or released late;
- every send, query and fence is logged durably for the independent scorer.
No production endpoint, model API or real messaging is involved.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from protocol import barrier, durable_json

LOG_COUNTER = itertools.count(1)
LOG_LOCK = threading.Lock()


class Service:
    def __init__(self, configuration: dict):
        self.configuration = configuration
        self.db_path = Path(configuration["db"])
        self.profile = configuration["profile"]
        self.query_available = self.profile in ("P1", "P3")
        self.idempotent = self.profile in ("P2", "P3")
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE operations (logical_operation_id TEXT PRIMARY KEY,
                    generation INTEGER NOT NULL, fenced INTEGER NOT NULL);
                CREATE TABLE requests (transport_request_id TEXT PRIMARY KEY,
                    logical_operation_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
                    generation INTEGER NOT NULL, state TEXT NOT NULL);
                CREATE TABLE fences (logical_operation_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    PRIMARY KEY(logical_operation_id,generation));
                CREATE TABLE effects (seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    logical_operation_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
                    transport_request_id TEXT NOT NULL, committed INTEGER NOT NULL);
            """)
            db.execute("INSERT INTO metadata VALUES ('profile',?)", (self.profile,))
            db.execute("INSERT INTO metadata VALUES ('run_id',?)", (configuration["run_id"],))
            if self.idempotent:
                db.execute("CREATE UNIQUE INDEX effect_once ON effects(logical_operation_id)")
        os.chmod(self.db_path, 0o640)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA journal_mode=DELETE")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def accept(self, body: dict) -> tuple[str, str, str, int]:
        values = [body.get(key) for key in
                  ("logical_operation_id", "attempt_id", "transport_request_id")]
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError("Three nonempty independent identities required")
        logical, attempt, transport = values
        generation = body.get("generation", 0)
        if type(generation) is not int or generation < 0:
            raise ValueError("Invalid generation")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO operations VALUES (?,0,0)", (logical,))
            current = db.execute("SELECT generation FROM operations WHERE logical_operation_id=?",
                                 (logical,)).fetchone()[0]
            if generation != current:
                raise ValueError("STALE_GENERATION; query endpoint for current fence")
            db.execute("INSERT INTO requests VALUES (?,?,?,?, 'ACCEPTED')",
                       (transport, logical, attempt, generation))
            db.execute("UPDATE operations SET fenced=0 WHERE logical_operation_id=?", (logical,))
        return logical, attempt, transport, generation

    def commit(self, values: tuple[str, str, str, int]) -> dict:
        logical, attempt, transport, generation = values
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current, fenced = db.execute(
                "SELECT generation,fenced FROM operations WHERE logical_operation_id=?",
                (logical,)).fetchone()
            state = db.execute("SELECT state FROM requests WHERE transport_request_id=?",
                               (transport,)).fetchone()[0]
            if generation != current or state == "CANCELLED" or fenced:
                return {"status": "CANCELLED_BY_FENCE", "generation": current}
            existing = db.execute(
                "SELECT seq FROM effects WHERE logical_operation_id=? ORDER BY seq LIMIT 1",
                (logical,)).fetchone()
            deduplicated = bool(self.idempotent and existing)
            if deduplicated:
                seq = existing[0]
            else:
                seq = db.execute(
                    "INSERT INTO effects(logical_operation_id,attempt_id,transport_request_id,committed) VALUES(?,?,?,1)",
                    (logical, attempt, transport)).lastrowid
            db.execute("UPDATE requests SET state='COMMITTED' WHERE transport_request_id=?", (transport,))
        return {"status": "COMMITTED", "effect_seq": seq, "deduplicated": deduplicated}

    def query(self, logical: str) -> dict:
        if not self.query_available:
            return {"result": "UNKNOWN", "reason": "QUERY_UNAVAILABLE"}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            effect = db.execute("SELECT seq FROM effects WHERE logical_operation_id=? LIMIT 1",
                                (logical,)).fetchone()
            if effect:
                return {"result": "COMMITTED", "effect_seq": effect[0]}
            operation = db.execute(
                "SELECT generation,fenced FROM operations WHERE logical_operation_id=?",
                (logical,)).fetchone()
            if not operation:
                return {"result": "UNKNOWN", "reason": "NOT_FOUND_IS_NOT_NEGATIVE_PROOF"}
            active = db.execute("SELECT count(*) FROM requests WHERE logical_operation_id=? AND state='ACCEPTED'",
                                (logical,)).fetchone()[0]
            if active:
                return {"result": "ACCEPTED_NOT_FINAL"}
            if operation[1]:
                return {"result": "DEFINITELY_NOT_COMMITTED", "retry_generation": operation[0],
                        "contract": "OLD_GENERATIONS_FENCED"}
            return {"result": "UNKNOWN"}

    def fence(self, logical: str) -> dict:
        if not self.query_available:
            raise ValueError("Fencing unsupported by endpoint profile")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            effect = db.execute("SELECT seq FROM effects WHERE logical_operation_id=? LIMIT 1",
                                (logical,)).fetchone()
            if effect:
                return {"result": "COMMITTED", "effect_seq": effect[0]}
            db.execute("INSERT OR IGNORE INTO operations VALUES (?,0,0)", (logical,))
            db.execute("UPDATE operations SET generation=generation+1,fenced=1 WHERE logical_operation_id=?",
                       (logical,))
            db.execute("UPDATE requests SET state='CANCELLED' WHERE logical_operation_id=? AND state='ACCEPTED'",
                       (logical,))
            generation = db.execute("SELECT generation FROM operations WHERE logical_operation_id=?",
                                    (logical,)).fetchone()[0]
            db.execute("INSERT INTO fences VALUES (?,?)", (logical, generation))
        return self.query(logical)


class FaultPlan:
    """E8 fault injection (endpoint_fi only). Off unless configured; every fault fires at most once.

    fi = {"fence_fault": "http500_once" | "unknown_once" | "nonjson_once",
          "send_fault": "http500_once" | "commit_then_drop_reply_once",
          "late_release_first_recovery_send": true}
    Faults apply only to requests other than the original gated transport. Each firing writes a
    durable receipt fi-<n>-<kind>.json so that injected behaviour is never mistaken for an
    environment failure.
    """
    FENCE = ("http500_once", "unknown_once", "nonjson_once")
    SEND = ("http500_once", "commit_then_drop_reply_once")

    def __init__(self, spec: dict, events: Path):
        unknown = set(spec) - {"fence_fault", "send_fault", "late_release_first_recovery_send"}
        if unknown:
            raise ValueError(f"unknown fi keys {sorted(unknown)}")
        if spec.get("fence_fault") not in (None,) + self.FENCE or spec.get("send_fault") not in (None,) + self.SEND:
            raise ValueError("invalid fi fault")
        self.spec, self.events, self.used, self.lock, self.n = dict(spec), events, set(), threading.Lock(), 0

    def _fire(self, kind: str, detail: dict) -> bool:
        with self.lock:
            if kind in self.used:
                return False
            self.used.add(kind)
            self.n += 1
            n = self.n
        durable_json(self.events / f"fi-{n:02d}-{kind}.json", {"kind": kind, "utc_ns": time.time_ns(), **detail})
        return True

    def fence_fault(self, logical: str):
        fault = self.spec.get("fence_fault")
        if fault and self._fire("fence_" + fault, {"logical_operation_id": logical}):
            return fault
        return None

    def send_fault_before(self, body: dict):
        if self.spec.get("send_fault") == "http500_once" and self._fire("send_http500_once", {"request": body}):
            return "http500_once"
        return None

    def send_drop_reply(self, body: dict) -> bool:
        return self.spec.get("send_fault") == "commit_then_drop_reply_once" and \
            self._fire("send_commit_then_drop_reply_once", {"request": body})

    def late_hold(self, body: dict, control: Path, seconds: float = 600) -> None:
        if not self.spec.get("late_release_first_recovery_send"):
            return
        tid = str(body.get("transport_request_id"))
        if not self._fire("late_hold", {"request": body}):
            return
        durable_json(self.events / f"late-held-{tid}.json", {"transport_request_id": tid, "utc_ns": time.time_ns()})
        deadline = time.monotonic() + seconds
        while not (control / "release-late").is_file():
            if time.monotonic() > deadline:
                raise TimeoutError("late gate")
            time.sleep(0.01)
        durable_json(self.events / f"late-released-{tid}.json", {"transport_request_id": tid, "utc_ns": time.time_ns()})


def log(events: Path, kind: str, value: dict) -> None:
    with LOG_LOCK:
        index = next(LOG_COUNTER)
    durable_json(events / f"log-{index:04d}-{kind}.json",
                 {"index": index, "kind": kind, "utc_ns": time.time_ns(), **value})


def wait_gate(control: Path, seconds: float = 120) -> str:
    deadline = time.monotonic() + seconds
    while True:
        if (control / "drop-request").is_file():
            return "DROP"
        if (control / "allow-accept").is_file():
            return "ALLOW"
        if time.monotonic() > deadline:
            raise TimeoutError("gate")
        time.sleep(0.01)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("configuration", type=Path)
    args = parser.parse_args()
    configuration = json.loads(args.configuration.read_text())
    os.umask(0o027)
    service = Service(configuration)
    control = Path(configuration["control"])
    events = Path(configuration["events"])
    fi = FaultPlan(configuration.get("fi") or {}, events)

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, value: dict) -> None:
            raw = json.dumps(value).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def reply_injected(self, fault: str) -> None:
            if fault == "http500_once":
                return self.reply(500, {"error": "FI_INJECTED_HTTP500"})
            if fault == "unknown_once":
                return self.reply(200, {"result": "UNKNOWN", "reason": "FI_INJECTED"})
            raw = b"FI-NOT-JSON"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path == "/health":
                return self.reply(200, {"ready": True, "run_id": configuration["run_id"]})
            if self.path.startswith("/query/"):
                logical = unquote(self.path[7:])
                result = service.query(logical)
                log(events, "query", {"logical_operation_id": logical, "result": result})
                return self.reply(200, result)
            self.reply(404, {"error": "not_found"})

        def do_POST(self) -> None:
            gated = False
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError("Invalid request length")
                body = json.loads(self.rfile.read(length))
                if self.path == "/fence":
                    logical = body.get("logical_operation_id")
                    if not isinstance(logical, str) or not logical:
                        raise ValueError("Missing logical operation")
                    injected = fi.fence_fault(logical)
                    if injected is not None:
                        return self.reply_injected(injected)
                    result = service.fence(logical)
                    log(events, "fence", {"logical_operation_id": logical, "result": result})
                    return self.reply(200, result)
                if self.path != "/send":
                    return self.reply(404, {"error": "not_found"})
                gated = body.get("transport_request_id") == configuration["gated_transport"]
                if gated and wait_gate(control) == "DROP":
                    log(events, "send", {"request": body, "gated": True, "result": {"status": "DROPPED_IN_TRANSIT"}})
                    durable_json(events / "original-done.json", {"fate": "DROPPED_IN_TRANSIT"})
                    self.close_connection = True
                    return
                if not gated:
                    fi.late_hold(body, control)
                    if fi.send_fault_before(body) is not None:
                        log(events, "send", {"request": body, "gated": False, "result": {"status": "FI_INJECTED_HTTP500"}})
                        return self.reply_injected("http500_once")
                try:
                    values = service.accept(body)
                except ValueError as error:
                    log(events, "send", {"request": body, "gated": gated, "result": {"status": "REJECTED", "error": str(error)}})
                    if gated:
                        durable_json(events / "original-done.json", {"fate": "REJECTED", "error": str(error)})
                    return self.reply(409, {"error": str(error)})
                if gated:
                    barrier(events, control, configuration["run_id"], "service_accepted")
                result = service.commit(values)
                log(events, "send", {"request": body, "gated": gated, "result": result})
                if gated:
                    durable_json(events / "commit-result.json", result)
                    if result["status"] == "COMMITTED":
                        barrier(events, control, configuration["run_id"], "effect_committed")
                    durable_json(events / "original-done.json", {"fate": result["status"]})
                if not gated and fi.send_drop_reply(body):
                    self.close_connection = True
                    return
                self.reply(200, result)
            except (ValueError, sqlite3.IntegrityError) as error:
                self.reply(409, {"error": str(error)})
            except Exception as error:
                durable_json(events / ("error-" + str(threading.get_ident()) + "-" + str(time.time_ns()) + ".json"),
                             {"type": type(error).__name__, "message": str(error), "gated": gated})
                self.reply(500, {"error": type(error).__name__})

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False
    durable_json(events / "ready.json", {"port": server.server_address[1],
                 "pid": os.getpid(), "uid": os.geteuid(), "run_id": configuration["run_id"]})

    def stop() -> None:
        while not (control / "stop").is_file():
            time.sleep(0.02)
        server.shutdown()

    threading.Thread(target=stop, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
