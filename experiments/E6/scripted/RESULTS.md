# Experiment 3 (`dsh-20261013-r1`): results

Batch run 2026-10-13 on the host, using the installed DeepSeek Harness `0.2.0-rc.2` in the isolated `DSH_HOME` `<WORKSPACE>/dsh-formal-home-20261013-r1`. It follows `dsh_r1/formal/PREREGISTRATION.md` and `manifest.json` (SHA-256 `ef1309d0…`).

- **Status:** `COMPLETED_REQUIRES_AUDIT`, then audited **PASS** (`formal-audit.json`).
- **Trials:** 252/252 OBSERVED, in manifest order. No infrastructure failures, no reruns, no exclusions.
- **Identity:** DSH `app.asar`, the executable and the package files matched the pinned hashes before the batch, every 21 trials, and after the batch (re-checked independently by the audit).
- **Kill timing:** the kill landed at most 1.4 ms later than the target in every trial; the median delay was 0.1–0.8 ms depending on the kill point.
- **Effects at kill:** the external ledger held 0 effects for every `pre` kill and 1 for every post-effect kill.
- **Scoring:** `score_formal.py` (reads only retained raw artifacts). Outputs are `score-report.json` and `per-trial-scores.csv`.
- **Audit script:** `<WORKSPACE>/audit_dsh_formal_r1.py`.

## Main table (per arm × model policy; 36 post-effect and 6 pre-effect trials each)

| Arm | Policy | Post-effect duplicates | Post-effect completed | Pre-effect completed | Pre-effect missed | Call record lost at kill | Guard denials |
|---|---|---|---|---|---|---|---|
| D0 unmodified | obedient | **18/36** | 18/36 | 6/6 | 0/6 | 24/42 | 0 |
| D0 unmodified | naive | **24/36** | 12/36 | 6/6 | 0/6 | 24/42 | 0 |
| D1 +durable dispatch | obedient | 0/36 | 36/36 | 0/6 | 6/6 | 0/42 | 0 |
| D1 +durable dispatch | naive | **24/36** | 12/36 | 6/6 | 0/6 | 0/42 | 0 |
| D2 +evidence guard | obedient | 0/36 | 36/36 | 0/6 | 6/6 | 0/42 | 0 |
| D2 +evidence guard | naive | 0/36 | 36/36 | 0/6 | 6/6 | 0/42 | 30 trials |

A "duplicate" means the external ledger shows two effects. "Completed" means exactly one effect. "Missed" means zero effects (possible only for `pre`).

## Duplicates by kill point (out of 6: 2 tools × 3 repetitions)

| Kill after effect | D0 obedient | D0 naive | D1 obedient | D1 naive | D2 obedient | D2 naive |
|---|---|---|---|---|---|---|
| pre | 0 | 0 | 0 | 0 | 0 | 0 |
| 0 ms | 6 | 6 | 0 | 6 | 0 | 0 |
| 100 ms | 6 | 6 | 0 | 6 | 0 | 0 |
| 150 ms | 6 | 6 | 0 | 6 | 0 | 0 |
| 250 ms | 0 | 3 | 0 | 3 | 0 | 0 |
| 400 ms | 0 | 3 | 0 | 3 | 0 | 0 |
| 1500 ms | 0 | 0 | 0 | 0 | 0 | 0 |

## Mechanism (from the per-trial durable state)

- **D0, kills at 0–150 ms after the effect.** For both tools, the session file at kill time contained the user request but **no `tool/call` record** (`CALL_NOT_DURABLE`, 18/18 per policy). That is DSH's 200 ms JSONL batching window.
  - On resume DSH has nothing to flag: it emits no `TOOL_OUTCOME_UNKNOWN` closer.
  - So a model that follows DSH's guidance faithfully (obedient) sees an unanswered request and sends again.
  - The duplicate is therefore caused by the runtime's lost dispatch evidence, not by model disobedience.
- **D0, kills at 250/400 ms.**
  - Fast tool: the result was already durable, so there was no duplicate.
  - Slow tool: the call was durable but the result was not. DSH correctly emitted `TOOL_OUTCOME_UNKNOWN`. The obedient model did not resend; the naive model did (3+3 duplicates).
- **D1.** Flushing before dispatch made the call durable in all 168 D1/D2 trials (0 lost call records).
  - Every kill inside the window now yields `TOOL_OUTCOME_UNKNOWN`.
  - Obedient recovery became duplicate-free.
  - A naive model still resends on the unknown outcome (24/36). Durable evidence alone does not stop a model from acting against it.
- **D2.** The guard turns the evidence into a runtime-enforced rule. It denied all 30 naive resends (24 post-effect, 6 pre-effect), and there were 0 duplicates in either policy.

## Costs (pre-registered)

- **Missed completions.** When the crash happened before the effect (`pre`), the outcome is genuinely unknown to the runtime.
  - D1-obedient, D2-obedient and D2-naive left the operation uncompleted (6/6 each) and reported `TOOL_OUTCOME_UNKNOWN`.
  - D0 "completed" these 12/12 only because the call record was lost and the request was re-executed. The same behaviour causes the 18–24 post-effect duplicates, because D0 cannot tell the two cases apart.
  - D1-naive also completed 6/6 by blind retry; that same rule produced 24 post-effect duplicates.
  - This endpoint offers no fenced query or idempotency key, so the contract cannot authorize a safe resend. In experiment 2 such evidence is what lets EBRQ recover these cases.
- **Latency.** The awaited `sessions.flush` before a side-effecting dispatch took:
  - median **1.28 ms**, p95 3.19 ms, max 19.8 ms;
  - n = 228 dispatches: 168 first dispatches plus 60 resumed dispatch attempts, including the 30 that the guard denied.

  This is small next to the 500 ms simulated model latency.

## Limits

- Model behaviour is scripted (two explicit policies), so these are runtime-mechanism results, not claims about DeepSeek models.
- Single host, single DSH build, JSONL persistence with `compression: none`.
- Isolation is at the code level only: the ledger is outside the DSH store and is never read by DSH or the plugins, but everything runs as the same OS user.
- Outcomes were fully deterministic across the 3 repetitions of each cell, so repetition adds robustness to timing jitter rather than statistical power.
