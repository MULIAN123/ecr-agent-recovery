"""Root-side SIGKILL injection with raw child-status receipts (async_sim_ac1, Linux only).

kill_with_receipt() is the only way the ac1 runner terminates an agent at a pause point or barrier:
  1. read /proc/<pid>/stat start time (field 22) of the live target;
  2. write the injection intent (durable, root-owned);
  3. os.kill(pid, SIGKILL);
  4. os.waitid(P_PID, pid, WEXITED | WNOWAIT): raw si_code / si_status, child NOT reaped yet,
     so /proc/<pid>/stat still exists (zombie) and its start time is read again;
  5. Popen.wait(): reap, normalized return code;
  6. write the receipt. Validity is judged by src/acreceipts.validate_kill_receipt.
The receipt keeps the scorer_v2 compatibility fields exit_status (= normalized return code) and
barrier_id (= the crash point name), because the frozen primary scorer checks exactly these.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

CODE_NAMES = {getattr(os, n): n for n in ("CLD_EXITED", "CLD_KILLED", "CLD_DUMPED", "CLD_TRAPPED", "CLD_STOPPED",
                                          "CLD_CONTINUED") if hasattr(os, n)}


def proc_starttime(pid: int) -> int | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # comm may contain spaces and parentheses: split after the last ')'
    fields = raw[raw.rindex(")") + 2:].split()
    return int(fields[19])  # field 22 overall (state is field 3 -> index 0 here)


def proc_uid(pid: int) -> int | None:
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return None
    return int(status.split("Uid:\t", 1)[1].split()[1])


def kill_with_receipt(proc: subprocess.Popen, *, point: str, label: str, run_id: str, basis: dict,
                      write_json, folder: Path, name: str) -> dict:
    """write_json(path, value) must write durably (root_json of the runner). Returns the receipt."""
    pid = proc.pid
    if proc.poll() is not None:
        raise RuntimeError(f"{label}: target already exited before the kill (status {proc.returncode})")
    intent = {"run_id": run_id, "process_label": label, "point": point, "barrier_id": point,
              "injection_action": "SIGKILL", "target_pid": pid, "target_uid": proc_uid(pid),
              "target_starttime_before": proc_starttime(pid), "basis_receipt_sha256": basis.get("receipt_sha256"),
              "intent_utc_ns": time.time_ns(), "intent_utc": datetime.now(timezone.utc).isoformat()}
    write_json(folder / f"{name}-intent.json", intent)
    try:
        os.kill(pid, signal.SIGKILL)
        kill_result = "ok"
    except OSError as error:
        kill_result = f"errno {error.errno}"
    kill_sent = time.time_ns()
    info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
    starttime_after = proc_starttime(pid)  # zombie: still readable before reaping
    returncode = proc.wait(timeout=10)
    receipt = {**intent, "kill_syscall_result": kill_result, "kill_sent_utc_ns": kill_sent,
               "raw_si_pid": info.si_pid, "raw_si_signo": info.si_signo, "raw_si_code": info.si_code,
               "raw_si_code_name": CODE_NAMES.get(info.si_code, f"UNKNOWN_{info.si_code}"),
               "raw_si_status": info.si_status,
               "wtermsig": info.si_status if CODE_NAMES.get(info.si_code) in ("CLD_KILLED", "CLD_DUMPED") else None,
               "target_starttime_after": starttime_after,
               "normalized_returncode": returncode, "exit_status": returncode,
               "reaped_utc_ns": time.time_ns()}
    write_json(folder / f"{name}.json", receipt)
    return receipt
