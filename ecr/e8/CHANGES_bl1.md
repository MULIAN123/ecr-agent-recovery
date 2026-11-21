# async_sim_bl1 — changes vs the frozen 2b package async_sim_r2 (candidate v4)

Created 2026-11-13 for the CCF-A rule-level baseline batch (INTENT-LOG, RIFL-C). This directory started as a
byte copy of `codex_qqrobot/experiments/ECR_FOLLOWUP_R1/async_sim_r2` (42 files, all equal to candidate
manifest v4, `freeze_candidates_code_manifest_v4.json` sha256 6496b502...). The original directory was not modified.

Modified files: none. All 42 v4 files are byte-identical to v4 (test T1).

Added files:
- src_bl/agent_bl.py — the two baseline recovery policies (INTENT_LOG, RIFL_C) on the frozen v1 send path
- src_bl/* (10 files) — byte copies of src/ (agent_bl.py imports the frozen agent_v1.py from its own directory)
- runner/run_bl1.py — run_r2.py with 12 exact substitutions only: batch/public parents async-sim-bl1, executor key
  BL -> src_bl/agent_bl.py, no classifier directory for BL (as for CONV), execution-manifest-bl1.json,
  private/cells_bl1.json, state roots bl1-<batch>, --set qualification|BLX
- tools/make_execution_manifest_bl1.py — make_execution_manifest.py with 4 substitutions (manifest name, required
  cell table, public src_bl/ directory)
- tools/build_bl1_cells.py — builds private/cells_bl1.json from the frozen private/cells_p3.json
- private/cells_bl1.json — 128 BLX units + 7 BLQ qualification units
- tests/test_bl1_static.py, tests/bl1_substitutions.py, tests/v4_file_hashes.json — static checks T1-T6
- CHANGES_bl1.md — this file

Inert files kept for traceability: runner/run_r2.py, tools/make_execution_manifest.py, tools/build_p3_cells.py,
private/cells_p3.json (the source table). run_r2.py cannot start here: it needs execution-manifest-r2.json,
which this package must never contain (test T5).
Arm rules and sources: ../frozen/ARM_DEFINITIONS_bl1.md.
