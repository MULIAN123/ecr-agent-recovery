# Experiment 3 (`dsh-20261013-r1`): DeepSeek Harness recovery comparison, preregistration

Written 2026-10-13 before the formal batch. The researcher approved scripted model policies now (a real-model extension may follow later with separate cost approval) and running the installed DSH on the host in an isolated `DSH_HOME`. The exploratory probes that motivated this design are disclosed in `../probe/PROBE_RESULTS.md`.

## Question
In an unmodified agent runtime, does crash recovery after a side-effecting tool call duplicate the effect? Which part of the evidence-constrained contract removes the duplication? (D0→D1: durable dispatch evidence; D1→D2: authorization rule.) What does it cost (missed completions, added latency)?

## System under test
Installed DeepSeek Harness desktop `0.2.0-rc.2`, `app.asar` SHA-256 `983ca711…`, executable SHA-256 `df4e91fd…`, both re-checked before the batch, every 21 trials and at the end; any change stops the batch. Minimal DSH plugin tree from DSH's own packages (`profile/cordis.patch.yml`), JSONL persistence with `compression: none`, run headless via the bundled CLI (`ELECTRON_RUN_AS_NODE`). No DSH file is modified.

## Workload (identical across arms)
A durable warm-up turn, then the user asks the agent to send the message for one logical operation. A scripted model (no network, no API) waits 500 ms, calls `send_message`, and waits 2000 ms before answering a tool result. The tool appends one effect record with fsync to an external ledger file outside the DSH store. It is either *fast* (returns at once) or *slow* (returns 1000 ms after the effect). After the hard kill, a resume run reopens the session (DSH appends its own recovery closers) and sends "Continue.".

Model policies (explicit assumptions, not claims about DeepSeek models):
- *obedient*: follows DSH's recovery guidance. It acts on a durable request that has no tool call after it, retries `TOOL_NOT_STARTED`, and does not retry `TOOL_OUTCOME_UNKNOWN`.
- *naive*: also retries any interrupted call.

## Arms
- **D0** unmodified DSH.
- **D1** D0 + `durable-dispatch` plugin: awaits DSH's own `sessions.flush` in `tools/pre-execute` for side-effecting tools, so the `tool/call` record is durable before the body runs.
- **D2** D1 + `evidence-guard` plugin: a monotonic DSH tool guard denies a send for a logical operation that already has a durable call without a successful result (`OUTCOME_UNKNOWN`) or with a successful result. This endpoint offers no fenced query or idempotency, so no resend is authorized.

## Kill points and design
`pre`: tool body started, effect not yet done (100 ms pre-effect hold, kill 20 ms after its marker). Post-effect: 0, 100, 150, 250, 400, 1500 ms after the effect record. Process termination is `TerminateProcess` (hard crash). 3 arms × 2 policies × 2 tools × 7 kill points × 3 repetitions = 252 trials, in the order fixed by `manifest.json` (SHA-256 `ef1309d0…`), arms interleaved within each cell.

## Truth and metrics
Truth comes from the external ledger: effects present at kill time (0 for `pre`, 1 for post-effect) and at the end. Per trial:
- duplicate: final effects > 1;
- completed: exactly 1;
- missed completion: 0 (only possible for `pre`);
- DSH durable state at kill (request/call/result durable or not);
- DSH recovery code (`TOOL_OUTCOME_UNKNOWN` / `TOOL_NOT_STARTED`);
- guard denials;
- added flush latency (D1/D2).

`score_formal.py` reads only retained raw artifacts (ledger, session logs copied at kill and after resume, latency log).

## Pre-stated expectations (not truth labels)
Post-effect kills in the batching window (~0–200 ms) lose the call record in D0, so even obedient recovery duplicates. D1 removes these for obedient but not for naive. D2 removes all post-effect duplicates. For `pre`, D0 may complete by re-sending after a lost record, whereas D1/D2 report outcome unknown and leave it uncompleted (a conservative cost). All outcomes are reported, including costs.

## Rules
Trials with an infrastructure failure (no marker, process exited before kill, resume failure or timeout) stop the batch as `FAIL_RETAIN_EVIDENCE`. No automatic retries, no overwrite (fresh `DSH_HOME`).

## Isolation and limits
The SUT and the scorer run as the same Windows user on the host; the ledger is outside the DSH store and is never read by DSH or the plugins (code-level isolation, weaker than the OS-identity isolation of experiments 1–2). The workload is scripted, so model behaviour after recovery is an assumption. Results hold for this DSH build and this persistence configuration only.
