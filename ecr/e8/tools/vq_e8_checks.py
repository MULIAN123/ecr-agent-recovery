"""VQ-E8 qualification checks (protocol r1/r2 section 6 step 3; freeze candidate gates VQ-1 .. VQ-12).
Runs on the dedicated guest AFTER the two qualification batches. Read-only on batch evidence; writes only its own
report (O_EXCL) and, for `unittests`, a scratch directory it creates.

Subcommands
  unittests --pkg DIR --scratch DIR --out R.json
      VQ-1/VQ-2. Three trees built from the package copy: c0 (src/), c1 (src_c1/), e1off (src/ with endpoint.py
      replaced by src_e1/endpoint_fi.py, no "fi" key = all faults off). Runs tests/test_bl6_v3.py,
      tests/test_authority.py, tests/test_single_entry.py in each with no ECR_TEST_* variable. PASS iff every test
      passes in c0 and the per-test outcome table of c1 and of e1off equals c0's.
  batch --q1 DIR --q2 DIR --pkg DIR --out R.json [--persist-semantics R.json --persist-verify R.json]
      VQ-3 .. VQ-12 over the attempt results of both qualification batches (see GATES below).
  Exit codes of every report subcommand: 0 = PASS, 3 = PASS_W6PRIME_UNTESTED (batch only), 1 = FAIL / error.
  kernel --out R.json           G-K-kernel: journalctl -k -b for BUG/Oops/WARNING/trap/segfault/general protection
  space --path P --min-free-gib G --min-free-inodes N --out R.json    G-S
  authorize --report R.json --unittests U.json --kernel K.json --space S.json --finalized-q1 F1.json --finalized-q2 F2.json
            --manifest-sha HEX --approved-by-user [--accept-w6prime-untested]
      Writes qualification-authorization-e8.json (status PASS_HOST_AUDIT) next to the package ONLY if every report
      is PASS and the flag is given. The flag records that the user reviewed the reports; the script never infers it.
      A batch report PASS_W6PRIME_UNTESTED (only VQ-6 failed) additionally needs --accept-w6prime-untested, and the
      authorization then records the 6 W6' units as UNTESTED.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

GATES = {
    "VQ-3": "every VQ attempt in q1 and q2 is OBSERVED_PENDING_REPLAY and each batch has a CONSISTENT same-runtime replay",
    "VQ-4": "real process exits: each recovery's process_outcome is EXPECTED_HOOK / EXPECTED_INJECTED_EXCEPTION / CLEAN "
            "(UNDECLARED only for G6); every planned hook gave exit 137; e8_diag hook_integrity true",
    "VQ-5": "E1 over real HTTP: each fi config produced exactly its receipt kind once; no endpoint error-*.json; "
            "endpoint exit 0; no unconfigured fi receipt in E0 or E1-off units",
    "VQ-6": "W6': held marker seen, release-late written, held send logged, in this order, each <= 30 s; late-held and "
            "late-released receipts present; endpoint stopped without residue kill; w6_order answer is not UNDETERMINED; "
            "q1 and q2 give the same step sequence and the same answer (the answer itself is not prescribed)",
    "VQ-7": "W6/W7 sync: every required wait logged endpoint_send_logged=true",
    "VQ-8": "CSR: W9s and W9f detected by source (a) AND source (c); audit snapshot copy SHA-256 = source SHA-256 for "
            "every snapshot; snapshots are outside the SUT store",
    "VQ-9": "final.json: last recovery's final.json kept its name, earlier ones archived as final.R{k}.json; when the last "
            "recovery is CLEAN, final.json task.deliveryState equals task.json deliveryState",
    "VQ-10": "scorer isolation: scorer_uid = ecr-scorer and no non-expected primary scorer fault (includes the SUT "
             "ledger-access probe); active probe: ecr-sut cannot open a VQ ledger copy or the ledger root",
    "VQ-11": "EQ: the three EQ units (C0+E0, C1+E0, C0+E1-off) are equal after the frozen normalisation",
    "VQ-12": "persistence: protocol.durable_json semantics OK and every q1/q2 evidence file unchanged after a guest reboot",
    "VQ-D": "q1 vs q2: scorer_v2 fields and e8_diag fields except path equal per VQ unit (reported; a difference is a "
            "finding that must be explained, never resolved by choosing one run)",
}
FI_KIND = {"fence_fault": lambda v: "fence_" + v, "send_fault": lambda v: "send_" + v,
           "late_release_first_recovery_send": lambda v: "late_hold"}
TESTS = ("test_bl6_v3.py", "test_authority.py", "test_single_entry.py")
DRIVER = r"""
import json, sys, unittest
sys.path.insert(0, sys.argv[1])
out = {}
class R(unittest.TextTestResult):
    def addSuccess(self, t): super().addSuccess(t); out[t.id()] = "pass"
    def addFailure(self, t, e): super().addFailure(t, e); out[t.id()] = "fail"
    def addError(self, t, e): super().addError(t, e); out[t.id() if hasattr(t, "id") else str(t)] = "error"
    def addSkip(self, t, r): super().addSkip(t, r); out[t.id()] = "skip"
suite = unittest.defaultTestLoader.loadTestsFromName(sys.argv[2])
unittest.TextTestRunner(stream=sys.stderr, resultclass=R, verbosity=1).run(suite)
print(json.dumps(out, sort_keys=True))
"""


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def write_report(path: str, rep: dict) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(rep, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: v for k, v in rep.items() if k in ("status", "gates", "failed")}, indent=1, default=str))


# ----------------------------------------------------------------------------------------------- unittests
def build_tree(pkg: Path, root: Path, variant: str) -> Path:
    tree = root / variant
    shutil.copytree(pkg / ("src_c1" if variant == "c1" else "src"), tree / "src")
    shutil.copytree(pkg / "tests", tree / "tests")
    if variant == "e1off":
        shutil.copyfile(pkg / "src_e1/endpoint_fi.py", tree / "src/endpoint.py")
    return tree


def unittests(pkg: Path, scratch: Path) -> dict:
    pkg, scratch = pkg.resolve(), scratch.resolve()  # the driver runs with cwd = <tree>/tests
    scratch.mkdir(mode=0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith("ECR_TEST_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    table, logs = {}, {}
    for variant in ("c0", "c1", "e1off"):
        tree = build_tree(pkg, scratch, variant)
        for test in TESTS:
            p = subprocess.run([sys.executable, "-I", "-c", DRIVER, str(tree / "tests"), test[:-3]], cwd=tree / "tests",
                               env=env, capture_output=True, text=True, timeout=1800)
            try:
                table[(variant, test)] = json.loads(p.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                table[(variant, test)] = {"<driver>": "error"}
            logs[f"{variant}/{test}"] = {"exit": p.returncode, "stderr_tail": p.stderr[-3000:]}
    rows = {f"{v}/{t}": {"n": len(r), "pass": sum(x == "pass" for x in r.values()),
                         "not_pass": sorted(k for k, x in r.items() if x != "pass")} for (v, t), r in table.items()}
    c0_all_pass = all(not rows[f"c0/{t}"]["not_pass"] and rows[f"c0/{t}"]["n"] > 0 for t in TESTS)
    same = {v: all(table[(v, t)] == table[("c0", t)] for t in TESTS) for v in ("c1", "e1off")}
    ok = c0_all_pass and all(same.values())
    return {"status": "PASS" if ok else "FAIL", "gates": {"VQ-1": c0_all_pass and same["c1"], "VQ-2": c0_all_pass and same["e1off"]},
            "c0_all_pass": c0_all_pass, "equal_to_c0": same, "rows": rows, "logs": logs,
            "trees_sha256": {v: {str(p.relative_to(scratch / v)): sha(p) for p in sorted((scratch / v / "src").rglob("*.py"))}
                             for v in ("c0", "c1", "e1off")}}


# ----------------------------------------------------------------------------------------------- batch
def attempts(batch: Path) -> dict:
    out = {}
    for d in sorted((batch / "attempts").iterdir()):
        r = d / "attempt-result.json"
        if r.is_file():
            res = jl(r)
            out[res.get("unit_id")] = (d, res)
    return out


def norm_eq(folder: Path, result: dict):
    """The frozen local normalisation (tools/local_qualification.py norm): drop clocks, grant ids, indices, pids;
    32-hex ids -> UUID; unit names -> NAME."""
    drop = {"utc_ns", "grant_id", "grants", "index", "pid"}

    def clean(x):
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items() if k not in drop}
        if isinstance(x, list):
            return [clean(v) for v in x]
        if isinstance(x, str):
            return re.sub(r"\b[0-9a-f]{32}\b", "UUID", re.sub(r"E8Q?-\d{2,4}", "NAME", x))
        return x
    view = folder / "diag"
    acts = [jl(p) for p in sorted((view / "sut/store/recovery").glob("*.json"))]
    log = [{k: v for k, v in jl(p).items() if k != "index"} for p in sorted((view / "endpoint_events").glob("log-*.json"))]
    sc = {k: v for k, v in result["primary_score"].items() if k not in ("run_id", "scorer_pid", "scorer_uid", "arm")}
    return clean(sc), clean(acts), clean(log)


def ledger_probe(path: Path) -> dict:
    code = "import sys\ntry:\n open(sys.argv[1],'rb').read(1)\n print('READABLE')\nexcept PermissionError:\n print('DENIED')\n"
    out = {}
    for target in (path, path.parent):
        p = subprocess.run(["sudo", "-n", "-u", "ecr-sut", sys.executable, "-I", "-c",
                            code if target == path else code.replace("open(sys.argv[1],'rb').read(1)", "__import__('os').listdir(sys.argv[1])"),
                            str(target)], capture_output=True, text=True, timeout=30)
        out[str(target)] = p.stdout.strip() or ("ERROR " + p.stderr[-200:])
    return out


def batch_checks(q1: Path, q2: Path, pkg: Path, persist_sem, persist_ver) -> dict:
    cells = {u["unit_id"]: u for u in jl(pkg / "private/cells_e8.json")["qualification_units"]}
    runs = {"q1": attempts(q1), "q2": attempts(q2)}
    fail = {g: [] for g in GATES}
    import pwd
    scorer_uid = pwd.getpwnam("ecr-scorer").pw_uid
    for run, batch in (("q1", q1), ("q2", q2)):
        missing = sorted(set(cells) - set(runs[run]))
        if missing:
            fail["VQ-3"].append(f"{run}: units without attempt result {missing}")
        reps = sorted(batch.glob("replay-same-runtime-*/report.json"))
        if not reps or jl(reps[-1]).get("status") != "CONSISTENT":
            fail["VQ-3"].append(f"{run}: no CONSISTENT same-runtime replay")
        for uid, (folder, res) in runs[run].items():
            cell, tag = cells.get(uid), f"{run}:{cells.get(uid, {}).get('name', uid)}"
            if cell is None:
                fail["VQ-3"].append(f"{tag}: not a VQ unit"); continue
            if res.get("state") != "OBSERVED_PENDING_REPLAY":
                fail["VQ-3"].append(f"{tag}: state {res.get('state')} {res.get('state_reasons')}"); continue
            recs, diag = res["e8"]["recoveries"], res["e8_diag"]
            allowed = {"EXPECTED_HOOK", "EXPECTED_INJECTED_EXCEPTION", "CLEAN"} | ({"UNDECLARED"} if cell["group"] == "G6" else set())
            for plan, r in zip(cell["recoveries"], recs):
                if r["process_outcome"] not in allowed or (plan["crash_at"] and r["exit_code"] != 137):
                    fail["VQ-4"].append(f"{tag}: R{r['index']} {r['process_outcome']} exit {r['exit_code']} exc {r['exception_type']}")
            if len(recs) != len(cell["recoveries"]) or not diag["hook_integrity"]:
                fail["VQ-4"].append(f"{tag}: hook_integrity false or recovery count differs")
            ev = folder / "diag/endpoint_events"
            kinds = sorted(jl(p)["kind"] for p in ev.glob("fi-*.json"))
            want = sorted(FI_KIND[k](v) for k, v in (cell.get("fi") or {}).items() if v)
            if kinds != want:
                fail["VQ-5"].append(f"{tag}: fi receipts {kinds} != configured {want}")
            if diag["endpoint_error_files"] or any("endpoint error" in e for e in res["infra_events"]):
                fail["VQ-5"].append(f"{tag}: endpoint error {diag['endpoint_error_files']} {res['infra_events']}")
            if res.get("cleanup", {}).get("endpoint_residue_killed") is not None or res.get("cleanup", {}).get("endpoint_exit_status") not in (None, 0):
                fail["VQ-5"].append(f"{tag}: endpoint cleanup {res.get('cleanup')}")
            if cell["window"] == "W6'":
                steps = [s["step"] for s in res["e8"]["w6_order_receipts"]]
                oks = all(s.get("ok", True) for s in res["e8"]["w6_order_receipts"])
                ts = [s["utc_ns"] for s in res["e8"]["w6_order_receipts"]]
                if steps != ["held_marker_seen", "release_late_written", "held_send_logged"] or not oks or ts != sorted(ts):
                    fail["VQ-6"].append(f"{tag}: receipts {res['e8']['w6_order_receipts']}")
                if not list(ev.glob("late-held-*.json")) or not list(ev.glob("late-released-*.json")):
                    fail["VQ-6"].append(f"{tag}: late-held/late-released receipt missing")
                if (res.get("w6_order") or {}).get("answer") in (None, "UNDETERMINED"):
                    fail["VQ-6"].append(f"{tag}: w6_order answer {(res.get('w6_order') or {}).get('answer')}")
            for s in res["e8"]["sync"]:
                if not s["endpoint_send_logged"]:
                    fail["VQ-7"].append(f"{tag}: sync {s}")
            need_sync = [p["index"] for p in cell["recoveries"][:-1] if p["crash_at"] in ("after_request", "after_response")]
            if cell["window"] != "W6'" and len(res["e8"]["sync"]) != len(need_sync):
                fail["VQ-7"].append(f"{tag}: {len(res['e8']['sync'])} sync receipts for {len(need_sync)} required")
            src = diag["csr_sources"]
            if cell["window"] in ("W9s", "W9f") and not (src["a_set_task_records"] and src["c_audit_snapshots"] and diag["completion_state_regression"] == 1):
                fail["VQ-8"].append(f"{tag}: CSR sources {src}")
            if not diag["snapshots_verified"]:
                fail["VQ-8"].append(f"{tag}: snapshot hash mismatch or count")
            if not (folder / "diag/audit/snapshots.json").is_file() or (folder / "diag/sut/store/audit").exists():
                fail["VQ-8"].append(f"{tag}: snapshots not in runner-owned audit directory")
            store = folder / "diag/sut/store"
            arch = res["e8"]["archive"]
            if not diag["final_json_rule_ok"] or arch[-1]["action"] != "KEPT_LAST" or any(a["action"] == "KEPT_LAST" for a in arch[:-1]):
                fail["VQ-9"].append(f"{tag}: final.json rule {arch}")
            if recs and recs[-1]["process_outcome"] == "CLEAN" and (store / "final.json").is_file() and (store / "task.json").is_file():
                f_state = (jl(store / "final.json").get("task") or {}).get("deliveryState")
                t_state = jl(store / "task.json").get("deliveryState")
                if f_state != t_state:
                    fail["VQ-9"].append(f"{tag}: final.json {f_state} != task.json {t_state}")
            ps = res["primary_score"]
            if ps.get("scorer_uid") != scorer_uid or res["scorer_faults"]:
                fail["VQ-10"].append(f"{tag}: scorer uid {ps.get('scorer_uid')} faults {res['scorer_faults']}")
        # active probe on the live ledger root of this batch (one unit is enough; the permission model is per root)
        roots = [jl(d / "initial-state.json")["adapter_root"] for d, _ in runs[run].values() if (d / "initial-state.json").is_file()]
        if not roots:
            fail["VQ-10"].append(f"{run}: no initial-state.json, ledger probe not possible")
        else:
            probe = ledger_probe(Path(roots[0]) / "data/effects.sqlite3")
            if any(v != "DENIED" for v in probe.values()):
                fail["VQ-10"].append(f"{run}: ecr-sut ledger probe {probe}")
        eq = {cells[u]["extra_condition"]: u for u in cells if cells[u]["window"] == "EQ"}
        if set(eq) == {"eq_ref", "eq_hooks_inert", "eq_fault_endpoint_off"} and all(u in runs[run] for u in eq.values()):
            ref = norm_eq(*runs[run][eq["eq_ref"]])
            for t in ("eq_hooks_inert", "eq_fault_endpoint_off"):
                if norm_eq(*runs[run][eq[t]]) != ref:
                    fail["VQ-11"].append(f"{run}: {t} differs from eq_ref")
        else:
            fail["VQ-11"].append(f"{run}: EQ units missing")
    # q1 vs q2
    diffs = []
    for uid in sorted(set(runs["q1"]) & set(runs["q2"])):
        a, b = runs["q1"][uid][1], runs["q2"][uid][1]
        if "primary_score" not in a or "primary_score" not in b:
            continue
        drop = {"run_id", "scorer_pid", "scorer_uid", "arm"}
        sa = {k: v for k, v in a["primary_score"].items() if k not in drop}
        sb = {k: v for k, v in b["primary_score"].items() if k not in drop}
        da = {k: v for k, v in a["e8_diag"].items() if k != "path"}
        db = {k: v for k, v in b["e8_diag"].items() if k != "path"}
        if sa != sb or da != db:
            diffs.append({"unit": cells[uid]["name"], "scorer": sorted(k for k in sa if sa.get(k) != sb.get(k)),
                          "diag": sorted(k for k in set(da) | set(db) if da.get(k) != db.get(k))})
        if cells[uid]["window"] == "W6'":
            sa_, sb_ = [s["step"] for s in a["e8"]["w6_order_receipts"]], [s["step"] for s in b["e8"]["w6_order_receipts"]]
            if sa_ != sb_ or (a.get("w6_order") or {}).get("answer") != (b.get("w6_order") or {}).get("answer"):
                fail["VQ-6"].append(f"{cells[uid]['name']}: q1/q2 order receipts or answer differ")
    if diffs:
        fail["VQ-D"].append(diffs)
    # persistence
    if not (persist_sem and jl(persist_sem).get("status") == "OK"):
        fail["VQ-12"].append("persist_check semantics report missing or not OK")
    if not (persist_ver and jl(persist_ver).get("status") == "OK"):
        fail["VQ-12"].append("persist_check verify-after-reboot report missing or not OK")
    gates = {g: not v for g, v in fail.items()}
    hard = [g for g in gates if g not in ("VQ-D", "VQ-6") and not gates[g]]
    w6_untested = not gates["VQ-6"]
    # protocol 4.1 / 8: a failed W6' qualification marks the 6 W6' units UNTESTED; it does not by itself stop the other
    # windows. A VQ-D difference must be analysed before anything runs (fail closed).
    status = "FAIL" if hard or not gates["VQ-D"] else ("PASS_W6PRIME_UNTESTED" if w6_untested else "PASS")
    return {"status": status, "gates": gates, "failed": {g: v for g, v in fail.items() if v},
            "gate_definitions": GATES, "w6prime_formal_status_if_run": "UNTESTED (protocol 4.1)" if w6_untested else "TESTABLE",
            "q1_attempts": len(runs["q1"]), "q2_attempts": len(runs["q2"]),
            "w6_order": {f"{r}:{cells[u]['name']}": (runs[r][u][1].get("w6_order") or {}).get("answer")
                         for r in runs for u in runs[r] if u in cells and cells[u]["window"] == "W6'"}}


# ----------------------------------------------------------------------------------------------- kernel / space
# union of the protocol list (BUG, Oops, WARNING, trap, segfault, general protection) and the bl6 capture_kernel.sh list
PATTERN = re.compile(r"BUG:|\bBUG\b|Oops|WARNING|cut here|general protection|Machine check|mce:|\btrap\b|trap int3|segfault|"
                     r"Kernel panic|soft lockup|hung_task|EXT4-fs error")


def kernel() -> dict:
    p = subprocess.run(["journalctl", "-k", "-b", "--no-pager", "-o", "short-iso"], capture_output=True, text=True, timeout=60)
    hits = [line for line in p.stdout.splitlines() if PATTERN.search(line)]
    return {"status": "PASS" if p.returncode == 0 and not hits else "FAIL", "journalctl_exit": p.returncode, "hits": hits[:200],
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}


def space(path: Path, gib: float, inodes: int) -> dict:
    st = os.statvfs(path)
    free_gib, free_inodes = st.f_bavail * st.f_frsize / 2 ** 30, st.f_favail
    df = subprocess.run(["df", "-h", "-i", str(path)], capture_output=True, text=True).stdout
    return {"status": "PASS" if free_gib >= gib and free_inodes >= inodes else "FAIL", "free_gib": round(free_gib, 2),
            "free_inodes": free_inodes, "thresholds": {"min_free_gib": gib, "min_free_inodes": inodes}, "df": df}


def authorize(args) -> int:
    reports = {k: jl(args[args.index(f"--{k}") + 1]) for k in ("report", "unittests", "kernel", "space")}
    w6_untested = reports["report"].get("status") == "PASS_W6PRIME_UNTESTED"
    if w6_untested and "--accept-w6prime-untested" not in args:
        print("VQ-6 failed: the 6 W6' units would be UNTESTED; pass --accept-w6prime-untested only if the user agreed"); return 2
    bad = [k for k, r in reports.items() if r.get("status") != "PASS" and not (k == "report" and w6_untested)]
    # host finalize (same- and cross-runtime replay) of both VQ batches: every VQ attempt must be VALID_COMPLETED
    for k in ("finalized-q1", "finalized-q2"):
        fin = jl(args[args.index(f"--{k}") + 1])  # tools/finalize_states.py output
        states = {r["state"] for r in fin["attempts"].values()}
        if states != {"VALID_COMPLETED"} or len(fin["attempts"]) != 17:
            bad.append(f"{k}: {sorted(states)} n={len(fin)}")
    if bad or "--approved-by-user" not in args:
        print("not authorizing:", bad or "missing --approved-by-user"); return 2
    pkg = Path(__file__).resolve().parents[1]
    manifest_sha = args[args.index("--manifest-sha") + 1]
    if sha(pkg / "execution-manifest-e8.json") != manifest_sha:
        print("manifest sha differs from the deployed manifest"); return 2
    out = pkg / "qualification-authorization-e8.json"
    fd = os.open(out, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"status": "PASS_HOST_AUDIT", "manifest_sha256": manifest_sha,
                   "reports_sha256": {k: sha(args[args.index(f"--{k}") + 1]) for k in (*reports, "finalized-q1", "finalized-q2")},
                   "authorizes": {"runs": [{"set": "E8", "batch_id": "ecr-e8x-srv-b1", "boot": "boot A (cold)"},
                                           {"set": "E8", "batch_id": "ecr-e8x-srv-c1", "boot": "boot B (new cold boot)"}],
                                  "units_per_run": 269, "attempts_per_unit": "first attempt only; INFRA_FAILED retry once per protocol 8"},
                   "not_approved": ["code, cell table, prediction or tool changes", "rerun of a VALID_COMPLETED unit"],
                   "w6prime_status": "UNTESTED (VQ-6 failed; protocol 4.1)" if w6_untested else "TESTABLE",
                   "approved_by_user": True}, f, indent=1)
    print("written", out)
    return 0


if __name__ == "__main__":
    cmd, a = sys.argv[1], sys.argv[2:]
    get = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    if cmd == "unittests":
        rep = unittests(Path(get("--pkg")), Path(get("--scratch")))
    elif cmd == "batch":
        rep = batch_checks(Path(get("--q1")), Path(get("--q2")), Path(get("--pkg")), get("--persist-semantics"), get("--persist-verify"))
    elif cmd == "kernel":
        rep = kernel()
    elif cmd == "space":
        rep = space(Path(get("--path")), float(get("--min-free-gib")), int(get("--min-free-inodes")))
    elif cmd == "authorize":
        sys.exit(authorize(a))
    else:
        sys.exit("unknown subcommand")
    write_report(get("--out"), rep)
    # exit code carries the verdict: 0 = PASS, 3 = PASS_W6PRIME_UNTESTED (only VQ-6 failed; needs the user's decision),
    # 1 = FAIL (and any other status). Shell callers must not treat 3 as success.
    sys.exit({"PASS": 0, "PASS_W6PRIME_UNTESTED": 3}.get(rep["status"], 1))
