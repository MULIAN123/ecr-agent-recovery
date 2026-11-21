#!/usr/bin/env python3
"""ECR paper data figures, version 3 (matplotlib). Copy of version 2 (make_paper_figures_v2.py,
kept unchanged) in which only the layout, font sizes and legend of the asynchronous-recovery
figure (fig_async_outcomes) were changed for print legibility (manuscript v10, 2026-10-14).
Inputs, data checks and every other figure are identical to version 2. Write into a new
output directory (figures_v3); never overwrite figures_v2.

Reads only the archived result files, records their SHA-256 in a manifest, checks every
aggregate it draws against the numbers printed in the manuscript tables (stops on any
mismatch), and draws each figure at its final printed size (no scaling in LaTeX), with the
paper's Latin Modern fonts taken from the local MiKTeX tree. Writes PDF and SVG.
Run with the clean environment <WORKSPACE>
Usage: python make_paper_figures_v3.py <experiments_root> <figures_out_dir>
"""
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle, Polygon
import numpy as np

ROOT, OUT = Path(sys.argv[1]), Path(sys.argv[2])
RUNS = ROOT / "environment-runs"
if OUT.name == "figures_v2":
    raise SystemExit("refusing to write into figures_v2; use a new output directory")
OUT.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------ style
LM = Path(r"<HOME>\AppData\Local\Programs\MiKTeX\fonts\opentype\public\lm")
for f in ("lmroman10-regular.otf", "lmroman10-bold.otf", "lmroman10-italic.otf", "lmroman10-bolditalic.otf", "lmmono10-regular.otf"):
    font_manager.fontManager.addfont(str(LM / f))
plt.rcParams.update({
    "font.family": "Latin Modern Roman", "font.size": 7, "axes.titlesize": 7, "axes.labelsize": 7,
    "xtick.labelsize": 6.5, "ytick.labelsize": 6.5, "legend.fontsize": 6.5,
    "mathtext.fontset": "custom", "mathtext.rm": "Latin Modern Roman", "mathtext.it": "Latin Modern Roman:italic",
    "mathtext.bf": "Latin Modern Roman:bold", "axes.linewidth": 0.5, "xtick.major.width": 0.5, "ytick.major.width": 0.5,
    "xtick.major.size": 2, "ytick.major.size": 2, "hatch.linewidth": 0.45, "svg.fonttype": "path", "pdf.fonttype": 42,
    "savefig.dpi": 600})
COL, TEXT = 3.45, 7.14          # \columnwidth and \textwidth of the manuscript, inches
INK, MUTED, HAIR = "#0B0B0B", "#52514E", "#C3C2B7"
GRAY, LIGHT = "#B9B8B0", "#E8E7E2"
RED, BLUE, YELLOW, VIOLET, TEAL = "#E34948", "#2A78D6", "#EDA100", "#4A3AA7", "#1BAF7A"
MONO = {"family": "Latin Modern Mono"}

MANIFEST = {"inputs": {}, "checks": []}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_csv(rel):
    path = RUNS / rel
    MANIFEST["inputs"][rel] = sha(path)
    return list(csv.DictReader(path.open(encoding="utf-8")))


def check(label, got, want):
    if got != want:
        raise SystemExit(f"MISMATCH {label}: data {got} != manuscript {want}")
    MANIFEST["checks"].append({"check": label, "value": got})


def save(fig, name):
    for ext in ("pdf", "svg"):
        fig.savefig(OUT / f"{name}.{ext}", bbox_inches=None, pad_inches=0)
    plt.close(fig)
    print("wrote", name)


def cell(ax, x, y, w, h, fill=None, letter="", lc="white", hatch=None, outline=False, corner=False, lw=0.35, letter_size=6.0):
    if fill is None and not hatch:
        ax.add_patch(Rectangle((x + 0.06 * w, y + 0.08 * h), 0.88 * w, 0.84 * h, fill=False, ec=HAIR, lw=lw))
    else:
        ax.add_patch(Rectangle((x + 0.04 * w, y + 0.06 * h), 0.92 * w, 0.88 * h, fc=fill or "white",
                               ec=(HAIR if hatch else "none"), lw=lw, hatch=hatch))
    if letter:
        ax.text(x + w / 2, y + h / 2, letter, ha="center", va="center", color=lc, fontsize=letter_size, fontweight="bold", family="Latin Modern Roman")
    if corner:
        ax.add_patch(Polygon([(x + 0.96 * w, y + 0.94 * h), (x + 0.58 * w, y + 0.94 * h), (x + 0.96 * w, y + 0.52 * h)],
                             closed=True, fc=INK, ec="none"))


# ------------------------------------------------------------------ Experiment 1
ARMS1 = [("FULL", "Full"), ("A_ID_COMPARE", r"$-C_{id}$"), ("A_VERSION_COMPARE", r"$-C_{ver}$"), ("A_SEQUENCE", r"$-C_{seq}$"),
         ("A_RANK", r"$-C_{rank}$"), ("A_OUTCOME_BOUNDARY", r"$-C_{disp}$"), ("A_UNKNOWN_PRESERVATION", r"$-U$"),
         ("A_ACCEPTANCE_LEVEL", r"$-C_{lvl}$")]
MODE = {"ADAPTER_ACCEPTED_EVIDENCE": "FA", "QUEUE_STALE_AFTER_ADAPTER_EVIDENCE": "FA", "PENDING_EXECUTION": "FP",
        "OUTCOME_UNKNOWN": "FU", "CONFIRMED_NO_ACCEPTANCE_EVIDENCE": "FF", "QUEUE_BINDING_PREPARED_BUT_QUEUE_MISSING": "FQ"}
MODE_STYLE = {"FA": (RED, "A", "white", "////", "false acceptance"), "FP": (BLUE, "P", "white", "....", "false pending"),
              "FU": (YELLOW, "U", INK, "xxxx", "false unknown"), "FF": (VIOLET, "F", "white", "\\\\\\\\", "false definite failure"),
              "FQ": (VIOLET, "Q", "white", "\\\\\\\\", "queue-missing")}


def mode_of(v):
    if "[UNSUPPORTED" not in v:
        return "abstain" if v.endswith("/FAIL_CLOSED") else "ok"
    return MODE[v.split("/")[0]]


def targets(rows, arm):
    return [r for r in rows if r["challenge"] == arm or (arm in r["challenge"] and not r["challenge"].startswith("MASK"))]


def exp1():
    rows = load_csv("20261012-ablation-ext-r3/classification-matrix.csv")
    check("exp1 scenarios", len(rows), 47)
    table3 = {"A_ID_COMPARE": (6, {"FA": 4, "FU": 1, "FP": 1}), "A_VERSION_COMPARE": (5, {"FA": 3, "FU": 1, "FP": 1}),
              "A_SEQUENCE": (5, {"FA": 4, "FU": 1}), "A_RANK": (5, {"FA": 3, "FU": 1, "FP": 1}),
              "A_OUTCOME_BOUNDARY": (4, {"FA": 3, "FF": 1}), "A_UNKNOWN_PRESERVATION": (8, {"FP": 7, "FQ": 1}),
              "A_ACCEPTANCE_LEVEL": (4, {"FA": 4})}
    check("exp1 full faults", sum(mode_of(r["FULL"]) not in ("ok", "abstain") for r in rows), 0)
    summary = {}
    for arm, (n_t, modes) in table3.items():
        tg = targets(rows, arm)
        got = Counter(mode_of(r[arm]) for r in tg if mode_of(r[arm]) not in ("ok", "abstain"))
        check(f"table3 {arm} targeted", len(tg), n_t)
        check(f"table3 {arm} modes", dict(got), modes)
        off = Counter(mode_of(r[arm]) for r in rows if r not in tg and mode_of(r[arm]) not in ("ok", "abstain"))
        summary[arm] = (n_t, got, off)
    check("off-target faults", {a: dict(s[2]) for a, s in summary.items() if s[2]}, {"A_UNKNOWN_PRESERVATION": {"FP": 2, "FQ": 1}})
    controls = [r for r in rows if r["challenge"] == "CONTROL"]
    masks = [r for r in rows if r["challenge"].startswith("MASK")]
    for a, _ in ARMS1:
        check(f"controls correct {a}", sum(mode_of(r[a]) == "ok" for r in controls), 5)
        check(f"masking rejected {a}", sum(mode_of(r[a]) == "abstain" for r in masks), 4)

    # ---- main-text summary (single column)
    fig = plt.figure(figsize=(COL, 1.95))
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
    ax.set_xlim(0, 100); ax.set_ylim(0, 60); ax.axis("off")
    x0, unit, rh = 15.0, 4.6, 5.0        # bar origin, mm-like units per scenario, row height
    top = 47.5
    ax.text(x0, top + 6.2, "Targeted scenarios misclassified", fontsize=6.5, color=INK, va="bottom")
    ax.text(62.0, top + 6.2, "Off-target", fontsize=6.5, color=INK, va="bottom", ha="center")
    ax.text(77.0, top + 6.2, "Controls", fontsize=6.5, color=INK, va="bottom", ha="center")
    ax.text(91.0, top + 6.2, "Masking", fontsize=6.5, color=INK, va="bottom", ha="center")
    ax.text(77.0, top + 3.4, "correct", fontsize=6.0, color=MUTED, va="bottom", ha="center")
    ax.text(91.0, top + 3.4, "rejected", fontsize=6.0, color=MUTED, va="bottom", ha="center")
    ax.text(62.0, top + 3.4, "faults", fontsize=6.0, color=MUTED, va="bottom", ha="center")
    for i, (arm, label) in enumerate(ARMS1):
        y = top - i * rh
        ax.text(x0 - 1.2, y + rh / 2 - 0.5, label, ha="right", va="center", fontsize=7)
        if arm == "FULL":
            ax.text(x0, y + rh / 2 - 0.5, "none of 47 scenarios misclassified", va="center", fontsize=6.3, color=MUTED)
            off = "0"
        else:
            n_t, modes, offc = summary[arm]
            ax.add_patch(Rectangle((x0, y + 0.6), n_t * unit, rh - 2.2, fill=False, ec=MUTED, lw=0.45))
            xx = x0
            for m in ("FA", "FP", "FU", "FF", "FQ"):
                for _ in range(modes.get(m, 0)):
                    fc, letter, lc, hatch, _l = MODE_STYLE[m]
                    ax.add_patch(Rectangle((xx + 0.25, y + 0.85), unit - 0.5, rh - 2.7, fc=fc, ec="white", lw=0.4))
                    ax.text(xx + unit / 2, y + 0.85 + (rh - 2.7) / 2, letter, ha="center", va="center", color=lc, fontsize=6.0, fontweight="bold")
                    xx += unit
            ax.text(x0 + 8 * unit + 1.0, y + rh / 2 - 0.5, f"{sum(modes.values())}/{n_t}", va="center", fontsize=6.5)
            off = "+" + str(sum(offc.values())) if offc else "0"
        ax.text(62.0, y + rh / 2 - 0.5, off, ha="center", va="center", fontsize=6.5, color=(INK if off != "0" else MUTED))
        ax.text(77.0, y + rh / 2 - 0.5, "5/5", ha="center", va="center", fontsize=6.5, color=MUTED)
        ax.text(91.0, y + rh / 2 - 0.5, "4/4", ha="center", va="center", fontsize=6.5, color=MUTED)
    ax.plot([2, 98], [top - 0.3, top - 0.3], color=HAIR, lw=0.4)
    # legend
    for m, lx, ly in (("FA", 4.0, 5.2), ("FP", 37.0, 5.2), ("FU", 68.0, 5.2), ("FF", 4.0, 1.0), ("FQ", 37.0, 1.0)):
        fc, letter, lc, hatch, lab = MODE_STYLE[m]
        ax.add_patch(Rectangle((lx, ly), 4.0, 3.0, fc=fc, ec="none"))
        ax.text(lx + 2.0, ly + 1.5, letter, ha="center", va="center", color=lc, fontsize=6.0, fontweight="bold")
        ax.text(lx + 5.0, ly + 1.5, lab, va="center", fontsize=6.0)
    save(fig, "fig_ablation_summary")

    # ---- appendix: full matrix, scenarios as rows (single column, tall)
    order = [("Controls", lambda r: r["challenge"] == "CONTROL"), (r"$C_{id}$ targets", lambda r: r["challenge"] == "A_ID_COMPARE"),
             (r"$C_{ver}$ targets", lambda r: r["challenge"] == "A_VERSION_COMPARE"), (r"$C_{seq}$ targets", lambda r: r["challenge"] == "A_SEQUENCE"),
             (r"$C_{rank}$ targets", lambda r: r["challenge"] == "A_RANK"), (r"$C_{disp}$ targets", lambda r: r["challenge"] == "A_OUTCOME_BOUNDARY"),
             (r"$U$ targets", lambda r: r["challenge"] == "A_UNKNOWN_PRESERVATION"),
             (r"$U$ and $C_{lvl}$ target", lambda r: r["challenge"] == "A_UNKNOWN_PRESERVATION+A_ACCEPTANCE_LEVEL"),
             (r"$C_{lvl}$ targets", lambda r: r["challenge"] == "A_ACCEPTANCE_LEVEL"), ("Masking (two defects)", lambda r: r["challenge"].startswith("MASK")),
             ("Other invalid", lambda r: r["challenge"] == "R2_OTHER")]
    groups = [(g, [r for r in rows if f(r)]) for g, f in order]
    check("appendix matrix rows", sum(len(g[1]) for g in groups), 47)
    rh, hh, cw = 1.0, 1.15, 1.0
    n_rows = 47 + len(groups)
    H = n_rows * rh + 0.5 * len(groups) + 4.2
    fig = plt.figure(figsize=(COL, 0.115 * H))
    ax = fig.add_axes([0, 0, 1, 1])
    lab_w = 4.6
    cw = 1.22
    ax.set_xlim(0, lab_w + 8 * cw + 0.2); ax.set_ylim(0, H); ax.axis("off")
    y = H - 2.6
    for j, (arm, label) in enumerate(ARMS1):
        ax.text(lab_w + j * cw + cw / 2, y + 0.7, label, ha="center", va="bottom", fontsize=6.5)
    style = {"ok": (GRAY, "", ""), "abstain": (None, "", "")}
    for g_label, members in groups:
        y -= hh
        ax.text(0.05, y + 0.25, g_label, fontsize=6.2, color=INK, fontweight="bold", va="bottom")
        for r in members:
            y -= rh
            short = "_".join(r["scenario"].split("_")[:3]) if r["scenario"].startswith("E_") else r["scenario"].split("_")[0]
            ax.text(0.35, y + rh / 2, short.replace("_", r"\_") if False else short, fontsize=6.0, va="center", color=MUTED, **MONO)
            for j, (arm, _l) in enumerate(ARMS1):
                m = mode_of(r[arm])
                if m in style:
                    fill, letter, lc = style[m]
                    cell(ax, lab_w + j * cw, y, cw, rh, fill=fill)
                else:
                    fc, letter, lc, hatch, _l2 = MODE_STYLE[m]
                    cell(ax, lab_w + j * cw, y, cw, rh, fill=fc, letter=letter, lc=lc)
        y -= 0.5
    save(fig, "fig_ablation_matrix_full")
    return summary


# ------------------------------------------------------------------ Experiment 2
ARMS2 = [("EBR", "EBR"), ("EBRQ", "EBRQ"), ("CONV", "CONV"), ("STOP", "STOP"), ("RETRY", "RETRY"),
         ("EBRQ_NO_UNKNOWN", r"EBRQ$-U$"), ("EBRQ_NO_BOUNDARY", r"EBRQ$-C_{disp}$"), ("EBRQ_NO_LEVEL", r"EBRQ$-C_{lvl}$")]
GROUPS2 = [("B1", "B1_"), ("B2 request lost", "B2_DROP_"), ("B2 arrives late", "B2_LATE_"), ("B2 arrives early", "B2_ARRIVE_"),
           ("B3 commits late", "B3_LATE_"), ("B4", "B4_"), ("B5", "B5_"), ("B6", "B6_"), ("F2 foreign acceptance", "F2_"),
           ("F3 weak ack.", "F3_"), ("F4 stale sequence", "F4_"), ("F5 superseded version", "F5_"), ("F6 lost dispatch", "F6_")]
KIND2 = {"done": (GRAY, "", "", None), "unresolved": (None, "", "", None), "missed": ("white", "", "", "/////"),
         "invalid": (YELLOW, "A", INK, None), "false": (BLUE, "F", "white", None), "dup": (RED, "D", "white", None),
         "unauth": (VIOLET, "U", "white", None)}


def kind2(code):
    head = code.split(" ")[0]
    for prefix, k in (("DUP", "dup"), ("UNAUTH", "unauth"), ("FALSE_DONE", "false"), ("UNSAFE_AUTH1", "invalid")):
        if head.startswith(prefix):
            return k
    if head == "UNRESOLVED(missed)":
        return "missed"
    if head == "UNRESOLVED":
        return "unresolved"
    if head.startswith("DONE"):
        return "done"
    raise SystemExit("unknown outcome code " + code)


def exp2():
    rows = load_csv("20261012-async-sim-r1/outcome-matrix.csv")
    by = {r["scenario"]: r for r in rows}
    check("exp2 scenarios", len(rows), 52)
    table4 = {"EBR": (0, 0, 0, 0, 8, 44), "EBRQ": (0, 0, 0, 0, 29, 23), "CONV": (1, 5, 8, 5, 32, 6), "STOP": (0, 4, 0, 4, 4, 44),
              "RETRY": (12, 16, 8, 32, 11, 0), "EBRQ_NO_UNKNOWN": (10, 10, 0, 28, 8, 16), "EBRQ_NO_BOUNDARY": (2, 2, 0, 4, 29, 19),
              "EBRQ_NO_LEVEL": (0, 0, 4, 0, 26, 22)}
    stats = {}
    for a, _ in ARMS2:
        k = [kind2(r[a]) for r in rows]
        opp = sum(kind2(r[a]) == "done" and r["safe_opportunity"] == "True" for r in rows)
        got = (k.count("dup"), k.count("dup") + k.count("unauth"), k.count("false"),
               k.count("dup") + k.count("unauth") + k.count("invalid"), opp, k.count("unresolved") + k.count("missed"))
        check(f"table4 {a}", got, table4[a])
        stats[a] = (Counter(k), opp)
    check("CONV scenarios with a safety event", sum(stats["CONV"][0][c] for c in ("dup", "unauth", "false", "invalid")), 13)
    check("total safe opportunities", sum(r["safe_opportunity"] == "True" for r in rows), 38)

    # v3 layout: larger cells and text (>= 6.5 pt), three-row legend; geometry in ~mm units, x and y equal scale.
    lab_w, sub, gap = 27.0, 4.4, 2.0
    block = 4 * sub
    W = lab_w + 8 * block + 7 * gap + 1.0
    unit_in = TEXT / W                     # inches per unit, used for both axes
    xc = [lab_w + j * (block + gap) + block / 2 for j in range(8)]
    n = len(GROUPS2)
    rh = 3.9
    leg_h = 3 * 4.6 + 1.0
    Hb = n * rh + 0.8 + 4.0 + leg_h + 1.0
    Ha = 50.0
    Hn = 5.0
    H_in = (Ha + Hn + Hb) * unit_in
    fig = plt.figure(figsize=(TEXT, H_in))
    fb, fn = Hb * unit_in / H_in, Hn * unit_in / H_in
    # (a) top strips, aligned with the matrix columns
    axa = fig.add_axes([0, fb + fn, 1, 1 - fb - fn])
    axa.set_xlim(0, W); axa.set_ylim(-1, Ha - 1); axa.axis("off")
    base_c, h_c = 27.0, 17.0      # completed strip: 0..38 mapped to h_c
    base_s, h_s = 2.0, 17.0       # safety strip: 0..40 mapped to h_s
    axa.text(lab_w - 1.5, base_c + h_c / 2, "Safe opportunities\ncompleted (of 38)", ha="right", va="center", fontsize=7)
    axa.text(lab_w - 1.5, base_s + h_s / 2, "Scenarios with a\nsafety event (of 52)", ha="right", va="center", fontsize=7)
    for base, h in ((base_c, h_c), (base_s, h_s)):
        axa.plot([lab_w - 0.5, W - 0.5], [base, base], color=HAIR, lw=0.5)
    axa.plot([lab_w - 0.5, W - 0.5], [base_c + h_c, base_c + h_c], color=HAIR, lw=0.4, ls=(0, (2, 2)))
    axa.text(lab_w + 0.2, base_c + h_c + 0.3, "38", fontsize=6.5, color=MUTED, va="bottom")
    for j, (a, _l) in enumerate(ARMS2):
        cnt, opp = stats[a]
        bw = block * 0.46
        axa.add_patch(Rectangle((xc[j] - bw / 2, base_c), bw, h_c * opp / 38, fc=GRAY, ec="none"))
        axa.text(xc[j], base_c + h_c * opp / 38 + 0.6, str(opp), ha="center", va="bottom", fontsize=7)
        yb = base_s
        total = 0
        for k_ in ("dup", "unauth", "false", "invalid"):
            v = cnt[k_]
            if v:
                fc, letter, lc, _h = KIND2[k_]
                axa.add_patch(Rectangle((xc[j] - bw / 2, yb), bw, h_s * v / 40, fc=fc, ec="white", lw=0.4))
                if h_s * v / 40 > 2.4:
                    axa.text(xc[j], yb + h_s * v / 80, letter, ha="center", va="center", color=lc, fontsize=6.5, fontweight="bold")
                yb += h_s * v / 40
                total += v
        axa.text(xc[j], yb + 0.6, str(total), ha="center", va="bottom", fontsize=7, color=(INK if total else MUTED))
    # arm names between the strips and the matrix
    axn = fig.add_axes([0, fb, 1, fn]); axn.set_xlim(0, W); axn.set_ylim(0, 1); axn.axis("off")
    for j, (a, label) in enumerate(ARMS2):
        axn.text(xc[j], 0.45, label, ha="center", va="center", fontsize=7.5, fontweight=("bold" if a == "EBRQ" else "normal"))
    # (b) matrix: rows = crash variant / defect, sub-columns = contracts P0..P3
    axb = fig.add_axes([0, 0.0, 1, fb])
    axb.set_xlim(0, W); axb.set_ylim(0, Hb); axb.axis("off")
    for i, (g_label, prefix) in enumerate(GROUPS2):
        y = Hb - 0.5 - (i + 1) * rh - (0.8 if i >= 8 else 0)
        axb.text(lab_w - 1.5, y + rh / 2, g_label, ha="right", va="center", fontsize=7)
        scen = [r for r in rows if r["scenario"].startswith(prefix)]
        check(f"exp2 group {prefix} contracts", [s["scenario"][-2:] for s in scen], ["P0", "P1", "P2", "P3"])
        for j, (a, _l) in enumerate(ARMS2):
            for p, s in enumerate(scen):
                k_ = kind2(s[a])
                fc, letter, lc, hatch = KIND2[k_]
                cell(axb, lab_w + j * (block + gap) + p * sub, y, sub, rh, fill=fc, letter=letter, lc=lc, hatch=hatch,
                     corner=k_ in ("dup", "unauth", "invalid"), letter_size=7.0)
    yl = Hb - 0.5 - n * rh - 0.8
    for j in range(8):
        for p in range(4):
            axb.text(lab_w + j * (block + gap) + p * sub + sub / 2, yl - 1.6, f"P{p}", ha="center", va="center", fontsize=6.5, color=MUTED)
    axb.plot([lab_w - 0.5, W - 0.5], [Hb - 0.5 - 8 * rh - 0.4] * 2, color=HAIR, lw=0.5)
    axb.text(lab_w - 1.5, yl - 1.6, "contract", ha="right", va="center", fontsize=6.5, color=MUTED)
    # legend: three rows, 7 pt
    rows_leg = [[("done", "completed"), ("unresolved", "unresolved, no safe opportunity"), ("missed", "unresolved, safe opportunity missed")],
                [("dup", "duplicate effect"), ("unauth", "effect for a superseded task version"), ("false", "false completion")],
                [("invalid", "contract-invalid authorization, no extra effect"), ("corner", "corner mark: the cell includes a contract-invalid authorization")]]
    for row_i, row_items in enumerate(rows_leg):
        lx, ly = lab_w, 0.6 + (2 - row_i) * 4.6
        for k_, text in row_items:
            if k_ == "corner":
                axb.add_patch(Rectangle((lx + 0.04 * sub, ly + 0.06 * rh), 0.92 * sub, 0.88 * rh, fill=False, ec=HAIR, lw=0.35))
                axb.add_patch(Polygon([(lx + 0.96 * sub, ly + 0.94 * rh), (lx + 0.58 * sub, ly + 0.94 * rh), (lx + 0.96 * sub, ly + 0.52 * rh)],
                                      closed=True, fc=INK, ec="none"))
            else:
                fc, letter, lc, hatch = KIND2[k_]
                cell(axb, lx, ly, sub, rh, fill=fc, letter=letter, lc=lc, hatch=hatch,
                     corner=k_ in ("dup", "unauth", "invalid"), letter_size=7.0)
            axb.text(lx + sub + 1.0, ly + rh / 2, text, va="center", fontsize=7)
            lx += sub + 4.0 + 1.13 * len(text)
    save(fig, "fig_async_outcomes")
    return stats


# ------------------------------------------------------------------ Experiment 3
KILLS = ["pre", "0", "100", "150", "250", "400", "1500"]


def exp3():
    scripted = load_csv("20261013-dsh-r1/per-trial-scores.csv")
    real_all = load_csv("20261013-dsh-r1r/per-trial-scores.csv")
    real = [r for r in real_all if not r["trial_id"].startswith("dsh-r1r-228-")]
    check("scripted trials", len(scripted), 252)
    check("real crash trials", len(real), 234)
    # Table 5
    t5 = {("D0", "obedient"): (18, 18, 0, 24), ("D0", "naive"): (24, 12, 0, 24), ("D1", "obedient"): (0, 36, 6, 0),
          ("D1", "naive"): (24, 12, 0, 0), ("D2", "obedient"): (0, 36, 6, 0), ("D2", "naive"): (0, 36, 6, 0)}
    lost = ("CALL_NOT_DURABLE", "REQUEST_NOT_DURABLE")
    for (a, p), want in t5.items():
        sel = [r for r in scripted if r["arm"] == a and r["policy"] == p]
        post = [r for r in sel if r["kill"] != "pre"]
        pre = [r for r in sel if r["kill"] == "pre"]
        got = (sum(r["duplicate"] == "True" for r in post), sum(r["completed"] == "True" for r in post),
               sum(r["missed_completion"] == "True" for r in pre), sum(r["durable_state_at_kill"] in lost for r in sel))
        check(f"table5 {a} {p}", got, want)
    # Table 6
    t6 = {("D0", "warn"): (33, 18, 0, 24, 0), ("D0", "plain"): (33, 18, 1, 23, 0), ("D1", "warn"): (33, 0, 6, 0, 0),
          ("D1", "plain"): (33, 0, 6, 0, 0), ("D2", "warn"): (34, 0, 6, 0, 0), ("D2", "plain"): (32, 0, 6, 2, 2)}
    for (a, d), want in t6.items():
        sel = [r for r in real if r["arm"] == a and r["desc"] == d]
        post = [r for r in sel if r["kill"] != "pre"]
        pre = [r for r in sel if r["kill"] == "pre"]
        got = (len(post), sum(r["duplicate"] == "True" for r in post), sum(r["missed_completion"] == "True" for r in pre),
               sum(int(r["model_resend_attempts"]) > 0 for r in sel), sum(int(r["guard_denials"]) > 0 for r in sel))
        check(f"table6 {a} {d}", got, want)
    # Table 7
    def state(r):
        if r["durable_state_at_kill"] in lost:
            return "lost"
        return "unknown" if "TOOL_OUTCOME_UNKNOWN" in r["dsh_recovery_codes"] else "durable"
    t7 = {("lost", "warn"): (24, 24), ("lost", "plain"): (23, 24), ("unknown", "warn"): (0, 66), ("unknown", "plain"): (2, 65),
          ("durable", "warn"): (0, 28), ("durable", "plain"): (0, 27)}
    for (s, d), want in t7.items():
        sel = [r for r in real if state(r) == s and r["desc"] == d]
        check(f"table7 {s} {d}", (sum(int(r["model_resend_attempts"]) > 0 for r in sel), len(sel)), want)

    # ---- Fig. 6: discrete k/n table-heatmap (single column)
    conds = [("Scripted, obedient", [r for r in scripted if r["policy"] == "obedient"]),
             ("Scripted, naive", [r for r in scripted if r["policy"] == "naive"]),
             ("Real model", real)]
    arms = [("D0", "D0 unmodified"), ("D1", "D1 durable dispatch"), ("D2", "D2 evidence guard")]
    lab_w, cw, rh, hh = 26.0, 9.6, 4.6, 4.0
    W = lab_w + 7 * cw + 1.0
    H = 3 * hh + 9 * rh + 18.0
    fig = plt.figure(figsize=(COL, COL * H / W))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list("dup", ["#FBE3E2", RED, "#9E1F1E"])
    ytop = H - 9.5
    for k, kname in enumerate(KILLS):
        ax.text(lab_w + k * cw + cw / 2, ytop + 1.4, kname, ha="center", va="bottom", fontsize=6.5)
    ax.text(lab_w + 3.5 * cw, ytop + 5.2, "Kill time after the effect (ms)", ha="center", va="bottom", fontsize=6.5, color=INK)
    # measured D0 record loss at 0-150 ms
    ax.plot([lab_w + 1 * cw + 0.6, lab_w + 4 * cw - 0.6], [ytop + 0.7] * 2, color=MUTED, lw=0.7)
    y = ytop
    for c, (cname, rows) in enumerate(conds):
        y -= hh
        ax.text(0.5, y + 1.0, cname, ha="left", va="bottom", fontsize=6.5, fontweight="bold")
        for a, aname in arms:
            y -= rh
            ax.text(lab_w - 1.0, y + rh / 2, aname, ha="right", va="center", fontsize=6.3)
            for k, kname in enumerate(KILLS):
                sel = [r for r in rows if r["arm"] == a and r["kill"] == kname]
                kdup = sum(r["duplicate"] == "True" for r in sel)
                n = len(sel)
                rate = kdup / n
                x = lab_w + k * cw
                if kdup:
                    ax.add_patch(Rectangle((x + 0.3, y + 0.3), cw - 0.6, rh - 0.6, fc=cmap(rate), ec="none"))
                else:
                    ax.add_patch(Rectangle((x + 0.3, y + 0.3), cw - 0.6, rh - 0.6, fc="white", ec=HAIR, lw=0.35))
                ax.text(x + cw / 2, y + rh / 2, f"{kdup}/{n}", ha="center", va="center", fontsize=6.0,
                        color=("white" if rate >= 0.6 else (INK if kdup else MUTED)), fontweight=("bold" if kdup else "normal"))
    ax.text(W - 0.5, 3.6, "Cells: trials with a duplicate / crash trials. Bar over 0–150 ms: D0 had not written",
            ha="right", va="bottom", fontsize=6.0, color=MUTED)
    ax.text(W - 0.5, 0.8, "the tool/call record to disk in any trial.", ha="right", va="bottom", fontsize=6.0, color=MUTED)
    save(fig, "fig_runtime_duplicates_v2")

    # ---- Fig. 7: ECDF with median and p95 (single column)
    def lat(rows):
        return np.array(sorted(x / 1000.0 for r in rows for x in json.loads(r["flush_latency_us"]) if x is not None))
    s_lat, r_lat = lat(scripted), lat(real_all)
    check("scripted flush n", len(s_lat), 228)
    check("real flush n", len(r_lat), 159)

    def p95(v):  # scorer's rule: sorted[int(0.95*(n-1))]
        return v[int(0.95 * (len(v) - 1))]
    stats = {"scripted": (float(np.median(s_lat)), float(p95(s_lat)), float(s_lat.max())),
             "real": (float(np.median(r_lat)), float(p95(r_lat)), float(r_lat.max()))}
    check("scripted flush median/p95/max ms", tuple(round(v, 3) for v in stats["scripted"]), (1.283, 3.192, 19.775))
    check("real flush median/p95/max ms", tuple(round(v, 3) for v in stats["real"]), (3.04, 16.698, 70.485))
    fig, ax = plt.subplots(figsize=(COL, 1.85))
    fig.subplots_adjust(left=0.13, right=0.97, bottom=0.21, top=0.97)
    for v, colr, name, key in ((s_lat, BLUE, "scripted model", "scripted"), (r_lat, RED, "real model", "real")):
        y = np.arange(1, len(v) + 1) / len(v)
        ax.step(v, y, where="post", color=colr, lw=1.0)
        med, q95, mx = stats[key]
        for q, mk in ((med, "o"), (q95, "D")):
            yy = np.searchsorted(v, q, side="right") / len(v)
            ax.plot([q], [yy], marker=mk, ms=3.2, mfc="white", mec=colr, mew=0.9, zorder=3)
        ax.plot([], [], color=colr, lw=1.0, label=f"{name}, $n={len(v)}$\nmedian {med:.2f} ms, p95 {q95:.2f} ms")
    ax.plot([], [], ls="none", marker="o", ms=3.2, mfc="white", mec=MUTED, label="median")
    ax.plot([], [], ls="none", marker="D", ms=3.2, mfc="white", mec=MUTED, label="p95")
    ax.set_xscale("log"); ax.set_xlim(0.5, 100); ax.set_ylim(0, 1.02)
    ax.set_xticks([1, 3, 10, 30, 100]); ax.set_xticklabels(["1", "3", "10", "30", "100"])
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0]); ax.set_yticklabels(["0", ".25", ".50", ".75", "1"])
    ax.set_xlabel("Awaited sessions.flush before dispatch (ms, log scale)")
    ax.set_ylabel("Share of dispatches")
    ax.grid(True, which="major", color=LIGHT, lw=0.5); ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelcolor=INK)
    leg = ax.legend(loc="center right", bbox_to_anchor=(1.0, 0.40), frameon=False, handlelength=1.4, borderaxespad=0.2, labelspacing=0.45)
    save(fig, "fig_flush_latency_v2")
    return stats


if __name__ == "__main__":
    exp1()
    exp2()
    s = exp3()
    MANIFEST["flush_stats_ms"] = s
    (OUT / "figure-data-manifest.json").write_text(json.dumps(MANIFEST, indent=2), encoding="utf-8")
    print("checks passed:", len(MANIFEST["checks"]))
