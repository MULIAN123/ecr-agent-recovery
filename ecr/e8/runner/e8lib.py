"""E8 runner helpers (pure or file-local; unit-tested on the host by tests/test_e8lib.py).

Protocol r1/r2 rules implemented here:
  * final.json is archived ONLY BETWEEN recoveries: after recovery k < K the runner renames store/final.json to
    store/final.R{k}.json; the final.json of the last recovery K keeps its name for scorer_v2.
  * after EVERY recovery (clean exit or crash) the runner copies store/task.json and store/queue.json byte-for-byte
    to an audit directory OUTSIDE the SUT working directory and records source and copy SHA-256 (must be equal).
  * process outcome per recovery (G-K-proc): EXPECTED_HOOK / EXPECTED_INJECTED_EXCEPTION / CLEAN / UNEXPECTED /
    UNDECLARED, decided from exit code, exception type and recovery index against the cell's declaration.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def snapshot(store: Path, audit: Path, k: int, write) -> dict:
    """Copy task.json and queue.json of `store` to `audit` as task.R{k}.json / queue.R{k}.json.
    `write(path, data)` must create the file exclusively (runner: root-owned, scorer-readable)."""
    entry = {"k": k, "utc_ns": time.time_ns()}
    for name in ("task", "queue"):
        src = store / f"{name}.json"
        prefix = "" if name == "task" else "queue_"
        if src.is_file():
            st = src.stat()
            data = src.read_bytes()
            target = audit / f"{name}.R{k}.json"
            write(target, data)
            copy = target.read_bytes()
            entry[prefix + "source_sha256"] = sha_bytes(data)
            entry[prefix + "copy_sha256"] = sha_bytes(copy)
            entry[prefix + "source_mtime_ns"] = st.st_mtime_ns
            if entry[prefix + "source_sha256"] != entry[prefix + "copy_sha256"]:
                raise RuntimeError(f"snapshot copy differs for {name}.R{k}")
        else:
            entry[prefix + "source_sha256"] = "ABSENT"
            entry[prefix + "copy_sha256"] = "ABSENT"
    return entry


def archive_final(store: Path, k: int, total: int) -> dict:
    """Between recoveries only. Returns a receipt; never touches the final.json of the last recovery."""
    final = store / "final.json"
    if k >= total:
        return {"k": k, "action": "KEPT_LAST", "present": final.is_file(),
                "sha256": sha_bytes(final.read_bytes()) if final.is_file() else None}
    if not final.is_file():
        return {"k": k, "action": "NONE_ABSENT"}
    target = store / f"final.R{k}.json"
    if target.exists():
        raise FileExistsError(target)
    digest = sha_bytes(final.read_bytes())
    os.rename(final, target)
    if sha_bytes(target.read_bytes()) != digest or final.exists():
        raise RuntimeError("final.json archive mismatch")
    return {"k": k, "action": "ARCHIVED", "to": target.name, "sha256": digest}


_EXC = re.compile(r"^([A-Za-z_][\w.]*)(?::|$)")


def exception_type(stderr_text: str) -> str | None:
    """Last line of a Python traceback 'pkg.mod.Name: message' -> 'Name'. None if there is no traceback."""
    if "Traceback (most recent call last)" not in stderr_text:
        return None
    for line in reversed(stderr_text.strip().splitlines()):
        m = _EXC.match(line.strip())
        if m and not line.startswith(" "):
            return m.group(1).split(".")[-1]
    return "UNPARSED"


def process_outcome(plan: dict, code, exc, fired_hook) -> str:
    exp_exit, exp_exc = plan.get("expected_exit"), plan.get("expected_exception")
    if exp_exit is None:
        return "UNDECLARED"
    if code != exp_exit:
        return "UNEXPECTED"
    if code == 137:
        return "EXPECTED_HOOK" if plan.get("crash_at") and fired_hook else "UNEXPECTED"
    if code == 0:
        return "CLEAN" if exc is None else "UNEXPECTED"
    return "EXPECTED_INJECTED_EXCEPTION" if exp_exc and exc in exp_exc else "UNEXPECTED"


def count_recovery_sends(events: Path) -> int:
    n = 0
    for p in events.glob("log-*-send.json"):
        try:
            if not json.loads(p.read_text()).get("gated"):
                n += 1
        except (OSError, ValueError):
            continue
    return n


def wait_until(predicate, seconds: float, step: float = 0.02) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()
