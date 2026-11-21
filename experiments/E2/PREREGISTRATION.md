# Experiment 2: asynchronous fault simulation, preregistration (`async-sim-20261012-r1`)

Written 2026-10-12 after the truth freeze (`TRUTH_FREEZE.md`) and harness smoke tests, before the sudo qualification `async-sim-qual-20261012-r1` and before any formal cell. The researcher approved running both the original mechanism and the query extension and asked the operator (Claude Code) to proceed.

## Question
Under identical crash points and identical endpoint capabilities, how do recovery policies compare on (1) duplicate external effects, (2) unauthorized effects, (3) false completion (task claims acceptance while no effect exists), (4) contract-invalid ("false-safe") send authorizations, (5) completion of predeclared safe recovery opportunities, and (6) unresolved cases left for review?

## Platform
Built on the platform-qualified `qualification_v2` mechanics (12/12 PASS): three OS identities (`ecr-sut` agent, `ecr-adapter` endpoint and ledger, `ecr-scorer`), durable barrier receipts, real `SIGKILL` of the agent at the barrier, and an endpoint with an authoritative fenced query (P1/P3: after a fence, old generations are rejected and a retry must use the fenced generation) and logical-operation idempotency (P2/P3). The endpoint is derived from `qualification_v2/adapter.py` with contract semantics unchanged. Changes: only the original transport request is gated; it can be dropped in transit or released late; all sends, queries and fences are logged. `protocol.py` is byte-identical (SHA-256 `ca67293c…`).

## Scenarios (52)
- Crash points × endpoint profiles (32): B1 (intent persisted, before dispatch), B2_DROP (request left the client, lost), B2_LATE (left the client, arrives after recovery), B2_ARRIVE (left the client, arrives and commits before recovery), B3_LATE (accepted by the service, commits after recovery unless fenced), B4 (effect committed, no response), B5 (response received, not persisted), B6 (outcome persisted, queue/task not updated) × P0 (no query, no idempotency), P1 (fenced query), P2 (idempotency), P3 (both).
- Local-evidence overlays × profiles (20), applied to the agent's durable store after the kill and before recovery: F2 foreign-attempt acceptance record (base B2_DROP); F3 transport-level acknowledgement only (B2_DROP); F4 genuine callback persisted with a stale sequence number (B5); F5 task re-planned to a newer version while the old item is still queued (B1); F6 dispatch record lost and a spurious failure callback recorded (B5).

## Truth (frozen, `private/cells.json` SHA-256 `b7f66c55…`)
From scenario construction and the endpoint contract only: `dispatched`; `max_allowed_effects` (0 for F5, else 1); `send_permitted` (false for F5); `safe_opportunity`: B1 and authentic B6 for all profiles; all other crash points and F2/F3/F4/F6 only where a contract mechanism exists (P1, P2, P3); never for F5.

## Arms (8)
| Arm | Definition |
|---|---|
| EBR | frozen classifier (FULL) + frozen execution planner; intents executed; pending queue items dispatched by the queue worker; no endpoint query |
| EBRQ | EBR + declared query extension: for `OUTCOME_UNKNOWN` only, fence (P1/P3) and hand the endpoint outcome to the same classifier as an outcome record that supersedes a missing or weaker local record; resend only on a fenced negative classified `CONFIRMED_NO_ACCEPTANCE_EVIDENCE` (fenced generation), or under server idempotency (P2); classifier-confirmed definite failure also authorizes a resend |
| CONV | conventional strong baseline: trusts recorded outcomes and pending queue items without identity/version/order validation; otherwise fence first (P1/P3), idempotent resend (P2), resend on a recorded failure (P0), else unknown |
| STOP | anything with dispatch/outcome evidence goes to review; pre-dispatch items continue |
| RETRY | recorded acceptance ⇒ done; otherwise resend |
| EBRQ_NO_UNKNOWN / EBRQ_NO_BOUNDARY / EBRQ_NO_LEVEL | EBRQ with the R2 ablated classifier sources A_UNKNOWN_PRESERVATION / A_OUTCOME_BOUNDARY / A_ACCEPTANCE_LEVEL |

The classifier and planner sources are byte-identical to `core_execution_r2` (hash-checked at build); the planner's `./delivery-reconciliation.js` import is resolved to the sibling `.ts` by a resolve hook in `plan.mjs`. 52 × 8 = 416 formal cells, one run each (deterministic schedules; no seeds).

## Scoring (independent `ecr-scorer` process; reads ledger, endpoint log, copied agent store, truth)
Per cell: effect count for the logical operation; duplicate (>1); unauthorized (> max allowed); final task state; true completion (accepted state with exactly 1 allowed effect); false completion (accepted state with 0 effects); unresolved; each `AUTHORIZE_SEND` judged contract-valid only if PRE_DISPATCH and truly not dispatched, FENCED_NEGATIVE on P1/P3 after a logged negative fence with the fenced generation, or IDEMPOTENT on P2/P3 (always invalid for F5); opportunity completed = safe opportunity ∧ true completion ∧ no invalid authorization ∧ no duplicate. Integrity faults (unrecorded send, fence record mismatch, foreign ledger effect, missing final state, ledger readable by the agent, injection receipt mismatch) are infrastructure failures.

## Analysis plan
Report per-arm integer counts of each metric over all 52 scenarios, and per scenario group (crash points by profile; each overlay), paired by scenario. No pooled score and no population inference. Pre-stated comparisons: EBRQ vs CONV (same capabilities), EBR vs EBRQ (value of the extension), EBRQ vs each integrated ablation (does the removed check change effects or completions?), and STOP/RETRY as references. Ties are reported as ties. Costs of EBR(Q) (for example missed opportunities when local evidence is defective but the effect is genuine, F4) are reported with the same prominence as benefits.

## Execution rules
`--mode qualify` (13 harness cells: 10 CONV/RETRY baseline cells with contract-derived harness expectations, 2 EBR accepted/pending controls, plus offline scorer fixtures for duplicate, false completion and unsafe authorization) → host audit → `qualification-authorization.json` → `--mode formal`. Any infrastructure or integrity fault stops the batch as `FAIL_RETAIN_EVIDENCE`; no automatic retry; no overwrite.

## Disclosures
- Harness development smoke tests were run as one unprivileged user without identity separation (`~/async-dev-a` in the guest): the 13 qualification cells (all matched their harness expectations) and 9 path checks (CONV on F2–F5 at P1, CONV on F6 at P1, STOP on F5 at P0, EBRQ on B4_P1 and B2_DROP_P1, EBRQ_NO_UNKNOWN on B6_P0). No code change followed them, and truth was not changed. Their outcomes are not results.
- Deviations from the Chinese design draft (`DESIGN_DRAFT_zh.md`): B2 is split into drop/late/arrive and B3 is run as late-commit (8 crash variants, 52 scenarios, 416 cells instead of about 40/320); overlay F1 (two concurrent recovery workers) is deferred because none of the implemented policies has a cross-process lock, so it would not discriminate between them; F6 (lost dispatch record + spurious failure) was added. Integrated ablations are applied to EBRQ, because the original EBR never resends, so false-failure and false-acceptance ablations would have no observable effect there.
- Scope: local simulation only; no real messaging endpoint, no model API.
