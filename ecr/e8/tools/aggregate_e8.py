"""E8 aggregation (E8_SCORING_SPEC r2 sections 4-6). Read-only on evidence; writes one JSON and one Markdown file (O_EXCL).
Tables: T-MODEL, T-CSR, T-EQ, T-S4-DUAL, T-EXPLORATORY, T-NEGCTRL, predictions vs observed, non-valid attempts.
Rules (fixed in advance, not tunable after results):
  * main S1-S4 and L come ONLY from the frozen scorer_v2 output; s4_issue_order_diag is shown beside S4 in
    T-S4-DUAL and never changes T-MODEL;
  * only VALID_COMPLETED units enter T-MODEL / T-CSR / T-EQ counts; every other state is listed, none dropped;
  * G6 (FA, A4) appears only in T-EXPLORATORY and T-NEGCTRL;
  * every prediction mismatch is listed.
Usage:
  server : python3 -I aggregate_e8.py server --pkg PKG --batch BATCH_DIR --finalized FINAL_STATES.json --out-prefix P
           (FINAL_STATES.json = output of tools/finalize_states.py on the host: attempts[attempt_id].state)
  local  : python -I aggregate_e8.py local --pkg PKG --out-prefix P        (development evidence only)
  compare: python -I aggregate_e8.py compare A.json B.json --out-prefix P   (G-D between two formal runs)
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def b(x):
    return "1" if x else "0"


# ------------------------------------------------------------------------------------------------ loaders
def load_local(pkg: Path) -> dict:
    """Local harness layout: work/dev_c0, work/dev_c1 (all 269 units); W6' partial order from the timestamped
    rerun in work/dev_w6 (tools/w6_order.py)."""
    sys.path.insert(0, str(pkg / "tools"))
    import w6_order  # noqa: E402  (reads durable records only)
    out = {}
    for d in ("work/dev_c0", "work/dev_c1"):
        for uid, r in jl(pkg / d / "results.json").items():
            view = pkg / d / r["name"]
            w6 = pkg / "work/dev_w6" / r["name"]
            out[uid] = {"state": "DEV_LOCAL", "scored": r["scored"], "diag": r["diag"],
                        "w6_order": w6_order.order(w6) if w6.is_dir() else None,
                        "exit_codes": [x["exit_code"] for x in jl(view / "receipts.json")["recoveries"]], "view": str(view)}
    return out


def load_server(batch: Path, finalized: dict) -> dict:
    out = {}
    for d in sorted((batch / "attempts").iterdir()):
        if not (d / "attempt-result.json").is_file():
            continue
        r = jl(d / "attempt-result.json")
        state = finalized.get(r.get("attempt_id"), r.get("state"))
        out[r["unit_id"]] = {"state": state, "scored": r.get("primary_score"), "diag": r.get("e8_diag"), "w6_order": r.get("w6_order"),
                             "exit_codes": [x["exit_code"] for x in r.get("e8", {}).get("recoveries", [])], "view": str(d / "diag"),
                             "attempt_id": r.get("attempt_id"), "state_reasons": r.get("state_reasons", [])}
    return out


VALID = ("VALID_COMPLETED", "DEV_LOCAL")


# ------------------------------------------------------------------------------------------------ observation
def observe(u: dict, r: dict) -> dict:
    s, d = r["scored"], r["diag"]
    return {"effects": str(s["effect_count"]), "durable_task_state": d["durable_final_state"], "scorer_final_state": s["final_state"],
            "S1": b(s["duplicate_effect"]), "S2": b(s["unauthorized_effect"]), "S3": b(s["false_completion"]), "S4": b(s["s4_invalid_sends"] > 0),
            "L": "n/a" if not u["truth"]["safe_opportunity"] else b(s["opportunity_completed"]),
            "completion_regression": str(d["completion_state_regression"]), "stall": str(d["stall"]),
            "recovery_exit_codes": ",".join(str(x) for x in r["exit_codes"])}


PRED_KEYS = ("effects", "durable_task_state", "scorer_final_state", "S1", "S2", "S3", "S4", "L", "completion_regression", "stall",
             "recovery_exit_codes")


def norm_view(view: Path, scored: dict):
    """Frozen EQ normalisation (identical to tools/local_qualification.py and vq_e8_checks.norm_eq)."""
    drop = {"utc_ns", "grant_id", "grants", "index", "pid"}

    def clean(x):
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items() if k not in drop}
        if isinstance(x, list):
            return [clean(v) for v in x]
        if isinstance(x, str):
            return re.sub(r"\b[0-9a-f]{32}\b", "UUID", re.sub(r"E8Q?-\d{2,4}", "NAME", x))
        return x
    acts = [jl(p) for p in sorted((view / "sut/store/recovery").glob("*.json"))]
    log = [{k: v for k, v in jl(p).items() if k != "index"} for p in sorted((view / "endpoint_events").glob("log-*.json"))]
    sc = {k: v for k, v in scored.items() if k not in ("run_id", "scorer_pid", "scorer_uid", "arm")}
    return {"scored": clean(sc), "recovery_records": clean(acts), "endpoint_log": clean(log)}


def rate(n, k):
    return f"{k}/{n}"


# ------------------------------------------------------------------------------------------------ aggregate
def aggregate(pkg: Path, res: dict, source: str) -> dict:
    cells = {u["unit_id"]: u for u in jl(pkg / "private/cells_e8.json")["units"]}
    preds = jl(pkg / "frozen/predictions_e8.json")["predictions"]
    missing = sorted(set(cells) - set(res))
    nonvalid = [{"unit_id": uid, "state": r["state"], "reasons": r.get("state_reasons", [])} for uid, r in res.items() if r["state"] not in VALID]
    valid = {uid for uid, r in res.items() if r["state"] in VALID and uid in cells}
    obs = {uid: observe(cells[uid], res[uid]) for uid in valid}
    # T-MODEL
    tm = defaultdict(lambda: Counter())
    for uid in sorted(valid):
        u = cells[uid]
        if u["counted_in"] != "MODEL":
            continue
        key = (u["group"], u["window"], u["scenario"]["profile"], u["r2_mode"])
        c = tm[key]
        c["n"] += 1
        for k in ("S1", "S2", "S3", "S4"):
            c[k] += obs[uid][k] == "1"
        if obs[uid]["L"] != "n/a":
            c["L_opp"] += 1
            c["L_done"] += obs[uid]["L"] == "1"
    t_model = [{"group": g, "window": w, "profile": p, "mode": m, "n": c["n"], "S1": c["S1"], "S2": c["S2"], "S3": c["S3"], "S4": c["S4"],
                "L": rate(c["L_opp"], c["L_done"])} for (g, w, p, m), c in sorted(tm.items())]
    model_units = [uid for uid in valid if cells[uid]["counted_in"] == "MODEL"]
    model_tot = {k: sum(obs[uid][k] == "1" for uid in model_units) for k in ("S1", "S2", "S3", "S4")}
    model_tot["n"] = len(model_units)
    model_tot["L"] = dict(Counter(obs[uid]["L"] for uid in model_units))
    # T-CSR
    tc = defaultdict(Counter)
    for uid in model_units:
        u, d = cells[uid], res[uid]["diag"]
        c = tc[(u["group"], u["window"])]
        c["n"] += 1
        c["CSR"] += d["completion_state_regression"]
        c["stall"] += d["stall"]
        c["durable_L_diag"] += d["durable_L_diag"]
        c["L"] += obs[uid]["L"] == "1"
        c["csr_source_disagree"] += bool(d["csr_source_disagree"])
    t_csr = [{"group": g, "window": w, **dict(c)} for (g, w), c in sorted(tc.items())]
    # T-EQ
    eq = defaultdict(dict)
    for uid, u in cells.items():
        if u["window"] == "EQ":
            eq[uid.rsplit(":", 1)[0]][u["extra_condition"]] = uid
    t_eq = []
    for key, g in sorted(eq.items()):
        row = {"scenario": key, "units": g}
        if not all(x in valid for x in g.values()):
            row["result"] = "NOT_ALL_VALID"
        else:
            ref = norm_view(Path(res[g["eq_ref"]]["view"]), res[g["eq_ref"]]["scored"])
            diffs = {}
            for t in ("eq_hooks_inert", "eq_fault_endpoint_off"):
                other = norm_view(Path(res[g[t]]["view"]), res[g[t]]["scored"])
                diffs[t] = [k for k in ref if ref[k] != other[k]]
            row["result"] = "PASS" if not any(diffs.values()) else "FAIL"
            row["differing_fields"] = diffs
        t_eq.append(row)
    # T-S4-DUAL
    t_s4 = []
    for uid in sorted(valid):
        d = res[uid]["diag"]
        fs = d["s4_issue_order_diag"]["fenced_sends"]
        if not fs:
            continue
        t_s4.append({"unit_id": uid, "window": cells[uid]["window"], "counted_in": cells[uid]["counted_in"],
                     "scorer_v2_S4_invalid_sends": res[uid]["scored"]["s4_invalid_sends"],
                     "issue_order_invalid": d["s4_issue_order_diag"]["invalid_by_issue_order"],
                     "late_arrivals": sum(x["late_arrival"] for x in fs), "differs": (res[uid]["scored"]["s4_invalid_sends"] > 0) != (d["s4_issue_order_diag"]["invalid_by_issue_order"] > 0),
                     "w6_order_answer": (res[uid].get("w6_order") or {}).get("answer") if cells[uid]["window"] == "W6'" else None})
    # G6
    def listing(win):
        return [{"unit_id": uid, "state": res.get(uid, {}).get("state", "MISSING"), **(obs.get(uid) or {}),
                 "scorer_faults": (res.get(uid, {}).get("scored") or {}).get("faults"), "path": (res.get(uid, {}).get("diag") or {}).get("path")}
                for uid, u in sorted(cells.items()) if u["window"] == win]
    # predictions vs observed
    mism = []
    for uid in sorted(valid):
        p = preds[uid]
        diffs = {k: (p[k], obs[uid][k]) for k in PRED_KEYS if p.get(k, "") != "" and str(p[k]) != obs[uid][k]}
        if diffs:
            u = cells[uid]
            mism.append({"unit_id": uid, "group": u["group"], "window": u["window"], "counted_in": u["counted_in"], "diffs": diffs})
    return {"source": source, "evidence_class": "DEVELOPMENT_EVIDENCE_NOT_A_RESULT" if source == "local" else "FORMAL_RUN_PENDING_REVIEW",
            "units_expected": len(cells), "units_with_result": len(res), "missing_units": missing, "valid_units": len(valid),
            "non_valid_attempts": nonvalid, "T_MODEL_totals": model_tot, "T_MODEL": t_model, "T_CSR": t_csr, "T_EQ": t_eq,
            "T_S4_DUAL": t_s4, "T_EXPLORATORY": listing("FA"), "T_NEGCTRL": listing("A4"),
            "prediction_mismatches": mism, "mismatch_count": len(mism), "observed": obs,
            "per_unit": {uid: {"scored": res[uid]["scored"], "diag": {k: v for k, v in res[uid]["diag"].items()}} for uid in sorted(valid)}}


def compare(a: dict, b_: dict) -> dict:
    """G-D: scorer_v2 fields and e8_diag fields except `path` equal per unit between two formal runs."""
    drop = {"run_id", "scorer_pid", "scorer_uid", "arm"}
    units = sorted(set(a["per_unit"]) | set(b_["per_unit"]))
    diffs = []
    for uid in units:
        x, y = a["per_unit"].get(uid), b_["per_unit"].get(uid)
        if x is None or y is None:
            diffs.append({"unit_id": uid, "only_in": "A" if y is None else "B"}); continue
        sx = {k: v for k, v in x["scored"].items() if k not in drop}
        sy = {k: v for k, v in y["scored"].items() if k not in drop}
        dx = {k: v for k, v in x["diag"].items() if k != "path"}
        dy = {k: v for k, v in y["diag"].items() if k != "path"}
        if sx != sy or dx != dy:
            diffs.append({"unit_id": uid, "scorer_fields": sorted(k for k in set(sx) | set(sy) if sx.get(k) != sy.get(k)),
                          "diag_fields": sorted(k for k in set(dx) | set(dy) if dx.get(k) != dy.get(k))})
    return {"gate": "G-D", "status": "IDENTICAL" if not diffs else "DIFFERENCES_TO_ANALYSE", "units_compared": len(units), "differences": diffs,
            "rule": "differences are analysed one by one; no majority vote and no choice of one run"}


def markdown(rep: dict) -> str:
    L = [f"# E8 aggregate ({rep['source']})", "", f"Evidence class: **{rep['evidence_class']}**", "",
         f"Units expected {rep['units_expected']}, with result {rep['units_with_result']}, valid {rep['valid_units']}, "
         f"missing {len(rep['missing_units'])}, non-valid {len(rep['non_valid_attempts'])}.", "", "## T-MODEL totals (scorer_v2 only)", "",
         "```", json.dumps(rep["T_MODEL_totals"]), "```", "", "## T-MODEL", "", "| group | window | profile | mode | n | S1 | S2 | S3 | S4 | L |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    L += [f"| {r['group']} | {r['window']} | {r['profile']} | {r['mode']} | {r['n']} | {r['S1']} | {r['S2']} | {r['S3']} | {r['S4']} | {r['L']} |" for r in rep["T_MODEL"]]
    L += ["", "## T-CSR (diagnostic; never part of S1-S4)", "", "| group | window | n | CSR | stall | durable_L_diag | L | source disagree |", "|---|---|---|---|---|---|---|---|"]
    L += [f"| {r['group']} | {r['window']} | {r['n']} | {r.get('CSR', 0)} | {r.get('stall', 0)} | {r.get('durable_L_diag', 0)} | {r.get('L', 0)} | {r.get('csr_source_disagree', 0)} |" for r in rep["T_CSR"]]
    L += ["", "## T-EQ", "", f"PASS {sum(r['result'] == 'PASS' for r in rep['T_EQ'])} / {len(rep['T_EQ'])}; "
          f"failures: {[r['scenario'] for r in rep['T_EQ'] if r['result'] != 'PASS']}", "",
          "## T-S4-DUAL (main S4 = scorer_v2; issue-order column is post-hoc explanatory)", "",
          "| unit | window | counted_in | scorer_v2 S4 invalid sends | issue-order invalid | late arrivals | differs | W6' answer |", "|---|---|---|---|---|---|---|---|"]
    L += [f"| {r['unit_id']} | {r['window']} | {r['counted_in']} | {r['scorer_v2_S4_invalid_sends']} | {r['issue_order_invalid']} | {r['late_arrivals']} | {r['differs']} | {r['w6_order_answer']} |" for r in rep["T_S4_DUAL"]]
    for name in ("T_EXPLORATORY", "T_NEGCTRL"):
        L += ["", f"## {name.replace('_', '-')} (outside the model; never merged into T-MODEL)", "", "| unit | state | effects | S1 | S2 | S3 | S4 | L | scorer faults |", "|---|---|---|---|---|---|---|---|---|"]
        L += [f"| {r['unit_id']} | {r['state']} | {r.get('effects')} | {r.get('S1')} | {r.get('S2')} | {r.get('S3')} | {r.get('S4')} | {r.get('L')} | {r.get('scorer_faults')} |" for r in rep[name]]
    L += ["", f"## Predictions vs observed: {rep['mismatch_count']} mismatching units (all listed, none adjusted)", ""]
    L += [f"- {m['unit_id']} ({m['group']} {m['window']} {m['counted_in']}): {m['diffs']}" for m in rep["prediction_mismatches"]]
    L += ["", "## Non-valid attempts (listed, not dropped)", ""] + [f"- {x['unit_id']}: {x['state']} {x['reasons']}" for x in rep["non_valid_attempts"]]
    return "\n".join(L) + "\n"


def write_x(path: Path, text: str) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


if __name__ == "__main__":
    cmd, a = sys.argv[1], sys.argv[2:]
    get = lambda k: a[a.index(k) + 1]
    prefix = Path(get("--out-prefix"))
    if cmd == "compare":
        rep = compare(jl(a[0]), jl(a[1]))
        write_x(prefix.with_suffix(".json"), json.dumps(rep, indent=1, sort_keys=True))
        print(json.dumps({k: v for k, v in rep.items() if k != "differences"}, indent=1), "differences:", len(rep["differences"]))
        sys.exit(0)
    pkg = Path(get("--pkg"))
    res = load_local(pkg) if cmd == "local" else load_server(Path(get("--batch")), {a: r["state"] for a, r in jl(get("--finalized"))["attempts"].items()})
    rep = aggregate(pkg, res, cmd)
    write_x(prefix.with_suffix(".json"), json.dumps(rep, indent=1, sort_keys=True, default=str))
    write_x(prefix.with_suffix(".md"), markdown(rep))
    print(json.dumps({k: rep[k] for k in ("evidence_class", "valid_units", "T_MODEL_totals", "mismatch_count")}, indent=1),
          "| T-EQ pass", sum(r["result"] == "PASS" for r in rep["T_EQ"]), "/", len(rep["T_EQ"]), "| T-S4-DUAL rows", len(rep["T_S4_DUAL"]))
