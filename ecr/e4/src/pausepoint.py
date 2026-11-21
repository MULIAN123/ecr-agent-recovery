"""Pause points for the authorization-crash experiment (async_sim_ac1).

Replaces the os._exit(137) crash hooks of the frozen package. A pause point never terminates
the process by itself: when the environment names this point and scope, the process writes a
durable, hashed pause receipt and a ready marker, then blocks until its root supervisor kills it
with SIGKILL. The supervisor (runner/run_ac.py) verifies the receipt, kills, and records the raw
child status (runner/ackill.py). Release of a pause is not part of the protocol: if a release
file ever appears, the process raises, so the run cannot silently continue past a pause.

Environment (set only by the runner, only for the one process that must pause):
  ECR_PAUSE_AT       point name, e.g. "after_consumed"
  ECR_PAUSE_SCOPE    "first" (send path) or "recovery"
  ECR_PAUSE_LABEL    process label, e.g. "agent-send" or "agent-recover-1"
  ECR_PAUSE_EVENTS   directory for the receipt (the agent's events directory)
  ECR_PAUSE_CONTROL  directory watched for a (forbidden) release file
  ECR_PAUSE_RUN_ID   run identifier
When ECR_PAUSE_AT is unset, pause() returns immediately and has no effect.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from protocol import digest, durable_json

POINTS = ("before_grant", "after_grant", "after_consumed", "after_dbc", "after_request", "after_response",
          "after_fence_post", "after_fence_record", "after_result_log")
SCOPES = ("first", "recovery")
MAX_WAIT_S = 900


class PauseReleased(RuntimeError):
    """A pause was released, which the protocol forbids."""


def receipt_name(label: str) -> str:
    return f"pause-{label}.json"


def ready_name(label: str) -> str:
    return f"pause-{label}-ready.json"


def pause(name: str, scope: str) -> None:
    if os.environ.get("ECR_PAUSE_AT") != name or os.environ.get("ECR_PAUSE_SCOPE") != scope:
        return
    if name not in POINTS or scope not in SCOPES:
        raise ValueError(f"unknown pause point {name}/{scope}")
    label = os.environ["ECR_PAUSE_LABEL"]
    events = Path(os.environ["ECR_PAUSE_EVENTS"])
    control = Path(os.environ["ECR_PAUSE_CONTROL"])
    value = {"run_id": os.environ["ECR_PAUSE_RUN_ID"], "pause_point": name, "scope": scope, "process_label": label,
             "actor_pid": os.getpid(), "actor_euid": os.geteuid(), "actor_egid": os.getegid(),
             "durable_timestamp_utc_ns": time.time_ns(), "actor_monotonic_ns": time.monotonic_ns()}
    value["receipt_sha256"] = digest(value)
    durable_json(events / receipt_name(label), value)
    # the ready marker is published only after the receipt itself is durable
    durable_json(events / ready_name(label), {"pause_sha256": value["receipt_sha256"]})
    deadline = time.monotonic() + MAX_WAIT_S
    while time.monotonic() < deadline:
        if (control / f"release-pause-{label}").exists():
            raise PauseReleased(f"pause {name} of {label} was released; not allowed")
        time.sleep(0.05)
    raise TimeoutError(f"pause {name} of {label} was never ended by SIGKILL")
