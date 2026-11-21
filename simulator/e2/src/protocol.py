"""Durable qualification primitives; importing this module runs no cell."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

PHASES = (
    "intent_persisted", "request_left_client", "service_accepted",
    "effect_committed", "response_received", "response_persisted",
)


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def durable_json(path: Path, value: object, *, replace: bool = False) -> None:
    """Flush bytes and parent directory; default forbids overwriting evidence."""
    if path.is_symlink() or (path.exists() and not replace):
        raise FileExistsError(path)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    if replace:
        os.replace(temporary, path)
    else:
        os.link(temporary, path)
        temporary.unlink()
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def wait_file(path: Path, seconds: float = 30) -> None:
    deadline = time.monotonic() + seconds
    while not path.is_file():
        if time.monotonic() > deadline:
            raise TimeoutError(str(path))
        time.sleep(0.01)


def barrier(directory: Path, control: Path, run_id: str, phase: str) -> dict:
    index = PHASES.index(phase) + 1
    value = {
        "run_id": run_id, "barrier_id": f"B{index}", "phase": phase,
        "durable_sequence": index, "durable_timestamp_utc_ns": time.time_ns(),
        "durable_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "actor_monotonic_ns": time.monotonic_ns(), "actor_pid": os.getpid(),
        "actor_euid": os.geteuid(), "actor_egid": os.getegid(),
    }
    value["receipt_sha256"] = digest(value)
    durable_json(directory / f"B{index}.json", value)
    # Publication of this second record follows fsync of the original barrier.
    durable_json(directory / f"B{index}-ready.json", {"barrier_sha256": value["receipt_sha256"]})
    wait_file(control / f"release-B{index}")
    return value
