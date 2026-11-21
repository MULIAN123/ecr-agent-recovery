"""W6' late-send event partial order (E8_W6_LATE_SEND_DIAGNOSTIC_DESIGN.md). Post-hoc explanatory diagnostic;
never changes scorer_v2 S4 or L. Reads only durable records of one unit directory (same layout as e8_diag).

Answers, without presupposing the answer:
  Q: was the first (held) request validly authorized AND actually sent before the second fence took effect?
Evidence classes:
  causal  : authority_seq order, fence/grant/consumed links (fence_id, grant_id), generations carried by requests
  clock   : utc_ns written by the same guest (endpoint log, authority records, E1 receipts, runner receipts)
A check fails closed: missing evidence -> UNDETERMINED, never "yes".
Usage: python -I w6_order.py <unit_dir>
"""
import json
import sys
from pathlib import Path


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def recs(d):
    d = Path(d)
    return [jl(p) for p in sorted(d.glob("*.json"))] if d.is_dir() else []


def order(unit: Path) -> dict:
    store, ev = unit / "sut" / "store", unit / "endpoint_events"
    auth = store / "authority"
    log = sorted([r for r in recs(ev) if "kind" in r and "index" in r], key=lambda r: r["index"])
    fences_ep = [r for r in log if r["kind"] == "fence"]
    sends_ep = [r for r in log if r["kind"] == "send" and not r.get("gated")]
    held = sorted(ev.glob("late-held-*.json"))
    released = sorted(ev.glob("late-released-*.json"))
    receipts = jl(unit / "receipts.json") if (unit / "receipts.json").is_file() else {}
    grants = {g["grant_id"]: g for g in recs(auth / "grants")}
    consumed = {c["grant_id"]: c for c in recs(auth / "consumed")}
    fences_auth = {f["fence_id"]: f for f in recs(auth / "fences")}
    out = {"unit": unit.name, "events": {}, "checks": {}, "answer": "UNDETERMINED", "reasons": []}
    if len(held) != 1:
        out["reasons"].append(f"expected exactly one late-held receipt, found {len(held)}")
        return out
    h = jl(held[0]); tid1 = h["transport_request_id"]
    g1 = next((g for g in grants.values() if g.get("send_attempt_id") == tid1), None)
    s1 = next((r for r in sends_ep if r["request"].get("transport_request_id") == tid1), None)
    if g1 is None or s1 is None:
        out["reasons"].append("held request has no grant or no endpoint send record")
        return out
    f1 = fences_auth.get(g1.get("fence_id")) if g1.get("basis") == "FENCED_NEGATIVE" else None
    c1 = consumed.get(g1["grant_id"])
    later_sends = [r for r in sends_ep if r["index"] < s1["index"] and r["request"].get("transport_request_id") != tid1]
    s2 = later_sends[-1] if later_sends else None
    g2 = next((g for g in grants.values() if s2 and g.get("send_attempt_id") == s2["request"].get("transport_request_id")), None)
    f2 = fences_auth.get(g2.get("fence_id")) if g2 and g2.get("basis") == "FENCED_NEGATIVE" else None
    def ep_fence(fa):  # endpoint log entry of an authority fence record: same operation and generation
        if not fa:
            return None
        gen = (fa.get("response") or {}).get("retry_generation")
        return next((r for r in fences_ep if (r.get("result") or {}).get("retry_generation") == gen), None)
    e1, e2 = ep_fence(f1), ep_fence(f2)
    rel = jl(released[0]) if released else None
    E = out["events"]
    E["1_first_fence_effective_endpoint"] = e1 and {"index": e1["index"], "utc_ns": e1.get("utc_ns"), "generation": (e1.get("result") or {}).get("retry_generation")}
    E["1_first_fence_persisted_authority"] = f1 and {"fence_id": f1["fence_id"], "authority_seq": f1.get("authority_seq"), "utc_ns": f1.get("utc_ns")}
    E["2_first_grant"] = {"grant_id": g1["grant_id"], "basis": g1.get("basis"), "fence_id": g1.get("fence_id"), "generation": g1.get("generation"),
                          "authority_seq": g1.get("authority_seq"), "utc_ns": g1.get("utc_ns")}
    E["2_first_consumed"] = c1 and {"authority_seq": c1.get("authority_seq"), "utc_ns": c1.get("utc_ns")}
    r1 = (receipts.get("recoveries") or [{}])[0]
    E["3_first_request_sent_bound"] = {"client_hook": r1.get("crash_at"), "client_exit_code": r1.get("exit_code"), "client_exit_utc_ns": r1.get("end_utc_ns"),
                                       "endpoint_received_utc_ns": h.get("utc_ns"), "request_generation": s1["request"].get("generation")}
    E["4_second_fence_effective_endpoint"] = e2 and {"index": e2["index"], "utc_ns": e2.get("utc_ns"), "generation": (e2.get("result") or {}).get("retry_generation")}
    E["5_second_request"] = s2 and {"transport_request_id": s2["request"].get("transport_request_id"), "generation": s2["request"].get("generation"),
                                     "index": s2["index"], "utc_ns": s2.get("utc_ns"), "status": (s2.get("result") or {}).get("status"),
                                     "basis": g2 and g2.get("basis")}
    E["6_first_request_admitted"] = {"released_utc_ns": rel and rel.get("utc_ns"), "index": s1["index"], "utc_ns": s1.get("utc_ns")}
    E["7_endpoint_answer_to_first"] = s1.get("result")
    C = out["checks"]
    lt = lambda a, b: (a is not None and b is not None and a < b)
    # causal checks
    if g1.get("basis") == "FENCED_NEGATIVE":
        C["c1_grant_bound_to_first_fence"] = bool(f1 and (f1.get("response") or {}).get("result") == "DEFINITELY_NOT_COMMITTED"
                                                  and (f1.get("response") or {}).get("retry_generation") == g1.get("generation") == s1["request"].get("generation"))
        C["c1_authority_seq_fence_lt_grant_lt_consumed"] = bool(f1 and c1 and lt(f1.get("authority_seq"), g1.get("authority_seq")) and lt(g1.get("authority_seq"), c1.get("authority_seq")))
        C["c4_second_fence_newer_generation"] = bool(e1 and e2 and lt((e1.get("result") or {}).get("retry_generation"), (e2.get("result") or {}).get("retry_generation")))
        C["c4_consumed_first_before_second_fence_seq"] = bool(c1 and f2 and lt(c1.get("authority_seq"), f2.get("authority_seq")))
    else:  # P2: idempotent send, no fence
        C["c1_idempotent_grant_consumed"] = bool(c1 and g1.get("basis") == "IDEMPOTENT")
    # clock checks (same guest clock)
    C["c2_consumed_before_endpoint_received"] = lt((c1 or {}).get("utc_ns"), h.get("utc_ns"))
    C["c3_endpoint_received_before_second_fence"] = lt(h.get("utc_ns"), (e2 or {}).get("utc_ns")) if e2 else None
    C["c5_admitted_after_release_after_second_request"] = lt((s2 or {}).get("utc_ns"), (rel or {}).get("utc_ns")) and lt((rel or {}).get("utc_ns"), s1.get("utc_ns"))
    status = (s1.get("result") or {}).get("status")
    C["c6_endpoint_rejected_stale"] = status == "REJECTED" and "STALE_GENERATION" in str((s1.get("result") or {}).get("error"))
    C["c6_endpoint_deduplicated"] = status == "COMMITTED" and (s1.get("result") or {}).get("deduplicated") is True
    if g1.get("basis") == "FENCED_NEGATIVE":
        needed = ["c1_grant_bound_to_first_fence", "c1_authority_seq_fence_lt_grant_lt_consumed", "c4_consumed_first_before_second_fence_seq",
                  "c2_consumed_before_endpoint_received", "c3_endpoint_received_before_second_fence"]
    else:
        needed = ["c1_idempotent_grant_consumed", "c2_consumed_before_endpoint_received"]
    missing = [k for k in needed if C.get(k) is None]
    if missing:
        out["answer"], out["reasons"] = "UNDETERMINED", [f"missing evidence: {missing}"]
    elif all(C[k] for k in needed):
        out["answer"] = "YES_VALIDLY_AUTHORIZED_AND_SENT_BEFORE_SECOND_FENCE" if g1.get("basis") == "FENCED_NEGATIVE" else "YES_VALIDLY_AUTHORIZED_IDEMPOTENT"
    else:
        out["answer"] = "NO"
        out["reasons"] = [k for k in needed if not C[k]]
    return out


if __name__ == "__main__":
    print(json.dumps(order(Path(sys.argv[1])), indent=1, sort_keys=True))
