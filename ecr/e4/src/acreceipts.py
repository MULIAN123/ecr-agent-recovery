"""Validation of SIGKILL injection receipts and of snapshot timing (async_sim_ac1).

Pure functions over JSON values; no OS calls, so they are tested on synthetic fixtures. The runner
(runner/ackill.py) writes the receipts; the diagnostic scorer re-validates them independently.

A kill receipt is valid evidence of a real SIGKILL only if ALL of the following hold:
  * injection_action == "SIGKILL" and kill_syscall_result == "ok";
  * the raw child status from waitid(WNOWAIT) says killed by signal 9:
        raw_si_code_name == "CLD_KILLED" and raw_si_status == 9
    (CLD_EXITED with status 137 is a voluntary exit, e.g. os._exit(137), and is rejected;
     CLD_DUMPED is a different termination and is rejected);
  * wtermsig == 9 and normalized_returncode == -9 (Popen.returncode after reaping);
  * the killed process is the one that wrote the pause (or barrier) receipt: same pid, same
    /proc start time before and after the kill (no pid reuse), expected uid;
  * the receipt names the expected point and the hash of the pause/barrier receipt, and that
    hash recomputes;
  * order: intent < kill sent < reaped (UTC ns).
"""
from __future__ import annotations

import hashlib
import json

SIGKILL = 9


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def basis_hash_ok(basis: dict) -> bool:
    body = {k: v for k, v in basis.items() if k != "receipt_sha256"}
    return basis.get("receipt_sha256") == digest(body)


ADAPTER_BARRIERS = ("B3", "B4")  # barrier receipts written by the endpoint process, not by the killed agent


def validate_kill_receipt(receipt: dict | None, basis: dict | None, *, expected_point: str,
                          expected_uid: int | None, adapter_identity: dict | None = None,
                          launch_record: dict | None = None) -> list[str]:
    """Return the list of reasons the receipt is NOT valid SIGKILL evidence ([] = valid).

    Cell binding (F3): the basis receipt must carry the same run_id as the kill receipt.
    B3/B4: the basis receipt is the endpoint's barrier receipt; instead of the agent's pid it must
    match the endpoint of this cell: adapter_identity = {"pid": endpoint pid from ready.json,
    "euid": adapter uid}; missing adapter_identity is itself a reason.
    launch_record = {"pid", "starttime", "uid"} written by the runner when it started the killed
    process; when given, target pid, both start times and uid must equal it (the callers in the
    runner and the diagnostic scorer always give it)."""
    basis_from_target = expected_point not in ADAPTER_BARRIERS
    if receipt is None:
        return ["kill receipt missing"]
    if basis is None:
        return ["pause/barrier receipt missing"]
    r = []
    if basis.get("run_id") is None or basis.get("run_id") != receipt.get("run_id"):
        r.append(f"basis run_id {basis.get('run_id')!r} differs from kill receipt run_id {receipt.get('run_id')!r}")
    if not basis_from_target:
        if adapter_identity is None:
            r.append("adapter identity of this cell not provided for an endpoint barrier receipt")
        else:
            if basis.get("actor_pid") != adapter_identity.get("pid"):
                r.append("endpoint barrier receipt pid differs from this cell's endpoint pid")
            if basis.get("actor_euid") != adapter_identity.get("euid"):
                r.append("endpoint barrier receipt was not written by the adapter identity")
    if launch_record is not None:
        if receipt.get("target_pid") != launch_record.get("pid"):
            r.append("killed pid differs from the pid recorded when the process was started")
        if launch_record.get("starttime") is None or receipt.get("target_starttime_before") != launch_record.get("starttime"):
            r.append("start time at kill differs from the start time recorded at launch")
        if launch_record.get("uid") != receipt.get("target_uid"):
            r.append("uid at kill differs from the uid recorded at launch")
    if receipt.get("injection_action") != "SIGKILL":
        r.append(f"injection_action is {receipt.get('injection_action')!r}, not SIGKILL")
    if receipt.get("kill_syscall_result") != "ok":
        r.append(f"kill() result {receipt.get('kill_syscall_result')!r}")
    code, status = receipt.get("raw_si_code_name"), receipt.get("raw_si_status")
    if code == "CLD_EXITED":
        r.append(f"raw status is a voluntary exit (CLD_EXITED, status {status}); not a SIGKILL")
    elif code != "CLD_KILLED":
        r.append(f"raw si_code {code!r} is not CLD_KILLED")
    if code != "CLD_EXITED" and status != SIGKILL:
        r.append(f"raw si_status {status!r} is not SIGKILL (9)")
    if receipt.get("wtermsig") != SIGKILL:
        r.append(f"wtermsig {receipt.get('wtermsig')!r} is not 9")
    if receipt.get("normalized_returncode") != -SIGKILL:
        r.append(f"normalized return code {receipt.get('normalized_returncode')!r} is not -9")
    if receipt.get("exit_status") != receipt.get("normalized_returncode"):
        r.append("exit_status (scorer_v2 compatibility field) differs from normalized_returncode")
    if receipt.get("barrier_id") != expected_point or receipt.get("point") != expected_point:
        r.append(f"receipt point {receipt.get('point')!r}/{receipt.get('barrier_id')!r} is not {expected_point!r}")
    if not basis_hash_ok(basis):
        r.append("pause/barrier receipt hash does not recompute")
    if receipt.get("basis_receipt_sha256") != basis.get("receipt_sha256"):
        r.append("kill receipt does not name the pause/barrier receipt hash")
    if basis_from_target and receipt.get("target_pid") != basis.get("actor_pid"):
        r.append("killed pid differs from the pid that wrote the pause/barrier receipt")
    st_before, st_after = receipt.get("target_starttime_before"), receipt.get("target_starttime_after")
    if st_before is None or st_before != st_after:
        r.append("process start time missing or changed across the kill (pid reuse not excluded)")
    if expected_uid is not None:
        if receipt.get("target_uid") != expected_uid:
            r.append("target uid differs from the expected agent identity")
        if basis_from_target and basis.get("actor_euid") != expected_uid:
            r.append("pause/barrier receipt was not written by the agent identity")
    t_intent, t_kill, t_reap = (receipt.get(k) for k in ("intent_utc_ns", "kill_sent_utc_ns", "reaped_utc_ns"))
    if not all(isinstance(t, int) for t in (t_intent, t_kill, t_reap)) or not (t_intent <= t_kill < t_reap):
        r.append("timestamps not in order intent <= kill < reap")
    return r


KILL_FIELDS = ("target_pid", "target_starttime_before", "target_starttime_after", "kill_sent_utc_ns", "raw_si_code",
               "raw_si_code_name", "raw_si_status", "wtermsig", "normalized_returncode")


def validate_none_receipt(receipt: dict | None) -> list[str]:
    """F1: conditions of the injection receipt of a no-fault control cell ([] = valid)."""
    if receipt is None:
        return ["control receipt missing"]
    r = []
    if receipt.get("injection_action") != "NONE":
        r.append(f"injection_action is {receipt.get('injection_action')!r}, not NONE")
    if receipt.get("exit_status") is not None:
        r.append("exit_status must be null for a control")
    if receipt.get("barrier_id") != "NONE" or receipt.get("point") != "NONE":
        r.append("barrier_id and point must both be NONE")
    if receipt.get("normal_completion") is not True:
        r.append("normal_completion is not true")
    if receipt.get("send_exit_code") != 0:
        r.append(f"send process exit code {receipt.get('send_exit_code')!r} is not 0")
    present = [k for k in KILL_FIELDS if k in receipt]
    if present:
        r.append(f"control receipt carries kill fields {present}")
    return r


def validate_snapshot_timing(kill_receipt: dict | None, snapshot: dict | None, recovery_start: dict | None,
                             *, live_agent_processes: list) -> list[str]:
    """A pre-recovery snapshot is valid only if it was taken after the preceding kill was reaped,
    finished before the next recovery process was started, and no agent process was alive while
    it was taken. snapshot: {"started_utc_ns", "finished_utc_ns", "tree_sha256"};
    recovery_start: {"started_utc_ns"} of the recovery process that follows."""
    r = []
    if snapshot is None:
        return ["snapshot record missing"]
    if not snapshot.get("tree_sha256"):
        r.append("snapshot tree hash missing")
    s0, s1 = snapshot.get("started_utc_ns"), snapshot.get("finished_utc_ns")
    if not isinstance(s0, int) or not isinstance(s1, int) or s1 < s0:
        r.append("snapshot timestamps missing or reversed")
        return r
    if kill_receipt is not None:
        reap = kill_receipt.get("reaped_utc_ns")
        if not isinstance(reap, int) or not reap < s0:
            r.append("snapshot started before the killed process was reaped")
    if recovery_start is None or not isinstance(recovery_start.get("started_utc_ns"), int):
        r.append("start record of the following recovery missing")
    elif not s1 < recovery_start["started_utc_ns"]:
        r.append("snapshot finished after the following recovery started")
    if live_agent_processes:
        r.append(f"agent processes alive during snapshot: {live_agent_processes}")
    return r
