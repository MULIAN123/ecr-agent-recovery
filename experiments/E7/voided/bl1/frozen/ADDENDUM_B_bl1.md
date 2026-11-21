# Addendum B to the bl1 freeze (written before any bl1b/bl1c unit runs)

Date: 2026-11-13. Approved by the user in chat ("用方案 B 跑").
Reason: the host proved unstable on 2026-11-13. The guest kernel reported a WARNING (filemap_add_folio) under VMware 26.0.1, and an Oops (NULL pointer dereference) under 25.0.0. A CPython interpreter assertion (memory corruption) stopped batch `ecr-blx-20261113-r1` at unit 57/128. All earlier guest boots (2026-10-11 to 10-21) show no BUG/Oops/WARNING/GPF/MCE. Batches `ecr-blq-20261113-r1` and `ecr-blx-20261113-r1` are void. They are kept as evidence and are never used as results.

Unchanged: agent code, cell table (cells_bl1.json be79c917…), predictions (bb18c342…), scorers, analysis tool (aggregate_bl1.py 2c66f415…), arm definitions, interpretation decisions I1–I5, rule R0 (first attempt only).

## B1 Fresh namespaces

`runner/run_bl1b.py` and `runner/run_bl1c.py` are derived from `runner/run_bl1.py`. Each differs only by 3 exact substitutions (prep/scripts/derive_bl1_bc.py): the batch parent (`async-sim-bl1b` / `async-sim-bl1c`), the public parent, and the state-root prefix. Each run therefore starts with first attempts in its own attempt index. The package gets a new candidate code manifest (63 files) and a new execution manifest.

## B2 Sequence

1. Boot 1 (fresh VM boot): A3 qualification, 7 BLQ units via run_bl1b.py (batch `ecr-blq-20261113-b1`), same-runtime replay, export. Host audit, as in the first A3.
2. If the audit passes and gate G-K holds: write the authorization (PASS_HOST_AUDIT, bound to the new execution manifest). Still in boot 1: A4 run 1, 128 BLX units via run_bl1b.py (batch `ecr-blx-20261113-b1`), same-runtime replay, export.
3. Soft shutdown, then boot 2 (fresh): A4 run 2, 128 BLX units via run_bl1c.py (batch `ecr-blx-20261113-c1`), same-runtime replay, export.
4. On the host: cross-runtime replay and finalisation for each batch; aggregate_bl1.py for each batch separately; then the run comparison G-D.

No Node-based test (test_phase3_tools, test_classifier_unchanged) runs in boot 1 or boot 2. The route-T TypeScript path produces int3 traps that gate G-K would count.

## B3 Gate G-K (kernel and interpreter integrity)

A boot passes only if `journalctl -k -b` for that whole boot, read after its export, contains none of:
`BUG:` `Oops` `cut here` `general protection` `Machine check` `mce:` `trap int3` `segfault` `Kernel panic` `soft lockup` `hung_task` `EXT4-fs error`
and if no attempt's stderr file in its exported batches contains `Fatal Python error` or `Assertion failed`.

If a boot fails G-K, every batch run in that boot is void. No unit is kept selectively. Work stops and is reported, and plan A (hardware test first) applies.

## B4 Gate G-D (double run)

Both A4 runs must reach COMPLETED_PENDING_REPLAY, with all 128 units VALID_COMPLETED after finalisation. For every unit, the primary scorer fields
`final_state, first_classification, opportunity_completed, true_completion, recovery_sends, authority_denials, s4_invalid_sends, duplicate_effect, unauthorized_effect, false_completion, effect_count`
and the sequence of AUTHORIZE_SEND labels must be identical in run 1 and run 2.

Any difference means that neither run is accepted. The differences are reported, and plan A applies.

## B5 Reporting

If G-K holds for both boots and G-D holds, the reported result is run 1 (`ecr-blx-20261113-b1`, R0 first attempts). Run 2 is reported as the agreement check, not as extra sample size. Comparison with the frozen predictions stays descriptive: a deviation is a result, never a reason to change anything.

The paper's Threats section will disclose:
- the host instability;
- the void batches;
- the VMware downgrade;
- gates G-K and G-D.
