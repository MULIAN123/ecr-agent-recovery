"""Send-authorization boundary for EBRQ-v2 (ECR technical fix, async_sim_r2).

This module is the only code in the EBRQ-v2 path that may contact the endpoint's mutating
operations (/send, /fence). Every new external effect follows the order
    authorize -> grant (durable) -> consumed marker (durable, exclusive)
    -> [first dispatch only] journal DispatchBoundaryCrossed (durable) -> HTTP send
so that dispatch evidence precedes the effect (assumption A1) and a grant can be consumed
at most once. Authorization never reads the classifier's or planner's output and never
trusts a caller-supplied label: the caller *applies* for a basis, and the decision is taken
from the durable state by evidence.validate() plus the basis conditions below.

Fail-closed rules after a crash (design v2, section 4.4):
  grant without consumed marker      -> voided at recovery start, never consumable again;
                                        the key is not counted as dispatched
  consumed marker (with or without   -> the key counts as possibly dispatched
  journal dispatch)
  authority-record anomaly            -> key frozen: no grant and no fence of any kind

Test hook: if ECR_TEST_CRASH_AT names a point (after_grant, after_consumed, after_dbc,
after_request, after_response) and ECR_TEST_CRASH_SCOPE matches ("first" or "recovery"),
the process exits with os._exit(137) at that point. Formal batches must not set it.
"""
from __future__ import annotations

import http.client
import json
import os
import time
import uuid
from pathlib import Path

from evidence import load_state, validate
from protocol import digest, durable_json

BASES = ("PRE_DISPATCH", "FENCED_NEGATIVE", "IDEMPOTENT")


def _crash_point(name: str, scope: str) -> None:
    if os.environ.get("ECR_TEST_CRASH_AT") == name and os.environ.get("ECR_TEST_CRASH_SCOPE", scope) == scope:
        os._exit(137)


class Authority:
    def __init__(self, store: Path, mode: str, config_key: str, contract: dict, port: int):
        if mode not in ("R2A", "R2B"):
            raise ValueError("mode must be R2A or R2B")
        if set(contract) - {"query", "idempotent", "profile", "config_sha256"} or not all(
                isinstance(contract.get(k), bool) for k in ("query", "idempotent")):
            raise ValueError("contract declaration must state query and idempotent as booleans")
        self.store, self.mode, self.config_key, self.contract, self.port = Path(store), mode, config_key, contract, port
        self.root = self.store / "authority"
        for sub in ("grants", "consumed", "void", "fences", "log"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self._issued: set[str] = set()   # grants issued by this process; only these may be consumed

    # ------------------------------------------------------------ records
    def _next_seq(self) -> int:
        seqs = [json.loads(p.read_text()).get("authority_seq", 0) for p in self.root.rglob("*.json")]
        return max(seqs, default=0) + 1

    def _write(self, sub: str, name: str, value: dict) -> dict:
        value = {**value, "authority_seq": self._next_seq(), "utc_ns": time.time_ns()}
        durable_json(self.root / sub / f"{name}.json", value)
        return value

    def _log(self, kind: str, value: dict) -> dict:
        seq = self._next_seq()
        return self._write("log", f"{seq:05d}-{kind}", {"kind": kind, **value})

    def report(self):
        return validate(load_state(self.store), self.mode, self.config_key)

    # ------------------------------------------------------------ recovery start
    def void_orphans(self) -> list[str]:
        """Grants without a consumed marker cannot have led to a send (A1 ordering); void them."""
        consumed = {p.stem for p in (self.root / "consumed").glob("*.json")}
        voided = {p.stem for p in (self.root / "void").glob("*.json")}
        out = []
        for p in sorted((self.root / "grants").glob("*.json")):
            gid = p.stem
            if gid not in consumed and gid not in voided:
                self._write("void", gid, {"kind": "void", "grant_id": gid, "reason": "orphan grant at recovery start"})
                out.append(gid)
        return out

    # ------------------------------------------------------------ decisions
    def _deny(self, basis: str, request: dict | None, reasons: list[str], report) -> None:
        self._log("denial", {"basis": basis, "request": request, "reasons": reasons, "logical_operation_id": self.config_key,
                             "validation": report.summary()})
        return None

    def authorize(self, basis: str, request: dict, *, first_dispatch: bool = False) -> dict | None:
        """Return a durable grant if the durable state independently satisfies the send contract."""
        rep = self.report()
        reasons = list(rep.reasons)
        if basis not in BASES:
            reasons.append(f"basis {basis!r} is not a contract basis")
        if request.get("logical_operation_id") != rep.key or request.get("logical_operation_id") != self.config_key:
            reasons.append("request key differs from the bound logical operation key")
        if rep.accepted:
            reasons.append("an acceptance or endpoint commit already exists for this operation")
        used = {json.loads(p.read_text()).get("send_attempt_id") for p in (self.root / "grants").glob("*.json")}
        if request.get("transport_request_id") in used:
            reasons.append("send-attempt id already used by an earlier grant")
        gen = request.get("generation")
        fence = None
        if basis == "PRE_DISPATCH":
            if rep.dispatched:
                reasons.append("dispatch evidence exists for this operation")
            if not rep.queue_pending:
                reasons.append("no pending queue responsibility")
            if gen != 0:
                reasons.append("pre-dispatch send must use generation 0")
        elif basis == "FENCED_NEGATIVE":
            fence = rep.unconsumed_fence
            if not self.contract["query"]:
                reasons.append("endpoint contract offers no fenced query")
            if fence is None:
                reasons.append("no durable negative fence result after the last dispatch")
            elif gen != fence["response"].get("retry_generation"):
                reasons.append("request generation differs from the fenced generation")
        elif basis == "IDEMPOTENT":
            if not self.contract["idempotent"]:
                reasons.append("endpoint contract offers no idempotency")
            if rep.original_key is not None and rep.original_key != request.get("logical_operation_id"):
                reasons.append("idempotency key differs from the key of the original dispatch")
            if rep.dispatched and rep.original_key is None:
                reasons.append("original dispatch key cannot be established")
            if gen != 0:
                reasons.append("idempotent resend must use the current generation 0")
        if reasons:
            return self._deny(basis, request, reasons, rep)
        gid = uuid.uuid4().hex
        grant = self._write("grants", gid, {
            "kind": "grant", "grant_id": gid, "basis": basis, "mode": self.mode,
            "logical_operation_id": request["logical_operation_id"], "idempotency_key": request["logical_operation_id"],
            "operation_version": rep.version, "attempt_id": request.get("attempt_id"),
            "send_attempt_id": request.get("transport_request_id"), "generation": gen,
            "fence_id": (fence or {}).get("fence_id"), "first_dispatch": first_dispatch,
            "contract": self.contract, "evidence_digest": digest(rep.summary())})
        self._issued.add(gid)
        _crash_point("after_grant", "first" if first_dispatch else "recovery")
        return grant

    def authorize_fence(self) -> tuple[bool, list[str]]:
        rep = self.report()
        reasons = list(rep.reasons)
        if not self.contract["query"]:
            reasons.append("endpoint contract offers no fenced query")
        if not rep.dispatched:
            reasons.append("no dispatch evidence: nothing to fence")
        if rep.accepted:
            reasons.append("acceptance already exists")
        if reasons:
            self._deny("FENCE", None, reasons, rep)
            return False, reasons
        return True, []

    # ------------------------------------------------------------ endpoint mutations (only here)
    def fence(self) -> tuple[int, dict, str] | None:
        """Gated fence. Returns (http_status, raw endpoint result, fence_id), or None if denied."""
        ok, _ = self.authorize_fence()
        if not ok:
            return None
        status, result = self._post("/fence", {"logical_operation_id": self.config_key})
        fid = f"{self._next_seq():05d}"
        self._write("fences", fid, {"kind": "fence", "fence_id": fid, "logical_operation_id": self.config_key,
                                    "http_status": status, "response": result})
        return status, result, fid

    def dispatch(self, grant: dict, *, journal_writer=None, on_request_left=None, on_response=None,
                 record_result: bool = True) -> dict:
        gid = grant["grant_id"]
        scope = "first" if grant.get("first_dispatch") else "recovery"
        if gid not in self._issued:
            raise PermissionError("grant was not issued by this process (orphan or foreign grant)")
        if (self.root / "void" / f"{gid}.json").exists():
            raise PermissionError("grant is void")
        # exclusive creation: a second consumption of the same grant fails here
        self._write("consumed", gid, {"kind": "consumed", "grant_id": gid,
                                      "logical_operation_id": grant["logical_operation_id"],
                                      "send_attempt_id": grant["send_attempt_id"], "generation": grant["generation"]})
        self._issued.discard(gid)
        _crash_point("after_consumed", scope)
        if journal_writer is not None:
            journal_writer(grant)
            _crash_point("after_dbc", scope)
        request = {"logical_operation_id": grant["logical_operation_id"], "attempt_id": grant["attempt_id"],
                   "transport_request_id": grant["send_attempt_id"], "generation": grant["generation"]}
        status, result = self._transmit(request, on_request_left, on_response, scope)
        if record_result:
            self._log("result", {"grant_id": gid, "logical_operation_id": grant["logical_operation_id"],
                                 "http_status": status, "status": result.get("status"), "result": result})
        return {"http_status": status, **result}

    def _post(self, path: str, body: dict) -> tuple[int, dict]:
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=40)
        raw = json.dumps(body).encode()
        client.request("POST", path, body=raw, headers={"Content-Type": "application/json"})
        response = client.getresponse()
        return response.status, json.loads(response.read() or b"{}")

    def _transmit(self, request: dict, on_request_left, on_response, scope: str) -> tuple[int, dict]:
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        body = json.dumps(request).encode()
        client.putrequest("POST", "/send")
        client.putheader("Content-Type", "application/json")
        client.putheader("Content-Length", str(len(body)))
        client.endheaders(body)
        _crash_point("after_request", scope)
        if on_request_left is not None:
            on_request_left()
        response = client.getresponse()
        raw = response.read()
        try:
            result = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            result = {"status": "UNPARSEABLE"}
        _crash_point("after_response", scope)
        if on_response is not None:
            on_response()
        return response.status, result
