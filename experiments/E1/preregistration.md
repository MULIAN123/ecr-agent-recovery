# R3 ablation extension (`ablation-ext-20261012-r3`): preregistration

Written 2026-10-12 before qualification `core-ts-bridge-ext-20261012-r3` and before any formal cell. The researcher approved the design in chat. No treatment code was invoked to build scenarios or truth.

## Identity
- Package `core_execution_r3_ext/`, bundle `bundles/ecr-ablation-ext-r3.tar.gz` (SHA-256 `822b60be12050d3976b1e6ad5211c1dba5029f9ea2be557dbf3fe0b6c249cbdc`), manifest SHA-256 `3d5e84b06a253c5a5d3f757540591b2970c1048cc4112f583f83e90320afab9d`.
- Generator: `<WORKSPACE>`.
- Byte-identical to R2: `run.py`, `scorer.py`, `sut.mjs`, all 8 arm sources and patches, `scoring-spec.md`, `source-mapping.json`, closure sources. Only scenarios, truth, matrix, READMEs, input-freeze and manifest identifiers differ.

## Motivation
R2 challenged each removed check with one targeted scenario (C05–C09; C10 shared by two). This batch adds 3–5 further targeted challenges per check, positive controls and masking scenarios, so that necessity is not shown on a single hand-picked case.

## Scenarios (47 = 12 R2 unchanged + 35 new)
| Group | New scenarios | Truth kind |
|---|---|---|
| A_ID_COMPARE | E_ID_01..05: conflicting attemptId (queue), deliverableId (task), taskId (one event, unknown base), queueItemId (queue), attemptId (task, pending base) | INVALID |
| A_VERSION_COMPARE | E_VER_01..04: version 2 in queue item; in dispatch event; task version 0 (unknown base); queue version 2 (pending base) | INVALID |
| A_SEQUENCE | E_SEQ_01..04: equal adjacent sequence; final regression; all equal; regression on unknown base | INVALID |
| A_RANK | E_RANK_01..04: outcome before dispatch; queue item before binding; dispatch before queue item (unknown base); queue item before binding (pending base) | INVALID |
| A_OUTCOME_BOUNDARY | E_OUTB_01..03: attempt + accepted outcome only (no queue); `definite_failure` outcome without dispatch; outcome without dispatch with processed queue item | INVALID |
| A_UNKNOWN_PRESERVATION | E_UNK_01A/01B twin (transport timeout after dispatch; synthetic effect 0/1, identical snapshot); queue record lost after dispatch; local state `failed` after dispatch; queue item marked processed after dispatch | UNKNOWN |
| A_ACCEPTANCE_LEVEL | E_ACC_01..03: evidence level missing; `transport_ack`; `local_unverified` without queue item | UNKNOWN |
| Controls | E_CTL_01..03: valid acceptance with queue record gone; with processed queue item; with non-contiguous increasing sequences | ACCEPTANCE |
| Masking | E_MSK_01 identity+version conflict; E_MSK_02 sequence+rank regression; E_MSK_03 rank regression with non-attempt first event; E_MSK_04 incomplete identity + identity conflict | INVALID |

Targeted challenges per check including R2: ID 6, VERSION 5, SEQUENCE 5, RANK 5, OUTCOME_BOUNDARY 4, UNKNOWN_PRESERVATION 8 (C02, C03, C10 + 5), ACCEPTANCE_LEVEL 4 (C10 + 3).

Truth rules (from the scoring contract): ACCEPTANCE only with dispatch evidence and an `adapter_accepted` outcome at `adapter_accepted` level with consistent, complete, ordered identity evidence; UNKNOWN when dispatch is evidenced without a sufficient outcome; PENDING only before dispatch; INVALID for any identity/version conflict, sequence or order violation, incomplete identity, unreadable journal, or outcome without dispatch. Abstention (`CORRUPT_OR_UNREADABLE_EVIDENCE` / `JOURNAL_QUEUE_IDENTITY_CONFLICT` with `FAIL_CLOSED`) is permitted everywhere and is not counted as completion.

## Analysis plan
1. Per arm: unsupported automatic classifications, invalid action pairs, unknown preserved, abstentions; per removed check: share of its targeted scenarios that change from FULL and the resulting classification (failure mode).
2. Controls: any non-acceptance output in any arm is reported as over-rejection.
3. Masking: report, per masking scenario and arm, whether the remaining check still rejects. Hypothesis (not a truth label): all arms abstain on E_MSK_01..04.
4. FULL arm: any fault on a new scenario is reported as a FULL finding (possible gap in the mechanism or in the scenario/truth), not silently relabelled.
5. Determinism: the 96 cells on the 12 R2 scenarios must equal R2 cell by cell; E_UNK_01A/01B outputs must be identical within each arm; cross-host replay of all 376 cells with host Node (secondary).
6. Stop rules as R2: infrastructure fault, V8 fatal, timeout, hash drift or residue ⇒ `FAIL_RETAIN_EVIDENCE`, no automatic retry.

Scope stays `STATIC_EVIDENCE_CORE_NO_EXTERNAL_EXECUTOR`: duplicate external effects, safe-opportunity completion and latency are NOT_MEASURED. Any statement about retry after a `CONFIRMED_NO_ACCEPTANCE_EVIDENCE` output is an integration inference, not a measurement.
