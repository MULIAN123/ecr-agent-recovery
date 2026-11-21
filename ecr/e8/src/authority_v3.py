"""Send-authorization boundary for ECR-G v3 (bl6): the frozen EBRQ-v2 Authority plus a QUARANTINE path.

Why: in bl5 every held-out composite scenario (a local outcome fault plus a record of another attempt)
made the frozen validator report invalid local evidence; the v2 gate then denies every fence and send,
so ECR-G v2 sent all of them to review even where the endpoint contract (P1/P2/P3) could settle the
outcome. v3 lets the endpoint settle it while the local journal is QUARANTINED (never used as a basis).

Quarantine eligibility (all must hold; otherwise nothing changes and v2 behaviour applies):
  Q1 the authority records themselves are not anomalous (report.frozen is False);
  Q2 no EV2 version failure: superseded or version-inconsistent work is never reconciled or resent;
  Q3 R2B: the durable AttemptCommitted key exists and equals the configured key (R2A: assumption A4).
Quarantined decisions read ONLY the configured key, the declared endpoint contract and the authority
records (fences, grants, consumed markers, endpoint results); journal outcome records are ignored:
  fence      : contract offers a query; no endpoint commit already recorded for the key
  FENCED_NEG : contract offers a query; latest negative fence after the last consumed grant (from the
               authority records); request generation equals the fenced generation
  IDEMPOTENT : contract offers idempotency; the original first-dispatch key (consumed first-dispatch
               grant) exists and equals the configured key; generation 0
  every send : request key == configured key; send-attempt id unused; no endpoint commit recorded.
Nothing else is relaxed: PRE_DISPATCH is never granted in quarantine.
"""
from __future__ import annotations

import json
import uuid

from authority import Authority, _crash_point
from evidence import load_state
from protocol import digest

Q_BASES = ("FENCED_NEGATIVE", "IDEMPOTENT")


class AuthorityV3(Authority):
    def quarantine_eligibility(self) -> dict:
        rep = self.report()
        blockers = []
        if rep.frozen:
            blockers.append("Q1 authority-record anomaly: key frozen")
        if any(r.startswith("EV2") for r in rep.reasons):
            blockers.append("Q2 version conflict: superseded or inconsistent work is never reconciled")
        if self.mode == "R2B" and any(("lacks logicalOperationId" in r) or ("differs from configured key" in r) for r in rep.reasons):
            blockers.append("Q3 R2B operation key not durably bound")
        return {"eligible": not blockers, "blockers": blockers, "local_reasons": list(rep.reasons)}

    def _authority_facts(self) -> dict:
        """Facts from the authority records only (never from the journal)."""
        state = load_state(self.store)
        key = self.config_key
        committed = any(e.get("kind") == "result" and e.get("status") == "COMMITTED" and e.get("logical_operation_id") == key
                        for e in state["log"]) or \
            any(f.get("logical_operation_id") == key and (f.get("response") or {}).get("result") == "COMMITTED"
                for f in state["fences"])
        grants = {g.get("grant_id"): g for g in state["grants"]}
        consumed = state["consumed"]
        first = [grants[c["grant_id"]] for c in consumed if c.get("grant_id") in grants and grants[c["grant_id"]].get("first_dispatch")]
        last_consumed_seq = max((c.get("authority_seq", 0) for c in consumed), default=0)
        negatives = [f for f in state["fences"] if f.get("logical_operation_id") == key
                     and (f.get("response") or {}).get("result") == "DEFINITELY_NOT_COMMITTED"
                     and f.get("authority_seq", 0) > last_consumed_seq]
        return {"endpoint_committed": committed,
                "original_key": first[0].get("logical_operation_id") if len(first) == 1 else None,
                "first_dispatch_grants": len(first),
                "unconsumed_fence": max(negatives, key=lambda f: f["authority_seq"]) if negatives else None}

    def fence_quarantined(self) -> tuple[int, dict, str] | None:
        rep = self.report()
        q = self.quarantine_eligibility()
        facts = self._authority_facts()
        reasons = list(q["blockers"])
        if not self.contract["query"]:
            reasons.append("endpoint contract offers no fenced query")
        if facts["endpoint_committed"]:
            reasons.append("endpoint commit already recorded for this key")
        if reasons:
            self._deny("FENCE_QUARANTINED", None, reasons, rep)
            return None
        status, result = self._post("/fence", {"logical_operation_id": self.config_key})
        fid = f"{self._next_seq():05d}"
        self._write("fences", fid, {"kind": "fence", "fence_id": fid, "logical_operation_id": self.config_key,
                                    "http_status": status, "response": result, "quarantined": True})
        return status, result, fid

    def authorize_quarantined(self, basis: str, request: dict) -> dict | None:
        rep = self.report()
        q = self.quarantine_eligibility()
        facts = self._authority_facts()
        reasons = list(q["blockers"])
        if basis not in Q_BASES:
            reasons.append(f"basis {basis!r} is not available in quarantine")
        if request.get("logical_operation_id") != self.config_key:
            reasons.append("request key differs from the configured logical operation key")
        if facts["endpoint_committed"]:
            reasons.append("endpoint commit already recorded for this key")
        used = {json.loads(p.read_text()).get("send_attempt_id") for p in (self.root / "grants").glob("*.json")}
        if request.get("transport_request_id") in used:
            reasons.append("send-attempt id already used by an earlier grant")
        gen = request.get("generation")
        fence = None
        if basis == "FENCED_NEGATIVE":
            fence = facts["unconsumed_fence"]
            if not self.contract["query"]:
                reasons.append("endpoint contract offers no fenced query")
            if fence is None:
                reasons.append("no durable negative fence result after the last consumed grant")
            elif gen != fence["response"].get("retry_generation"):
                reasons.append("request generation differs from the fenced generation")
        elif basis == "IDEMPOTENT":
            if not self.contract["idempotent"]:
                reasons.append("endpoint contract offers no idempotency")
            if facts["original_key"] is None:
                reasons.append("original first-dispatch key cannot be established from the authority records")
            elif facts["original_key"] != self.config_key:
                reasons.append("original first-dispatch key differs from the configured key")
            if gen != 0:
                reasons.append("idempotent resend must use generation 0")
        if reasons:
            return self._deny(basis + "_QUARANTINED", request, reasons, rep)
        gid = uuid.uuid4().hex
        grant = self._write("grants", gid, {
            "kind": "grant", "grant_id": gid, "basis": basis, "mode": self.mode, "quarantined": True,
            "logical_operation_id": request["logical_operation_id"], "idempotency_key": request["logical_operation_id"],
            "operation_version": rep.version, "attempt_id": request.get("attempt_id"),
            "send_attempt_id": request.get("transport_request_id"), "generation": gen,
            "fence_id": (fence or {}).get("fence_id"), "first_dispatch": False,
            "contract": self.contract, "evidence_digest": digest({"quarantine": q, "facts": {k: v for k, v in facts.items()
                                                                                             if k != "unconsumed_fence"}})})
        self._issued.add(gid)
        _crash_point("after_grant", "recovery")
        return grant
