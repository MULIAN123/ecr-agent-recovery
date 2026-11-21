"""Completeness and consistency check of one attempt's planner-call records (phase 3).

check(record_dir, agent_plan_records) returns a dict with
  status   : "COMPLETE" | "INCOMPLETE"
  reasons  : list of problems (any problem -> the attempt is INTEGRITY_UNKNOWN)
  calls    : per-call summary (exit, timeout, signal, hashes verified)
  failures : calls with a non-zero exit, a signal or a timeout (planner infrastructure failures)
A record set is INCOMPLETE if
  * a call has input but no started record, or started but no finished record (recorder or
    process interrupted mid-call), or the numbering has gaps;
  * a stored stdout/stderr/input file is missing or its SHA-256 differs from the recorded one;
  * a route check failed;
  * identity binding (candidate v4): a call's identity_check is not OK, or is absent (a record
    without binding is never accepted for a v4 attempt), or the calls of one attempt name more
    than one code identity or generated-JS identity;
  * the number of successful calls (exit 0, stdout parses as {"node","plan"}) differs from the
    number of PLAN records the agent persisted (agent_plan_records), in either direction.
Nothing is normalised away: a successful call whose stdout is not exactly one JSON object with
the keys "node" and "plan" is reported as malformed.
"""
import hashlib
import json
import re
from pathlib import Path

NAME = re.compile(r"^call-(\d{4})\.(input\.bin|started\.json|stdout\.bin|stderr\.bin|finished\.json)$")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(record_dir: Path, agent_plan_records: int) -> dict:
    record_dir = Path(record_dir)
    reasons, calls, failures = [], [], []
    files = {}
    if not record_dir.is_dir():
        return {"status": "INCOMPLETE", "reasons": ["record directory missing"], "calls": [], "failures": []}
    for p in record_dir.iterdir():
        m = NAME.match(p.name)
        if not m:
            reasons.append(f"unexpected file {p.name}")
            continue
        files.setdefault(int(m.group(1)), {})[m.group(2)] = p
    numbers = sorted(files)
    if numbers and numbers != list(range(1, numbers[-1] + 1)):
        reasons.append(f"call numbering has gaps: {numbers}")
    successes = 0
    identities = set()
    for n in numbers:
        f = files[n]
        cid = f"call-{n:04d}"
        missing = [k for k in ("input.bin", "started.json", "stdout.bin", "stderr.bin", "finished.json") if k not in f]
        if missing:
            reasons.append(f"{cid}: missing {','.join(missing)} (call interrupted or record incomplete)")
            calls.append({"call_id": cid, "complete": False})
            continue
        try:
            started = json.loads(f["started.json"].read_bytes())
            finished = json.loads(f["finished.json"].read_bytes())
        except ValueError:
            reasons.append(f"{cid}: unreadable started/finished record")
            calls.append({"call_id": cid, "complete": False})
            continue
        ok = True
        if _sha(f["input.bin"]) != started.get("input_sha256"):
            reasons.append(f"{cid}: input hash mismatch")
            ok = False
        if _sha(f["stdout.bin"]) != finished.get("stdout_sha256") or _sha(f["stderr.bin"]) != finished.get("stderr_sha256"):
            reasons.append(f"{cid}: stdout/stderr hash mismatch")
            ok = False
        if started.get("route_check") != "OK":
            reasons.append(f"{cid}: route check failed: {started.get('route_check')}")
            ok = False
        if started.get("identity_check") != "OK":
            reasons.append(f"{cid}: identity check {started.get('identity_check', 'ABSENT')}: {started.get('identity_problems')}")
            ok = False
        identities.add((started.get("code_identity"), started.get("js_identity")))
        exit_code, timed_out, sig = finished.get("exit_code"), finished.get("timed_out"), finished.get("signal")
        summary = {"call_id": cid, "complete": ok, "exit_code": exit_code, "timed_out": timed_out, "signal": sig,
                   "input_sha256": started.get("input_sha256"), "stdout_sha256": finished.get("stdout_sha256"),
                   "runtime_version_reported": finished.get("runtime_version_reported")}
        if exit_code == 0 and not timed_out and not sig:
            try:
                parsed = json.loads(f["stdout.bin"].read_bytes())
                well_formed = isinstance(parsed, dict) and set(parsed) == {"node", "plan"} and isinstance(parsed["plan"], dict)
            except (ValueError, UnicodeDecodeError):
                well_formed = False
            summary["well_formed"] = well_formed
            if well_formed:
                successes += 1
            else:
                reasons.append(f"{cid}: exit 0 but stdout is not exactly one {{node, plan}} JSON object")
        else:
            failures.append(summary)
        calls.append(summary)
    if len(identities) > 1:
        reasons.append(f"calls name more than one (code, js) identity: {sorted(map(str, identities))}")
    if successes != agent_plan_records:
        reasons.append(f"successful recorded calls ({successes}) != agent PLAN records ({agent_plan_records})")
    return {"status": "COMPLETE" if not reasons else "INCOMPLETE", "reasons": reasons, "calls": calls,
            "identities": sorted([list(i) for i in identities], key=str),
            "failures": failures, "successful_calls": successes}
