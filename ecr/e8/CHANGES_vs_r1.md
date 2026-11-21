# async_sim_r2: differences from async_sim_r1 and authorization paths

Written 2026-10-15 (technical fix, phase 2). Nothing in `async_sim_r1/`, in the historical
evidence or in any preregistration was modified; r1 files used here are byte-identical copies.

## 1. File inventory

| File | Status | SHA-256 (prefix) | Note |
|---|---|---|---|
| `src/endpoint.py` | copy of r1, unchanged | `d5d3b5e1` | endpoint contract P0–P3, external effect ledger |
| `src/protocol.py` | copy of r1, unchanged | `ca67293c` | `durable_json` (fsync + exclusive link), barriers |
| `src/plan.mjs` | copy of r1, unchanged | `3cfb9f2a` | bridge to the frozen planner |
| `src/scorer.py` | copy of r1, unchanged | `e4cbb567` | historical scorer, run as secondary scorer |
| `src/agent_v1.py` | copy of r1 `agent.py`, unchanged | `e9e24a94` | frozen EBR/EBRQ executor and the CONV/STOP/RETRY reference policies |
| `arms/*/*.ts` (8 files) | copies of r1, unchanged | FULL classifier `a293da95` | frozen classifier and planner, FULL and three ablations |
| `src/evidence.py` | **new** | `e5fbdf3d` | read-only Evidence Validator (EV1–EV7), independent of the classifier |
| `src/authority.py` | **new** | `666a3383` | authorization gate; the only code in the EBRQ-v2 path that calls `/send` or `/fence` |
| `src/agent_v2.py` | **new** (imports frozen helpers from `agent_v1.py`) | `34a07df1` | gated send path (R2A/R2B) and EBRQ-v2 recovery |
| `src/scorer_v2.py` | **new** | `846d612b` | independent scorer; S4 = claimed basis verified on independent evidence |
| `tests/*` | **new** | see the phase-2 report | unit, static, invariance, scorer, end-to-end and crash-point tests |

## 2. Send path (first dispatch)

| Step | r1 `agent.send` | r2 `agent_v2.send` |
|---|---|---|
| 1–3 | AttemptCommitted, QueueBindingPrepared, queue.json, QueueItemPersisted | same. R2B adds `logicalOperationId`, `operationVersion` to AttemptCommitted |
| B1 | barrier `intent_persisted` | same |
| — | — | `authority.authorize("PRE_DISPATCH", first_dispatch=True)` → durable grant |
| — | — | `authority.dispatch`: exclusive `consumed/<grant>` marker |
| 4 | journal DispatchBoundaryCrossed | same, written by the dispatch callback. R2B adds `grantId`, `logicalOperationId`, `generation`, `sendAttemptId` |
| send | HTTP `/send` from `send()` | HTTP `/send` only from `Authority._transmit` |
| B2, B5, 5, B6 | as r1 | as r1 (B2 and B5 are called from inside `_transmit` at the same points) |

R2A's first-dispatch code path therefore differs from r1 (one grant and one marker are written between B1 and event 4), although its journal format is unchanged.

## 3. Recovery: every path that can create an external effect

| Path | r1 EBRQ (`agent_v1.Recovery.run_ebr`) | r2 EBRQ-v2 (`agent_v2.RecoveryV2.run_ebrq_v2`) |
|---|---|---|
| Queue worker after a pre-dispatch classification | `new_send(PRE_DISPATCH)`; no independent check | `gated_send("PRE_DISPATCH")`: the authority checks, from durable state only, that no dispatch evidence exists (journal DBC, consumed marker or any outcome), that the evidence is valid, that queue responsibility is pending and that generation = 0 |
| PRE_DISPATCH denied because valid evidence shows a possible dispatch | — | rerouted to the unknown-outcome route below (itself gated); otherwise fail-closed |
| Unknown outcome, P1/P3 | `fence()` → if negative and re-classified CONFIRMED → `new_send(FENCED_NEGATIVE)` | `authority.fence()` (gated: valid evidence, dispatch evidence, contract offers fence, no acceptance) → durable fence record → if negative and re-classified CONFIRMED → `gated_send("FENCED_NEGATIVE")`: the fence must be durable, for this key, negative, with `retry_generation` = request generation, and not followed by any consumed marker (one fence authorizes at most one send) |
| Unknown outcome, P2 | `new_send(IDEMPOTENT)` | `gated_send("IDEMPOTENT")`: contract offers idempotency, request key = bound key = original dispatch key, generation 0, no acceptance |
| **CONFIRMED_NO_ACCEPTANCE_EVIDENCE (local failure)** | **`new_send(CLASSIFIED_DEFINITE_FAILURE)` on every contract (r1 line 247–249)** | **handled as unknown outcome**: P1/P3 fence, P2 idempotent, P0 `delivery_unknown`. No basis named after a local failure exists in the authority |
| After endpoint evidence: planner keeps queue responsibility | queue worker, ungated | `gated_send("PRE_DISPATCH")` (denied while dispatch evidence exists) |
| Any denial | — | durable denial record; task → `review` (invalid evidence) or `delivery_unknown` |

The v1 methods `fence`, `new_send`, `queue_worker`, `run_ebr`, `run_conv`, `run_stop`, `run_retry` are overridden in `RecoveryV2` to raise `PermissionError`. Tests confirm that they are unreachable, that `/send` and `/fence` appear only in `authority.py`, and that `_transmit` has exactly one call site.

Reference policies CONV, STOP and RETRY are **not** gated. They run only from `agent_v1.py`, unchanged.

## 4. Durable records and crash rules (authority/)

| Record | Meaning | Rule after a crash |
|---|---|---|
| `grants/<G>.json` | permission for one send attempt; no external action implied | without a consumed marker → voided at recovery start; never consumable afterwards |
| `consumed/<G>.json` (exclusive) | the grant was used; a send may have happened | the key counts as possibly dispatched |
| `void/<G>.json` | the grant can never be used | — |
| `fences/<n>.json` | durable copy of an endpoint fence answer | a negative fence authorizes at most one later send |
| `log/<n>-denial.json`, `log/<n>-result.json` | denials and recovery-send results | a COMMITTED result counts as acceptance |
| anomaly (marker without grant, grant/marker/DBC mismatch, void and consumed) | — | key frozen: no grant, no fence; task → review |

## 5. Changes made after tests exposed defects in the new code (2026-10-14)

1. A second recovery process could not start, because `ledger-access-recover.json` already existed. Later recoveries now write `ledger-access-recover-<n>.json`.
2. The reused v1 `endpoint_evidence` wrote `endpoint-evidence.json` without overwrite, which blocked a second recovery. RecoveryV2 now reimplements the same projection and writes a numbered file.
3. Send-attempt ids were numbered per recovery process and could repeat across processes. They are now numbered after all durable grants, and the authority refuses an id that any earlier grant used.
4. scorer_v2 originally accepted a send whenever some basis objectively existed. It now verifies the **claimed** basis, as the paper's S4 requires; "objective basis existed but was not claimed" is reported separately.
