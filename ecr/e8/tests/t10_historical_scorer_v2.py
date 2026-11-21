"""T10: run scorer_v2 over the archived Experiment 2 evidence (read-only extraction) and compare
it cell by cell with the historical scorer. Agreement is not forced: every difference is
listed for investigation, and diagnostics that need records the historical batch never wrote
are counted as UNVERIFIABLE.
Usage: python3 t10_historical_scorer_v2.py <extracted_root> <out.json>
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from scorer_v2 import score  # noqa: E402

ROOT, OUT = Path(sys.argv[1]), Path(sys.argv[2])
BATCH = "async-sim-20261012-r1"
GUEST = "/var/lib/ecr-followup-r1"
PAIRS = [("duplicate_effect", "duplicate_effect"), ("unauthorized_effect", "unauthorized_effect"),
         ("false_completion", "false_completion"), ("opportunity_completed", "opportunity_completed"),
         ("unresolved", "unresolved"), ("effect_count", "effect_count")]

rows, diffs, unverifiable = [], [], Counter()
for cell in sorted(p for p in (ROOT / "async-sim" / BATCH).iterdir() if p.is_dir()):
    ctx = json.loads((cell / "primary-context.json").read_text())
    ctx = {k: (v.replace(GUEST, str(ROOT)) if isinstance(v, str) else v) for k, v in ctx.items()}
    hist = json.loads((cell / "primary-score.json").read_text())
    new = score(ctx)
    d = {a: (new[a], hist[b]) for a, b in PAIRS if new[a] != hist[b]}
    if new["s4_invalid_sends"] != hist["false_safe_authorizations"]:
        d["s4"] = (new["s4_invalid_sends"], hist["false_safe_authorizations"])
    if new["faults"]:
        d["faults"] = new["faults"]
    for j in new["sends_judged"]:
        unverifiable["grant_binding_" + str(j["grant_binding"])] += 1
        unverifiable["label_grant_consistent_" + str(j["label_grant_consistent"])] += 1
        unverifiable["objective_only_" + str((not j["contract_valid"]) and j["objective_basis"] is not None)] += 1
    unverifiable["journal_dispatch_binding_" + new["journal_dispatch_binding"]] += 1
    if d:
        diffs.append({"run": ctx["run_id"], "scenario": new["scenario_id"], "arm": ctx["arm"], "diff": d,
                      "sends": new["sends_judged"]})
    rows.append({"run": ctx["run_id"], "arm": ctx["arm"], "s4_v2": new["s4_invalid_sends"],
                 "s4_hist": hist["false_safe_authorizations"]})

per_arm = {}
for r in rows:
    a = per_arm.setdefault(r["arm"], [0, 0])
    a[0] += r["s4_v2"]
    a[1] += r["s4_hist"]
out = {"cells": len(rows), "cells_with_differences": len(diffs), "differences": diffs,
       "s4_totals_per_arm_v2_vs_hist": per_arm, "diagnostic_counts": dict(unverifiable)}
OUT.write_text(json.dumps(out, indent=1))
print("cells", len(rows), "cells with differences", len(diffs))
print("S4 per arm (v2, historical):", per_arm)
print("diagnostics:", dict(unverifiable))
for d in diffs[:20]:
    print(d["run"], d["scenario"], d["diff"])
