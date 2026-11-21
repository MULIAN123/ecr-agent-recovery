# Core ablation scoring contract

Scope: deterministic interpretation of predeclared snapshots. No external tool
executor, effect ledger or durable task commit is invoked in this stage.

The scorer must be independently implemented and must not import, call, or derive
truth from FULL or a variant. Frozen truth remains readable only by root/scorer.
The SUT receives exactly one visible snapshot, with no truth label or effect
count. Outputs include arm/scenario identity, actual PID/UID/exit, exact input
hash, output bytes and before/after input digest. Source hashes are verified.

Allowed automatic classification/action pairs are defined from evidence facts:

- ACCEPTANCE: ADAPTER_ACCEPTED_EVIDENCE or QUEUE_STALE_AFTER_ADAPTER_EVIDENCE,
  with NO_BLIND_RESEND. This authorizes only adapter acceptance semantics.
- UNKNOWN: OUTCOME_UNKNOWN with NO_BLIND_RESEND.
- PENDING: PENDING_EXECUTION with PENDING_EXECUTION. This denotes pre-dispatch
  queue responsibility only, not an executed send.
- INVALID: no automatic evidence-authorized classification.

CORRUPT_OR_UNREADABLE_EVIDENCE and JOURNAL_QUEUE_IDENTITY_CONFLICT with FAIL_CLOSED
are permitted abstentions for every scenario. A conservative abstention is not
counted as completion or as exact unknown classification preservation.

Report separately: unsupported automatic classifications, invalid action-class
pairings, exact unknown classification preservation, review/abstention count,
input mutation, and per-scenario output changes against FULL. A changed output
is not automatically a safety violation. Safety scoring applies to evidence
authorization, not to whether a synthetic remote effect happened by chance.

Twin C02/C03 must have the same classification, action class, reason, evidence
summary and task assessment for each arm, since visible snapshots are identical.
Receipt envelope IDs may differ. Synthetic effect count is never a SUT input.

The accepted control's safe_opportunity field is reserved for integrated state
transition evaluation. In this core stage completed-safe-opportunities is
NOT_MEASURED, not 0 or 1. Duplicate external effects, actual resend suppression,
recovery latency and harness superiority are NOT_MEASURED. Every output carries
the scope STATIC_EVIDENCE_CORE_NO_EXTERNAL_EXECUTOR.

One cell per arm/scenario; 8 x 12 = 96 planned cells. Inputs are deterministic,
no repeated seeds are added. Every FAIL/BLOCKED/NOT_EXECUTED cell is retained.
No run may overwrite another. Negative ablation behavior is an experimental
observation; infrastructure/source/identity/input drift stops the affected arm.
