"""Static checks for package bl1 (no endpoint, no VM state, no unit run). Usage: python3 -I -B tests/test_bl1_static.py

T1 every frozen v4 file is unchanged (hashes from the v4 candidate manifest embedded below by digest)
T2 src_bl/ = byte copies of src/ plus agent_bl.py only
T3 cells_bl1.json: 128 BLX + 7 BLQ; scenario, truth and endpoint contract equal to the frozen 2b FULL/v1 units
T4 runner/run_bl1.py and tools/make_execution_manifest_bl1.py differ from their frozen sources only by the
   predeclared substitutions (re-derived here and compared byte for byte)
T5 no execution-manifest-r2.json in the package (run_r2.py stays inert)
T6 agent_bl.py imports only the frozen agent_v1/protocol modules and defines exactly the two policies
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CELLS_P3_SHA256 = "c244199721f4982bbda5aacf2b09e837e19a6b1395ab62a6dcdbc7025aa190b6"
RESULTS = []


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))


def t1():
    m = json.loads((ROOT / "tests/v4_file_hashes.json").read_text())
    bad = [rel for rel, h in m.items() if not (ROOT / rel).is_file() or sha(ROOT / rel) != h]
    check("T1 v4 files unchanged", len(m) == 42 and not bad, f"{len(m)} files, changed: {bad}")


def t2():
    src = {p.name for p in (ROOT / "src").iterdir() if p.is_file()}
    bl = {p.name for p in (ROOT / "src_bl").iterdir() if p.is_file()}
    diff = [n for n in src if sha(ROOT / "src" / n) != sha(ROOT / "src_bl" / n)]
    check("T2 src_bl = src + agent_bl.py", bl == src | {"agent_bl.py"} and not diff, f"extra={sorted(bl - src)} differing={diff}")


def t3():
    p3 = json.loads((ROOT / "private/cells_p3.json").read_text())
    ok_src = sha(ROOT / "private/cells_p3.json") == CELLS_P3_SHA256
    base = {u["scenario"]["scenario_id"]: u for u in p3["units"]
            if u["family"] == "P3A" and u["classifier_arm"] == "FULL" and u["executor"] == "v1"}
    bl = json.loads((ROOT / "private/cells_bl1.json").read_text())
    blx = [u for u in bl["units"] if u["family"] == "BLX"]
    blq = [u for u in bl["units"] if u["family"] == "BLQ"]
    same = all(u["scenario"] == base[u["scenario"]["scenario_id"]]["scenario"]
               and u["truth"] == base[u["scenario"]["scenario_id"]]["truth"]
               and u["endpoint_contract"] == base[u["scenario"]["scenario_id"]]["endpoint_contract"] for u in bl["units"])
    arms = {(u["policy"], u["executor"]) for u in bl["units"]}
    per = {pol: len({u["scenario"]["scenario_id"] for u in blx if u["policy"] == pol}) for pol in ("INTENT_LOG", "RIFL_C")}
    check("T3 cell table", ok_src and len(blx) == 128 and len(blq) == 7 and same
          and arms == {("INTENT_LOG", "BL"), ("RIFL_C", "BL")} and per == {"INTENT_LOG": 64, "RIFL_C": 64},
          f"src_ok={ok_src} blx={len(blx)} blq={len(blq)} same={same} arms={sorted(arms)} per={per}")


def t4():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bl1_substitutions as s  # noqa: E402
    ok = []
    for src, dst, subs in ((ROOT / "runner/run_r2.py", ROOT / "runner/run_bl1.py", s.RUNNER),
                           (ROOT / "tools/make_execution_manifest.py", ROOT / "tools/make_execution_manifest_bl1.py", s.MANIFEST_TOOL)):
        data = src.read_bytes()
        counts = [data.count(old) for old, _ in subs]
        for old, new in subs:
            data = data.replace(old, new)
        ok.append(counts == [1] * len(subs) and data == dst.read_bytes())
    check("T4 runner/manifest tool = predeclared substitutions", all(ok), str(ok))


def t5():
    check("T5 no r2 execution manifest", not (ROOT / "execution-manifest-r2.json").exists())


def t6():
    text = (ROOT / "src_bl/agent_bl.py").read_text()
    imports = sorted(line.strip() for line in text.splitlines() if line.startswith(("import ", "from ")))
    expected = ["from __future__ import annotations", "from pathlib import Path", "from protocol import durable_json",
                "import agent_v1 as v1", "import argparse", "import json", "import os"]
    check("T6 agent_bl imports and policies", imports == sorted(expected) and 'POLICIES = ("INTENT_LOG", "RIFL_C")' in text,
          str(imports))


if __name__ == "__main__":
    for t in (t1, t2, t3, t4, t5, t6):
        try:
            t()
        except Exception as error:  # a failing check is reported, never skipped
            check(t.__name__, False, f"{type(error).__name__}: {error}")
    for name, ok, detail in RESULTS:
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    print(f"{sum(ok for _, ok, _ in RESULTS)}/{len(RESULTS)} passed")
    sys.exit(0 if all(ok for _, ok, _ in RESULTS) else 1)
