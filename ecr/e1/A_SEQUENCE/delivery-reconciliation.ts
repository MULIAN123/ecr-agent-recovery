export type ReconciliationClassification =
  | "CORRUPT_OR_UNREADABLE_EVIDENCE"
  | "JOURNAL_QUEUE_IDENTITY_CONFLICT"
  | "LEGACY_UNQUALIFIED"
  | "QUEUE_STALE_AFTER_ADAPTER_EVIDENCE"
  | "ADAPTER_ACCEPTED_EVIDENCE"
  | "CONFIRMED_NO_ACCEPTANCE_EVIDENCE"
  | "OUTCOME_UNKNOWN"
  | "PENDING_EXECUTION"
  | "QUEUE_ITEM_WITHOUT_JOURNAL_CONFIRMATION"
  | "QUEUE_BINDING_PREPARED_BUT_QUEUE_MISSING"
  | "ATTEMPT_ONLY"
  | "UNSUPPORTED_SNAPSHOT";

export type DeliveryReconciliationResult = {
  readonly classification: ReconciliationClassification;
  readonly evidenceSummary: readonly string[];
  readonly permittedActionClass: "FAIL_CLOSED" | "LEGACY_OUT_OF_SCOPE" | "NO_BLIND_RESEND" | "FUTURE_STRATEGY_ONLY" | "PENDING_EXECUTION" | "NO_EXECUTION";
  readonly reason: string;
  readonly taskContextAssessment: "CONSISTENT" | "SEMANTIC_STATE_LAGGING" | "SEMANTIC_STATE_CONFLICT";
};

type Evidence = Record<string, unknown>;
type Snapshot = { taskContext?: Evidence; journalAttempt?: { events?: unknown; unreadable?: unknown }; queueItem?: Evidence | null };
const eventRank: Record<string, number> = { AttemptCommitted: 1, QueueBindingPrepared: 2, QueueItemPersisted: 3, DispatchBoundaryCrossed: 4, AdapterOutcomeObserved: 5 };
const identityKeys = ["attemptId", "taskId", "deliverableId", "artifactSetId", "artifactId", "bindingGeneration", "taskStateVersion", "queueItemId"] as const;

const isRecord = (value: unknown): value is Evidence => typeof value === "object" && value !== null && !Array.isArray(value);
const result = (classification: ReconciliationClassification, permittedActionClass: DeliveryReconciliationResult["permittedActionClass"], reason: string, summary: readonly string[], taskContextAssessment: DeliveryReconciliationResult["taskContextAssessment"] = "CONSISTENT"): DeliveryReconciliationResult => ({ classification, evidenceSummary: summary, permittedActionClass, reason, taskContextAssessment });
const eventsSummary = (events: readonly Evidence[]) => events.map((event) => `${event.journalSequence}:${event.eventType}`);

function validateEvidence(events: readonly Evidence[], unreadable: boolean): string | undefined {
  if (unreadable) return "journal_unreadable";
  let previousSequence = 0;
  let previousRank = 0;
  const seen = new Set<string>();
  for (const entry of events) {
    const type = entry.eventType;
    const sequence = entry.journalSequence;
    if (typeof type !== "string" || eventRank[type] === undefined || !Number.isSafeInteger(sequence)) return "invalid_event_or_sequence";
    if (eventRank[type] < previousRank || seen.has(type)) return "impossible_event_order";
    if (typeof entry.taskId !== "string" || typeof entry.attemptId !== "string" || typeof entry.deliverableId !== "string" || typeof entry.artifactSetId !== "string" || typeof entry.artifactId !== "string" || !Number.isSafeInteger(entry.bindingGeneration) || !Number.isSafeInteger(entry.taskStateVersion)) return "incomplete_durable_identity";
    previousSequence = sequence;
    previousRank = eventRank[type];
    seen.add(type);
  }
  if (events.length > 0 && events[0]!.eventType !== "AttemptCommitted") return "attempt_missing_before_later_evidence";
  if (seen.has("QueueItemPersisted") && !seen.has("QueueBindingPrepared")) return "queue_item_without_binding";
  if (seen.has("DispatchBoundaryCrossed") && seen.has("QueueBindingPrepared") && !seen.has("QueueItemPersisted")) return "dispatch_without_queue_item";
  if (seen.has("AdapterOutcomeObserved") && !seen.has("DispatchBoundaryCrossed")) return "outcome_without_dispatch";
  return undefined;
}

function hasIdentityConflict(taskContext: Evidence | undefined, events: readonly Evidence[], queueItem: Evidence | null | undefined): boolean {
  const facts = [taskContext, ...events, queueItem].filter((value): value is Evidence => isRecord(value));
  return identityKeys.some((key) => new Set(facts.map((fact) => fact[key]).filter((value) => value !== undefined).map(String)).size > 1);
}

function semanticAssessment(taskContext: Evidence | undefined, queueItem: Evidence | null | undefined, accepted: boolean): DeliveryReconciliationResult["taskContextAssessment"] {
  if (!accepted) return "CONSISTENT";
  if (taskContext?.deliveryState === "failed") return "SEMANTIC_STATE_CONFLICT";
  if (taskContext?.deliveryState === "pending" || queueItem?.pending === true) return "SEMANTIC_STATE_LAGGING";
  return "CONSISTENT";
}

export function reconcileDeliveryState(value: unknown): DeliveryReconciliationResult {
  if (!isRecord(value)) return result("CORRUPT_OR_UNREADABLE_EVIDENCE", "FAIL_CLOSED", "The durable snapshot is unreadable.", [], "CONSISTENT");
  const snapshot = value as Snapshot;
  const journal = snapshot.journalAttempt;
  if (!isRecord(journal) || !Array.isArray(journal.events) || !journal.events.every(isRecord)) return result("CORRUPT_OR_UNREADABLE_EVIDENCE", "FAIL_CLOSED", "The journal attempt evidence is unreadable.", [], "CONSISTENT");
  const events = journal.events;
  const summary = eventsSummary(events);
  const corrupt = validateEvidence(events, journal.unreadable === true);
  if (corrupt) return result("CORRUPT_OR_UNREADABLE_EVIDENCE", "FAIL_CLOSED", corrupt, summary, "CONSISTENT");
  if (hasIdentityConflict(isRecord(snapshot.taskContext) ? snapshot.taskContext : undefined, events, isRecord(snapshot.queueItem) ? snapshot.queueItem : null)) return result("JOURNAL_QUEUE_IDENTITY_CONFLICT", "FAIL_CLOSED", "Durable identity evidence conflicts.", summary, "CONSISTENT");
  if (isRecord(snapshot.queueItem) && snapshot.queueItem.legacy === true) return result("LEGACY_UNQUALIFIED", "LEGACY_OUT_OF_SCOPE", "The queue item lacks research correlation.", summary, "CONSISTENT");

  const has = (type: string) => events.some((event) => event.eventType === type);
  const outcome = events.find((event) => event.eventType === "AdapterOutcomeObserved");
  const accepted = outcome?.outcome === "adapter_accepted" && outcome.evidenceLevel === "adapter_accepted";
  const assessment = semanticAssessment(isRecord(snapshot.taskContext) ? snapshot.taskContext : undefined, isRecord(snapshot.queueItem) ? snapshot.queueItem : null, accepted);
  if (accepted && isRecord(snapshot.queueItem) && snapshot.queueItem.pending === true) return result("QUEUE_STALE_AFTER_ADAPTER_EVIDENCE", "NO_BLIND_RESEND", "Adapter acceptance evidence outranks pending queue responsibility.", summary, assessment);
  if (accepted) return result("ADAPTER_ACCEPTED_EVIDENCE", "NO_BLIND_RESEND", "Adapter acceptance is not end-user receipt evidence.", summary, assessment);
  if (outcome?.outcome === "definite_failure") return result("CONFIRMED_NO_ACCEPTANCE_EVIDENCE", "FUTURE_STRATEGY_ONLY", "Definite pre-acceptance failure evidence is present.", summary, "CONSISTENT");
  if (has("DispatchBoundaryCrossed")) return result("OUTCOME_UNKNOWN", "NO_BLIND_RESEND", "Dispatch crossed the boundary without an observed adapter outcome.", summary, "CONSISTENT");
  if (isRecord(snapshot.queueItem) && !has("QueueItemPersisted")) return result("QUEUE_ITEM_WITHOUT_JOURNAL_CONFIRMATION", "FAIL_CLOSED", "Queue item lacks QueueItemPersisted evidence.", summary, "CONSISTENT");
  if (has("QueueItemPersisted") && isRecord(snapshot.queueItem)) return result("PENDING_EXECUTION", "PENDING_EXECUTION", "Queue item is durably persisted before dispatch.", summary, "CONSISTENT");
  if (has("QueueBindingPrepared")) return result("QUEUE_BINDING_PREPARED_BUT_QUEUE_MISSING", "NO_EXECUTION", "Queue binding is prepared but no queue item is present.", summary, "CONSISTENT");
  if (has("AttemptCommitted")) return result("ATTEMPT_ONLY", "NO_EXECUTION", "Only durable attempt evidence is present.", summary, "CONSISTENT");
  return result("UNSUPPORTED_SNAPSHOT", "NO_EXECUTION", "No supported reconciliation classification applies.", summary, "CONSISTENT");
}
