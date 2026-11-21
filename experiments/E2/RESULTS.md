# Experiment 2 (`async-sim-20261012-r1`): results record

Date: 2026-10-12. Operator: Claude Code. Design and analysis plan: `async_sim_r1/PREREGISTRATION.md`; truth freeze: `async_sim_r1/TRUTH_FREEZE.md`. Local simulation only; deterministic predeclared cells; counts are descriptive.

## Execution chain
| Step | Evidence | Result |
|---|---|---|
| Qualification `async-sim-qual-20261012-r1` (13 cells + 3 offline scorer fixtures) | `../ecr-async-qual-evidence-r1.tar.gz` (`578ec890…`) | 13/13 matched harness expectations; duplicate, false-completion and unsafe-authorization fixtures detected |
| Host qualification audit / authorization | `qualification-audit.json`, `qualification-authorization.json` (`9386eac4…`) | `PASS_HOST_AUDIT` |
| Formal 416 cells (52 scenarios × 8 arms) | `../ecr-async-formal-evidence-r1.tar.gz` (`d789a997…`) | 416/416 `OBSERVED`; no guest crash report |
| Host formal audit | `formal-audit.json` | `PASS_INTEGRITY_AUDIT_COMPLETE_BATCH`: barrier receipts, SIGKILL receipts, agent ledger-access denials (4 per cell), overlay identity, deployed file hashes, planner runtime v24.21.0 for every cell |

Per-cell outcome codes: `outcome-matrix.csv`. Scripts: `<WORKSPACE>` (`build_async_sim_r1.py`, `audit_async_qual_r1.py`, `audit_async_formal_r1.py`; dev smoke scripts disclosed in the preregistration).

## Totals per arm (52 scenarios; 38 predeclared safe opportunities per arm)
| Arm | Duplicate effects | Unauthorized effects* | False completion | Contract-invalid send authorizations | Opportunities completed (/38) | Unresolved |
|---|---|---|---|---|---|---|
| EBR (original mechanism) | 0 | 0 | 0 | 0 | 8 | 44 |
| **EBRQ (mechanism + query extension)** | **0** | **0** | **0** | **0** | **29** | 23 |
| CONV (strong conventional baseline) | 1 | 5 | 8 | 5 | 32 (+1 completion without an opportunity) | 6 |
| STOP | 0 | 4 | 0 | 4 | 4 | 44 |
| RETRY | 12 | 16 | 8 | 32 | 11 | 0 |
| EBRQ without unknown preservation | 10 | 10 | 0 | 28 | 8 | 16 |
| EBRQ without outcome-requires-dispatch | 2 | 2 | 0 | 4 | 29 | 19 |
| EBRQ without acceptance-level check | 0 | 0 | 4 | 0 | 26 | 22 |

*Unauthorized = effects above the allowed count; this includes duplicates and every effect in F5.

## Findings
1. **Clean crash points (32 scenarios, B1–B6 × P0–P3): EBRQ and CONV are identical in every scenario.** Both complete all 26 opportunities with no safety event, and both leave the 6 P0 post-dispatch cases unresolved, which is the only safe choice without a query or idempotency contract. The original EBR completes only B1/B6 (8), because it never queries. RETRY duplicates on P0/P1 (B2_LATE, B2_ARRIVE, B3_LATE, B4, B5: 10) and authorizes 24 contract-invalid sends in these groups. STOP completes only pre-dispatch work.
2. **Defective local evidence (20 overlay scenarios) is where EBRQ and CONV differ (20/20 overlay scenarios differ, 0/32 clean ones).**
   - Benefits of EBRQ: no false completion in F2 (foreign-attempt acceptance) or F3 (transport-level acknowledgement only), where CONV falsely marked 8 tasks accepted with no effect; with F3 on P1–P3 EBRQ fenced, resent with the fenced generation and completed 3 tasks correctly. No effect for a superseded task version in F5, where CONV, STOP and RETRY each produced 4 unauthorized effects. No duplicate in F6 on P0, where CONV resent on a spurious failure record.
   - Costs of EBRQ: 6 genuine completions left unresolved that CONV completed correctly (F4 genuine callback with a stale sequence number, P1–P3; F6 lost dispatch record, P1–P3), because EBRQ does not query when local evidence fails validation. It also left F2 on P1–P3 unresolved (3 opportunities missed, where CONV produced false completions).
   - Net: EBRQ completed 29/38 opportunities vs CONV 32/38. EBRQ had no scenario with a safety event; CONV had 13 (8 false completions in F2/F3, 4 unauthorized effects in F5, 1 duplicate in F6 on P0; the F5 and F6-P0 cells also carry the 5 contract-invalid authorizations).
3. **The query extension adds completions at no safety cost:** EBR → EBRQ goes from 8 to 29 completed opportunities with zero events in either arm.
4. **Integrated ablations turn the experiment-1 classification errors into observed harm:**
   - Removing unknown preservation: 10 duplicate effects (B2_LATE, B2_ARRIVE, B3_LATE, B4, B5 on P0/P1) and 28 contract-invalid sends; post-dispatch evidence is treated as pending and the queue worker dispatches again.
   - Removing outcome-requires-dispatch: 2 duplicates in F6 (P0, P1); a spurious failure record without dispatch evidence is classified as a confirmed failure and resent.
   - Removing the acceptance-level check: 4 false completions in F3 (all profiles); a transport acknowledgement is taken as adapter acceptance.

## Interpretation limits
- The overlay set (5 defect types × 4 profiles) and its proportion relative to clean crash points were chosen by the authors. Differences between EBRQ and CONV arise only under these defects; on clean crash points the mechanism offers no advantage over a strong conventional baseline.
- CONV is one reasonable conventional design (trusts recorded outcomes and queued items without identity/version/order validation). A baseline that adds such validation would converge toward EBRQ.
- The query extension is new code around the frozen classifier and planner (declared in the preregistration); it is not part of the original source.
- One endpoint simulator, one process per role, no concurrent recovery workers (F1 deferred), and no real messaging endpoint.
