# async_sim_cp1 — changes vs the frozen 2b package async_sim_r2 (candidate v4)

Created 2026-10-21 for ECR r9.6 Batch A (CONV+ / CONV+g). This directory started as a byte copy of
`codex_qqrobot/experiments/ECR_FOLLOWUP_R1/async_sim_r2` (42 files, all equal to candidate manifest v4,
`freeze_candidates_code_manifest_v4.json` sha256 6496b502...). The original directory was not modified.

Modified files: none. All 42 v4 files are byte-identical to v4.

Added files:
- arms/CP_SEQ_DISP/delivery-reconciliation.ts — FULL minus D1 (C_seq) and D2 (C_disp)
- arms/CP_SEQ_DISP/delivery-reconciliation-execution.ts — byte copy of FULL
- src_cp/* (10 files) — byte copies of src/ except evidence.py = src/evidence.py minus D3 (EV5 validity)
- runner/run_cp1.py — run_r2.py with substitutions only: batch/public parents async-sim-cp1, executor key
  v2cp -> src_cp/agent_v2.py, v2 configuration also for v2cp, execution-manifest-cp1.json,
  private/cells_cp1.json, state roots cp1-<batch>, --set qualification|CPX
- tools/build_cp1_cells.py — builds private/cells_cp1.json from the frozen private/cells_p3.json
- tools/make_execution_manifest_cp1.py — make_execution_manifest.py writing execution-manifest-cp1.json,
  requiring private/cells_cp1.json, and deploying src_cp/ as a public directory
- private/cells_cp1.json — 256 CPX units + 8 CPQ qualification units
- tests/test_cp1_static.py — static checks T1-T7
- CHANGES_cp1.md — this file

Inert files kept for traceability: runner/run_r2.py, tools/make_execution_manifest.py, tools/build_p3_cells.py,
private/cells_p3.json (the source table). run_r2.py cannot start here: it needs execution-manifest-r2.json,
which this package must never contain (test T7).
Exact diffs: see ../frozen/ARM_DEFINITIONS_cp1.md and ../frozen/SOURCE_DIFFS_cp1.diff.
