"""Predeclared byte substitutions for package bl1 (used by prep derive_bl1.py logic and test T4)."""

RUNNER = [
    (b"  run    --set qualification|P3A|P3C --batch-id ID", b"  run    --set qualification|BLX --batch-id ID"),
    (b'BATCH_PARENT = BASE / "async-sim-r2"', b'BATCH_PARENT = BASE / "async-sim-bl1"'),
    (b'PUBLIC_PARENT = Path("/opt/ecr-followup-r1-async-r2")', b'PUBLIC_PARENT = Path("/opt/ecr-followup-r1-async-bl1")'),
    (b'AGENT = {"v1": "src/agent_v1.py", "CONV": "src/agent_v1.py", "v2": "src/agent_v2.py"}',
     b'AGENT = {"v1": "src/agent_v1.py", "CONV": "src/agent_v1.py", "v2": "src/agent_v2.py",\r\n'
     b'         "BL": "src_bl/agent_bl.py"}  # bl1: CCF-A rule-level baselines on the frozen v1 send path'),
    (b'    target = public / "execution-manifest-r2.json"\r\n    shutil.copyfile(HERE / "execution-manifest-r2.json", target)',
     b'    target = public / "execution-manifest-bl1.json"\r\n    shutil.copyfile(HERE / "execution-manifest-bl1.json", target)'),
    (b'    if sha(target) != sha(HERE / "execution-manifest-r2.json"):',
     b'    if sha(target) != sha(HERE / "execution-manifest-bl1.json"):'),
    (b'if executor != "CONV" else ""}', b'if executor not in ("CONV", "BL") else ""}'),
    (b'"ECR_CODE_MANIFEST": str(public / "execution-manifest-r2.json")',
     b'"ECR_CODE_MANIFEST": str(public / "execution-manifest-bl1.json")'),
    (b'    cells = json.loads((HERE / "private/cells_p3.json").read_text())\r\n'
     b'    if sha(HERE / "private/cells_p3.json") != manifest["files"]["private/cells_p3.json"]:',
     b'    cells = json.loads((HERE / "private/cells_bl1.json").read_text())\r\n'
     b'    if sha(HERE / "private/cells_bl1.json") != manifest["files"]["private/cells_bl1.json"]:'),
    (b'("r2-" + args.batch_id), "adapter": BASE / "ledger" / ("r2-" + args.batch_id)}',
     b'("bl1-" + args.batch_id), "adapter": BASE / "ledger" / ("bl1-" + args.batch_id)}'),
    (b'run.add_argument("--set", choices=["qualification", "P3A", "P3C"], required=True)',
     b'run.add_argument("--set", choices=["qualification", "BLX"], required=True)'),
    (b'    manifest_path = HERE / "execution-manifest-r2.json"', b'    manifest_path = HERE / "execution-manifest-bl1.json"'),
]

MANIFEST_TOOL = [
    (b'"""Write execution-manifest-r2.json for runner/run_r2.py (phase 3). Run on the guest after the JS',
     b'"""Write execution-manifest-bl1.json for runner/run_bl1.py (CCF-A rule-level baselines bl1; derived from the 2b tool:\r\n'
     b'only the manifest name, the required cell table and the public src_bl/ directory differ). Run on the guest after the JS'),
    (b'NAME = "execution-manifest-r2.json"', b'NAME = "execution-manifest-bl1.json"'),
    (b'    if "private/cells_p3.json" not in files:\r\n        raise SystemExit("private/cells_p3.json missing")',
     b'    if "private/cells_bl1.json" not in files:\r\n        raise SystemExit("private/cells_bl1.json missing")'),
    (b'rel.startswith(("src/", "arms/"))', b'rel.startswith(("src/", "src_bl/", "arms/"))'),
]
