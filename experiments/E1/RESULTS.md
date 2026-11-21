# R3 ablation extension (`ablation-ext-20261012-r3`): results record

Date: 2026-10-12. Operator: Claude Code. Design and analysis plan: `preregistration.md` (written before execution). Scope `STATIC_EVIDENCE_CORE_NO_EXTERNAL_EXECUTOR`; duplicate external effects, safe-opportunity completion and latency are NOT_MEASURED.

## Execution chain
| Step | Evidence | Result |
|---|---|---|
| Bridge qualification `core-ts-bridge-ext-20261012-r3` | `../ecr-bridge-evidence-ext-r3.tar.gz` (`a78d3d62…`) | `PASS_BRIDGE_ONLY` |
| Host bridge audit / authorization | `bridge-audit.json`, `bridge-authorization.json` (`4baa8a67…`) | `PASS_HOST_AUDIT` |
| Formal 376 cells (47 scenarios × 8 arms) | `../ecr-ablation-evidence-ext-r3.tar.gz` (`b4d2cc29…`) | 376/376 `OBSERVED`; no new guest crash report |
| Host formal audit | `formal-audit.json` | `PASS_INTEGRITY_AUDIT_COMPLETE_BATCH`, 10/10 checks, every snapshot equals the predeclared one |
| Determinism vs R2 (12 reused scenarios) | `formal-audit.json` → `r2_determinism` | 96/96 identical |
| Twins (C02/C03, E_UNK_01A/01B) | `formal-audit.json` | identical outputs in every arm |
| Cross-host replay | `cross-host-replay.json` | 376/376 identical (guest Node v24.21.0, host v24.18.0) |

Scripts: `<WORKSPACE>` (`build_ablation_ext_r3.py`, `audit_core_bridge_ext_r3.py`, `prepare_core_bridge_authorization_ext_r3.py`, `audit_ablation_ext_r3.py`, `cross_host_replay_ext_r3.py`).

Tooling note: the host msys2 Python 3.14.6 crashed while reading this archive (segmentation faults and internal `tarfile`/`zlib` errors at different points). The archive hash matched the guest and `gzip -t` passed. Host audits were then run with Miniforge CPython 3.13.13 (`<HOST-PATH>`) and archives decompressed in memory. Those scripts are read-only, so no evidence was affected. No WHEA hardware error was logged on the host in the preceding 3 days.

## Main results
**FULL:** 0 faults on all 47 scenarios. **Controls** (C01, C04, E_CTL_01..03): 0 faults in all 8 arms, so no removal caused over-rejection of valid evidence.

| Removed check | Targeted scenarios (incl. R2) | Faulted | Failure modes on targeted scenarios |
|---|---|---|---|
| Identity agreement | 6 | 6 | false acceptance 4, false unknown 1, false pending 1 |
| Task-state version | 5 | 5 | false acceptance 3, false unknown 1, false pending 1 |
| Journal sequence | 5 | 5 | false acceptance 4, false unknown 1 |
| Event rank order | 5 | 5 | false acceptance 3, false unknown 1, false pending 1 |
| Outcome requires dispatch | 4 | 4 | false acceptance 3 (`QUEUE_STALE…` 1, `ADAPTER_ACCEPTED…` 2), **false definite failure 1** (`CONFIRMED_NO_ACCEPTANCE_EVIDENCE/FUTURE_STRATEGY_ONLY`) |
| Unknown-outcome preservation | 8 | 8 | false pending 7 (`PENDING_EXECUTION`), queue-missing 1 (`QUEUE_BINDING_PREPARED_BUT_QUEUE_MISSING/NO_EXECUTION`) |
| Acceptance evidence level | 4 | 4 | false acceptance 4 |

Terms: *false acceptance* = invalid or insufficient evidence classified as adapter acceptance; *false pending* = post-dispatch or conflicting evidence classified as pre-dispatch queue responsibility; *false unknown* = conflicting evidence classified `OUTCOME_UNKNOWN` instead of abstaining; *false definite failure* = an outcome without dispatch evidence classified as confirmed pre-acceptance failure, the class the contract reserves for future retry strategies.

Additional faults outside the preregistered target lists: removing unknown-outcome preservation also misclassified E_ACC_01..03 (weak acceptance after dispatch) as `PENDING_EXECUTION`. These scenarios have UNKNOWN truth, the same mechanism as C10. With C10, unknown preservation guards 4/4 weak-acceptance cases in addition to its 8 targeted ones. No other removal faulted outside its targets.

**Masking (hypothesis held):** E_MSK_01..04 were rejected by every arm (identity+version → `JOURNAL_QUEUE_IDENTITY_CONFLICT`; sequence+rank, rank+attempt-first, incomplete+conflicting identity → `CORRUPT_OR_UNREADABLE_EVIDENCE`). When two independent defects are present, removing one check leaves another to reject.

## Interpretation limits
- Deterministic predeclared cells; counts are descriptive, not population rates.
- Truth is synthetic and predeclared; there is no executor or effect ledger. "Would resend" (false pending) and "would permit future retry" (false definite failure) are integration inferences, to be measured in the asynchronous simulation.
- Artifact byte/membership integrity and retry suppression remain excluded (no implementing predicate or executor in the selected source).
