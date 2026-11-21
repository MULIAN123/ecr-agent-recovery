"""Post-hoc interval estimates and exact tests for Experiment 3 / 3R (DSH).

Stdlib only. Reads the archived per-trial score files (never modifies them),
recomputes every count used, checks each against the numbers printed in
manuscript v7 (Tables 4-6 and the RQ3 text), and writes JSON + Markdown.

Usage:
  python -I ecr_exp3_statistics.py <ECR_FOLLOWUP_R1 dir> <output dir>

Methods (none was preregistered; all are reported as post hoc):
  * Wilson score 95% interval for each proportion (Clopper-Pearson kept as a
    sensitivity column).
  * Newcombe hybrid-score 95% interval for a difference of two proportions.
  * Fisher exact test, two-sided (sum of tables no more probable than the
    observed one), Holm-adjusted over the family of contrasts.
  * Exact conditional stratified test (strata = tool x kill point x
    description) for the arm contrast, because trials share design cells.
  * Distribution-free 95% interval for a median from binomial order statistics.
"""

import csv
import hashlib
import json
import math
import sys
from fractions import Fraction
from pathlib import Path

Z = 1.959963984540054  # two-sided 95 %
EXCLUDED = {"dsh-r1r-228-D2-plain-slow-k250-rep3"}  # unrequested warm-up send


# ---------------------------------------------------------------- estimators
def wilson(k, n):
    p = k / n
    den = 1 + Z * Z / n
    centre = (p + Z * Z / (2 * n)) / den
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def _beta_quantile(q, a, b):
    """Quantile of Beta(a, b) by bisection on the regularized incomplete beta,
    using the binomial identity (a, b integers here)."""
    # I_x(a, b) = P(Bin(a+b-1, x) >= a)
    n = a + b - 1

    def cdf(x):
        return sum(math.comb(n, j) * x**j * (1 - x) ** (n - j) for j in range(a, n + 1))

    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if cdf(mid) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def clopper_pearson(k, n):
    lo = 0.0 if k == 0 else _beta_quantile(0.025, k, n - k + 1)
    hi = 1.0 if k == n else _beta_quantile(0.975, k + 1, n - k)
    return lo, hi


def newcombe(k1, n1, k2, n2):
    p1, p2 = k1 / n1, k2 / n2
    l1, u1 = wilson(k1, n1)
    l2, u2 = wilson(k2, n2)
    d = p1 - p2
    lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return d, lo, hi


def _hyper(a, n1, n2, t):
    """P(X = a) for X ~ Hypergeometric: a successes in group 1 of size n1,
    t successes in total, n1 + n2 trials. Exact rational."""
    return Fraction(math.comb(n1, a) * math.comb(n2, t - a), math.comb(n1 + n2, t))


def fisher_two_sided(k1, n1, k2, n2):
    t = k1 + k2
    lo, hi = max(0, t - n2), min(n1, t)
    pmf = {a: _hyper(a, n1, n2, t) for a in range(lo, hi + 1)}
    obs = pmf[k1]
    return float(sum(p for p in pmf.values() if p <= obs))


def stratified_exact(strata):
    """strata: list of (k1, n1, k2, n2). Exact conditional test of no arm
    effect in any stratum; statistic = sum of k1. Two-sided by the same
    'no more probable than observed' rule as Fisher."""
    dist = {0: Fraction(1)}
    observed = 0
    for k1, n1, k2, n2 in strata:
        t = k1 + k2
        observed += k1
        lo, hi = max(0, t - n2), min(n1, t)
        step = {a: _hyper(a, n1, n2, t) for a in range(lo, hi + 1)}
        new = {}
        for s, ps in dist.items():
            for a, pa in step.items():
                new[s + a] = new.get(s + a, Fraction(0)) + ps * pa
        dist = new
    obs = dist[observed]
    return float(sum(p for p in dist.values() if p <= obs)), observed


def median_ci(values):
    """Median and distribution-free 95 % interval from order statistics."""
    x = sorted(values)
    n = len(x)
    med = (x[(n - 1) // 2] + x[n // 2]) / 2
    # largest j with P(Bin(n, .5) < j) <= .025  ->  [x_(j), x_(n-j+1)]
    cum, j = 0.0, 0
    while True:
        nxt = cum + math.comb(n, j) / 2**n
        if nxt > 0.025:
            break
        cum, j = nxt, j + 1
    # x_(j) is 1-based order statistic j; coverage = 1 - 2*cum
    return med, x[j - 1], x[n - j], 1 - 2 * cum


def holm(pvals):
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    adj = [0.0] * len(pvals)
    running = 0.0
    m = len(pvals)
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvals[i]))
        adj[i] = running
    return adj


# ---------------------------------------------------------------- data
def load(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def flush_values(rows):
    out = []
    for r in rows:
        if r["arm"] in ("D1", "D2"):
            out += [v / 1000.0 for v in json.loads(r["flush_latency_us"])]
    return out


def main(root, outdir):
    root, outdir = Path(root), Path(outdir)
    real_csv = root / "environment-runs" / "20261013-dsh-r1r" / "per-trial-scores.csv"
    scr_csv = root / "environment-runs" / "20261013-dsh-r1" / "per-trial-scores.csv"
    real_all = load(real_csv)
    scr = load(scr_csv)
    real = [r for r in real_all if r["trial_id"] not in EXCLUDED]
    assert len(real_all) == 235 and len(real) == 234, (len(real_all), len(real))
    assert len(scr) == 252

    yes = lambda v: v == "True"
    post = lambda r: r["kill"] != "pre"
    resent = lambda r: int(r["model_resend_attempts"]) > 0
    state = {
        "CALL_NOT_DURABLE": "lost",
        "REQUEST_NOT_DURABLE": "lost",
        "CALL_DURABLE_RESULT_NOT_DURABLE": "unknown",
        "CALL_AND_RESULT_DURABLE": "durable",
    }

    def count(rows, pred):
        sel = [r for r in rows if pred(r)]
        return sel

    checks = []

    def check(name, got, want):
        checks.append({"check": name, "got": got, "manuscript": want, "ok": got == want})

    # --- Table 5 (real model) per arm x description
    tab5 = {("D0", "warn"): (33, 18), ("D0", "plain"): (33, 18), ("D1", "warn"): (33, 0),
            ("D1", "plain"): (33, 0), ("D2", "warn"): (34, 0), ("D2", "plain"): (32, 0)}
    for (arm, desc), (n_post, dup) in tab5.items():
        p = count(real, lambda r: r["arm"] == arm and r["desc"] == desc and post(r))
        check(f"T5 {arm}-{desc} post trials", len(p), n_post)
        check(f"T5 {arm}-{desc} duplicates", sum(yes(r["duplicate"]) for r in p), dup)

    def arm_post(arm):
        p = count(real, lambda r: r["arm"] == arm and post(r))
        return sum(yes(r["duplicate"]) for r in p), len(p)

    d0 = arm_post("D0")
    d1 = arm_post("D1")
    d2 = arm_post("D2")
    check("D0 post dup", d0, (36, 66))
    check("D1 post dup", d1, (0, 66))
    check("D2 post dup", d2, (0, 66))

    # --- Table 6 (behaviour by DSH state)
    beh = {}
    for st in ("lost", "unknown", "durable"):
        for desc in ("warn", "plain"):
            sel = count(real, lambda r: state[r["durable_state_at_kill"]] == st and r["desc"] == desc)
            beh[(st, desc)] = (sum(resent(r) for r in sel), len(sel))
    want6 = {("lost", "warn"): (24, 24), ("lost", "plain"): (23, 24),
             ("unknown", "warn"): (0, 66), ("unknown", "plain"): (2, 65),
             ("durable", "warn"): (0, 28), ("durable", "plain"): (0, 27)}
    for key, w in want6.items():
        check(f"T6 {key[0]}-{key[1]}", beh[key], w)
    lost = tuple(map(sum, zip(beh[("lost", "warn")], beh[("lost", "plain")])))
    unk = tuple(map(sum, zip(beh[("unknown", "warn")], beh[("unknown", "plain")])))
    dur = tuple(map(sum, zip(beh[("durable", "warn")], beh[("durable", "plain")])))
    check("lost resend", lost, (47, 48))
    check("unknown resend", unk, (2, 131))
    check("durable resend", dur, (0, 55))

    # --- pre-effect completion (cost)
    def pre_done(arms):
        sel = count(real, lambda r: r["arm"] in arms and not post(r))
        return sum(yes(r["completed"]) for r in sel), len(sel)

    pre_d0 = pre_done({"D0"})
    pre_d12 = pre_done({"D1", "D2"})
    check("pre D0 completed", pre_d0, (11, 12))
    check("pre D1+D2 completed", pre_d12, (0, 24))

    # --- guard denials
    check("guard denials D2", sum(int(r["guard_denials"]) for r in real if r["arm"] == "D2"), 2)

    # --- flush latency
    fr = flush_values(real_all)  # paper n=159 includes trial 228's dispatch
    fs = flush_values(scr)
    check("real flush n", len(fr), 159)
    check("scripted flush n", len(fs), 228)
    med_r = median_ci(fr)
    med_s = median_ci(fs)
    check("real flush median ms (2 dp)", round(med_r[0], 2), 3.04)
    check("scripted flush median ms (2 dp)", round(med_s[0], 2), 1.28)

    # --- scripted batch (deterministic, counts only; checked for completeness)
    def scr_post(arm, pol):
        sel = [r for r in scr if r["arm"] == arm and r["policy"] == pol and r["kill"] != "pre"]
        return sum(yes(r["duplicate"]) for r in sel), len(sel)

    for (arm, pol), w in {("D0", "obedient"): (18, 36), ("D0", "naive"): (24, 36),
                           ("D1", "obedient"): (0, 36), ("D1", "naive"): (24, 36),
                           ("D2", "obedient"): (0, 36), ("D2", "naive"): (0, 36)}.items():
        check(f"T4 {arm}-{pol}", scr_post(arm, pol), w)

    failed = [c for c in checks if not c["ok"]]
    if failed:
        print(json.dumps(failed, indent=1))
        raise SystemExit("count check failed; nothing written")

    # ---------------------------------------------------------------- proportions
    props = [
        ("D0 post-effect duplicates", *d0),
        ("D1 post-effect duplicates", *d1),
        ("D2 post-effect duplicates", *d2),
        ("D1+D2 post-effect duplicates", d1[0] + d2[0], d1[1] + d2[1]),
        ("Resend after lost call record", *lost),
        ("Resend after TOOL_OUTCOME_UNKNOWN", *unk),
        ("  ... warn description", *beh[("unknown", "warn")]),
        ("  ... plain description", *beh[("unknown", "plain")]),
        ("Resend after durable result", *dur),
        ("D0 duplicates reported as single success", 35, 36),
        ("Pre-effect completed, D0", *pre_d0),
        ("Pre-effect completed, D1+D2", *pre_d12),
        ("Unrequested warm-up send", 1, 252),
    ]
    prop_out = []
    for name, k, n in props:
        w = wilson(k, n)
        cp = clopper_pearson(k, n)
        prop_out.append({"quantity": name, "k": k, "n": n, "p": k / n,
                         "wilson95": w, "clopper_pearson95": cp})

    # ---------------------------------------------------------------- contrasts
    contrasts = [
        ("Post-effect duplicates: D0 vs D1", d0, d1),
        ("Post-effect duplicates: D0 vs D2", d0, d2),
        ("Model resend: lost record vs TOOL_OUTCOME_UNKNOWN", lost, unk),
        ("Resend on unknown: plain vs warn", beh[("unknown", "plain")], beh[("unknown", "warn")]),
        ("D0 post-effect duplicates: warn vs plain",
         (18, 33), (18, 33)),
        ("Pre-effect completed: D0 vs D1+D2", pre_d0, pre_d12),
    ]
    con_out = []
    for name, (k1, n1), (k2, n2) in contrasts:
        d, lo, hi = newcombe(k1, n1, k2, n2)
        con_out.append({"contrast": name, "a": [k1, n1], "b": [k2, n2],
                        "risk_difference": d, "newcombe95": [lo, hi],
                        "fisher_p": fisher_two_sided(k1, n1, k2, n2)})
    adj = holm([c["fisher_p"] for c in con_out])
    for c, a in zip(con_out, adj):
        c["holm_p"] = a

    # stratified (tool x kill x description), post-effect, D0 vs D1 and D0 vs D2
    def strata(arm_b):
        out = []
        keys = sorted({(r["tool"], r["kill"], r["desc"]) for r in real if post(r)})
        for key in keys:
            cell = lambda arm: [r for r in real if r["arm"] == arm and post(r)
                                and (r["tool"], r["kill"], r["desc"]) == key]
            a, b = cell("D0"), cell(arm_b)
            if not a or not b:
                continue
            out.append((sum(yes(r["duplicate"]) for r in a), len(a),
                        sum(yes(r["duplicate"]) for r in b), len(b)))
        return out

    strat_out = []
    for arm_b in ("D1", "D2"):
        s = strata(arm_b)
        p, obs = stratified_exact(s)
        informative = sum(1 for k1, n1, k2, n2 in s if k1 + k2 > 0)
        strat_out.append({"contrast": f"D0 vs {arm_b}, stratified by tool x kill x description",
                          "strata": len(s), "informative_strata": informative,
                          "observed_D0_duplicates": obs, "exact_p": p})

    medians = [
        {"batch": "real model (3R)", "n": len(fr), "median_ms": med_r[0],
         "ci95_ms": [med_r[1], med_r[2]], "coverage": med_r[3]},
        {"batch": "scripted (3)", "n": len(fs), "median_ms": med_s[0],
         "ci95_ms": [med_s[1], med_s[2]], "coverage": med_s[3]},
    ]

    result = {
        "inputs": {str(real_csv): sha256(real_csv), str(scr_csv): sha256(scr_csv)},
        "excluded_trials": sorted(EXCLUDED),
        "count_checks": checks,
        "proportions": prop_out,
        "contrasts": con_out,
        "stratified": strat_out,
        "flush_median": medians,
        "note": "No inferential analysis was preregistered; everything here is post hoc. "
                "'D0 duplicates reported as single success' (35/36) is taken from the manual "
                "reading of replies in RESULTS.md and is not recomputed.",
    }
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "exp3_statistics.json").write_text(json.dumps(result, indent=1), encoding="utf-8")

    pct = lambda x: f"{100 * x:.1f}"
    fp = lambda p: "<0.0001" if p < 1e-4 else f"{p:.4f}"
    md = ["# Experiment 3 / 3R: post-hoc interval estimates and exact tests", "",
          "Generated by `ecr_exp3_statistics.py` (stdlib only). "
          f"All {len(checks)} count checks against manuscript v7 passed. "
          "No inferential analysis was preregistered; all of this is post hoc.", "",
          "Inputs (SHA-256):", ""]
    md += [f"- `{k}`: `{v}`" for k, v in result["inputs"].items()]
    md += ["", "Trial 228 excluded (unrequested warm-up send), as in the paper.", "",
           "## Proportions (95% intervals, %)", "",
           "| Quantity | k/n | % | Wilson | Clopper-Pearson |", "|---|---|---|---|---|"]
    for p in prop_out:
        w, c = p["wilson95"], p["clopper_pearson95"]
        md.append(f"| {p['quantity'].strip()} | {p['k']}/{p['n']} | {pct(p['p'])} | "
                  f"{pct(w[0])}–{pct(w[1])} | {pct(c[0])}–{pct(c[1])} |")
    md += ["", "## Contrasts (two-sided Fisher exact; Holm over the 6 contrasts)", "",
           "| Contrast | a | b | Difference, pp (Newcombe 95%) | Fisher p | Holm p |",
           "|---|---|---|---|---|---|"]
    for c in con_out:
        lo, hi = c["newcombe95"]
        md.append(f"| {c['contrast']} | {c['a'][0]}/{c['a'][1]} | {c['b'][0]}/{c['b'][1]} | "
                  f"{pct(c['risk_difference'])} ({pct(lo)} to {pct(hi)}) | "
                  f"{fp(c['fisher_p'])} | {fp(c['holm_p'])} |")
    md += ["", "## Stratified exact test (design cells as strata)", ""]
    for s in strat_out:
        md.append(f"- {s['contrast']}: {s['strata']} strata ({s['informative_strata']} "
                  f"informative), D0 duplicates {s['observed_D0_duplicates']}, "
                  f"exact p = {fp(s['exact_p'])}")
    md += ["", "## Flush latency median (distribution-free 95% interval)", ""]
    for m in medians:
        md.append(f"- {m['batch']}: n = {m['n']}, median {m['median_ms']:.2f} ms, "
                  f"95% CI {m['ci95_ms'][0]:.2f}–{m['ci95_ms'][1]:.2f} ms "
                  f"(exact coverage {100 * m['coverage']:.1f}%)")
    md += ["", "## Notes", "",
           "- Experiments 1, 2 and the scripted batch of Experiment 3 are deterministic "
           "(identical outcomes across repetitions and replays), so no sampling interval is "
           "attached to their counts; their T4 counts were only re-checked here.",
           "- " + result["note"], ""]
    (outdir / "exp3_statistics.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
