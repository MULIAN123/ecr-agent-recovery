import {
  reconcileDeliveryState,
  type DeliveryReconciliationResult,
  type ReconciliationClassification,
} from "./delivery-reconciliation.js";

type Evidence = Record<string, unknown>;

export type DeliveryReconciliationSnapshot = {
  readonly taskContext?: Evidence;
  readonly journalAttempt?: { readonly events?: unknown; readonly unreadable?: unknown };
  readonly queueItem?: Evidence | null;
};

export type QueueBindingProjectionView =
  | {
      readonly kind: "DISCOVERED";
      readonly preparationState: "PROJECTED_ONLY" | "FULLY_PREPARED";
      readonly queueBinding: {
        readonly schemaVersion: 1;
        readonly correlationId: string;
        readonly opaqueMetadata: {
          readonly queueBindingId: string;
          readonly attempts: readonly QueueBindingAttemptReference[];
        };
      };
    }
  | { readonly kind: "NO_RESEARCH_PROJECTION" }
  | { readonly kind: "PROJECTION_CONFLICT" }
  | { readonly kind: "PARTIAL_PREPARED" };

export type QueueBindingAttemptReference = {
  readonly taskId: string;
  readonly attemptId: string;
  readonly deliverableId: string;
  readonly artifactSetId: string;
  readonly artifactId: string;
  readonly bindingGeneration: number;
  readonly taskStateVersion: number;
};

export type ReconciliationExecutionBasis = {
  readonly taskId: string;
  readonly attemptId: string;
  readonly deliverableId: string;
  readonly artifactSetId: string;
  readonly artifactId: string;
  readonly bindingGeneration: number;
  readonly expectedTaskStateVersion: number;
  readonly queueBindingId?: string;
  readonly queueItemId?: string;
  readonly evidenceEventIds: readonly string[];
};

export type ReconciliationExecutionIntent =
  | { readonly type: "TRANSITION_TASK_SEMANTIC_STATE"; readonly target: "adapter_accepted" | "delivery_unknown" }
  | { readonly type: "RECORD_RECONCILIATION_APPLIED" }
  | { readonly type: "PREPARE_DURABLE_QUEUE_BINDING"; readonly queueBindingId: string }
  | { readonly type: "RECREATE_QUEUE_RESPONSIBILITY"; readonly queueBindingId: string; readonly operationKey: string }
  | { readonly type: "PRESERVE_EXISTING_QUEUE_RESPONSIBILITY"; readonly queueItemId: string }
  | { readonly type: "RELEASE_STALE_QUEUE_RESPONSIBILITY"; readonly queueItemId: string }
  | { readonly type: "QUARANTINE_QUEUE_RESPONSIBILITY"; readonly queueItemId?: string }
  | { readonly type: "REQUIRE_REVIEW" };

export type ReconciliationExecutionPlan = {
  readonly decision: "PLANNED" | "FAIL_CLOSED" | "NO_ACTION";
  readonly classification: ReconciliationClassification;
  readonly basis?: ReconciliationExecutionBasis;
  readonly orderedIntents: readonly ReconciliationExecutionIntent[];
  readonly externalEffectPermission: "FORBIDDEN_IN_THIS_INCREMENT";
  readonly reason: string;
};

export type ReconciliationExecutionPlanInput = {
  readonly snapshot: DeliveryReconciliationSnapshot;
  readonly projection: QueueBindingProjectionView | unknown;
};

const identityKeys = ["taskId", "attemptId", "deliverableId", "artifactSetId", "artifactId", "bindingGeneration", "taskStateVersion"] as const;

const isRecord = (value: unknown): value is Evidence => typeof value === "object" && value !== null && !Array.isArray(value);
const isIdentifier = (value: unknown): value is string => typeof value === "string" && value.trim() !== "";
const isSafeVersion = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;

function fail(classification: ReconciliationClassification, reason: string, intent?: ReconciliationExecutionIntent): ReconciliationExecutionPlan {
  return {
    decision: "FAIL_CLOSED",
    classification,
    orderedIntents: [intent ?? { type: "REQUIRE_REVIEW" }],
    externalEffectPermission: "FORBIDDEN_IN_THIS_INCREMENT",
    reason,
  };
}

function noAction(classification: ReconciliationClassification, reason: string): ReconciliationExecutionPlan {
  return {
    decision: "NO_ACTION",
    classification,
    orderedIntents: [],
    externalEffectPermission: "FORBIDDEN_IN_THIS_INCREMENT",
    reason,
  };
}

function planned(classification: ReconciliationClassification, basis: ReconciliationExecutionBasis, intents: readonly ReconciliationExecutionIntent[], reason: string): ReconciliationExecutionPlan {
  return {
    decision: "PLANNED",
    classification,
    basis,
    orderedIntents: intents,
    externalEffectPermission: "FORBIDDEN_IN_THIS_INCREMENT",
    reason,
  };
}

function eventsFor(snapshot: DeliveryReconciliationSnapshot): readonly Evidence[] | undefined {
  const events = snapshot.journalAttempt?.events;
  return Array.isArray(events) && events.every(isRecord) ? events : undefined;
}

function projectionState(value: unknown): { readonly preparationState: "PROJECTED_ONLY" | "FULLY_PREPARED"; readonly queueBindingId: string; readonly attempts: readonly QueueBindingAttemptReference[] } | undefined {
  if (!isRecord(value) || value.kind !== "DISCOVERED" || (value.preparationState !== "PROJECTED_ONLY" && value.preparationState !== "FULLY_PREPARED") || !isRecord(value.queueBinding) || value.queueBinding.schemaVersion !== 1 || !isIdentifier(value.queueBinding.correlationId) || !isRecord(value.queueBinding.opaqueMetadata) || !isIdentifier(value.queueBinding.opaqueMetadata.queueBindingId) || value.queueBinding.correlationId !== value.queueBinding.opaqueMetadata.queueBindingId || !Array.isArray(value.queueBinding.opaqueMetadata.attempts)) return undefined;
  const attempts = value.queueBinding.opaqueMetadata.attempts;
  if (!attempts.every(isRecord)) return undefined;
  return { preparationState: value.preparationState, queueBindingId: value.queueBinding.opaqueMetadata.queueBindingId, attempts: attempts as readonly QueueBindingAttemptReference[] };
}

function basisFor(snapshot: DeliveryReconciliationSnapshot, projection: unknown): ReconciliationExecutionBasis | undefined {
  const events = eventsFor(snapshot);
  const taskContext = snapshot.taskContext;
  if (!events || events.length === 0 || !isRecord(taskContext)) return undefined;
  const attempt = events.find((entry) => entry.eventType === "AttemptCommitted");
  if (!attempt) return undefined;
  if (!identityKeys.every((key) => (key === "bindingGeneration" || key === "taskStateVersion" ? isSafeVersion(attempt[key]) && attempt[key] === taskContext[key] : isIdentifier(attempt[key]) && attempt[key] === taskContext[key]))) return undefined;
  const eventIds = [...events].sort((left, right) => Number(left.journalSequence) - Number(right.journalSequence)).map((entry) => entry.eventId);
  if (eventIds.length === 0 || eventIds.some((eventId) => !isIdentifier(eventId)) || new Set(eventIds).size !== eventIds.length) return undefined;
  if (events.some((entry) => !identityKeys.every((key) => entry[key] === attempt[key]))) return undefined;
  const state = projectionState(projection);
  if (!state || !isIdentifier(attempt.queueBindingId) || state.queueBindingId !== attempt.queueBindingId) return undefined;
  const matching = state.attempts.filter((reference) => reference.attemptId === attempt.attemptId);
  if (matching.length !== 1 || !identityKeys.every((key) => matching[0]![key] === attempt[key])) return undefined;
  const queueItemId = isIdentifier(snapshot.queueItem?.queueItemId) ? snapshot.queueItem.queueItemId : (events.find((entry) => isIdentifier(entry.queueItemId))?.queueItemId as string | undefined);
  if (snapshot.queueItem && (!isIdentifier(snapshot.queueItem.queueBindingId) || snapshot.queueItem.queueBindingId !== state.queueBindingId)) return undefined;
  return {
    taskId: attempt.taskId as string,
    attemptId: attempt.attemptId as string,
    deliverableId: attempt.deliverableId as string,
    artifactSetId: attempt.artifactSetId as string,
    artifactId: attempt.artifactId as string,
    bindingGeneration: attempt.bindingGeneration as number,
    expectedTaskStateVersion: attempt.taskStateVersion as number,
    queueBindingId: state.queueBindingId,
    ...(queueItemId === undefined ? {} : { queueItemId }),
    evidenceEventIds: eventIds as readonly string[],
  };
}

function requiresFullProjection(classification: ReconciliationClassification): boolean {
  return classification === "QUEUE_STALE_AFTER_ADAPTER_EVIDENCE" || classification === "ADAPTER_ACCEPTED_EVIDENCE" || classification === "OUTCOME_UNKNOWN" || classification === "PENDING_EXECUTION" || classification === "QUEUE_BINDING_PREPARED_BUT_QUEUE_MISSING";
}

export function planDeliveryReconciliationExecution(input: ReconciliationExecutionPlanInput): ReconciliationExecutionPlan {
  const reconciliation: DeliveryReconciliationResult = reconcileDeliveryState(input.snapshot);
  const { classification } = reconciliation;
  if (isRecord(input.projection) && (input.projection.kind === "PROJECTION_CONFLICT" || input.projection.kind === "PARTIAL_PREPARED")) return fail(classification, "Queue-binding projection is not safe for execution planning.");
  if (classification === "CORRUPT_OR_UNREADABLE_EVIDENCE" || classification === "JOURNAL_QUEUE_IDENTITY_CONFLICT") return fail(classification, "Durable reconciliation evidence requires review.");
  if (classification === "LEGACY_UNQUALIFIED") return noAction(classification, "Legacy direct evidence remains outside the strong execution protocol.");
  if (classification === "CONFIRMED_NO_ACCEPTANCE_EVIDENCE") return noAction(classification, "Definite failure remains future policy and does not authorize retry.");
  if (classification === "UNSUPPORTED_SNAPSHOT") return noAction(classification, "No supported execution plan applies to this snapshot.");
  if (classification === "QUEUE_ITEM_WITHOUT_JOURNAL_CONFIRMATION") {
    const queueItemId = isIdentifier(input.snapshot.queueItem?.queueItemId) ? input.snapshot.queueItem.queueItemId : undefined;
    return fail(classification, "Queue responsibility lacks durable journal confirmation.", { type: "QUARANTINE_QUEUE_RESPONSIBILITY", ...(queueItemId === undefined ? {} : { queueItemId }) });
  }
  const state = projectionState(input.projection);
  if (!state) return fail(classification, "Strong reconciliation requires a valid durable queue-binding projection.");
  if ((classification === "ATTEMPT_ONLY" && state.preparationState !== "PROJECTED_ONLY") || (classification === "QUEUE_BINDING_PREPARED_BUT_QUEUE_MISSING" && state.preparationState !== "FULLY_PREPARED") || (requiresFullProjection(classification) && state.preparationState !== "FULLY_PREPARED")) return fail(classification, "Projection preparation state is incompatible with reconciliation classification.");
  const basis = basisFor(input.snapshot, input.projection);
  if (!basis) return fail(classification, "Execution basis is missing, inconsistent, or lacks unique durable event IDs.");
  if (classification === "ATTEMPT_ONLY") return planned(classification, basis, [{ type: "PREPARE_DURABLE_QUEUE_BINDING", queueBindingId: basis.queueBindingId! }, { type: "RECREATE_QUEUE_RESPONSIBILITY", queueBindingId: basis.queueBindingId!, operationKey: basis.queueBindingId! }], "Projected attempt has no prepared queue evidence.");
  if (classification === "QUEUE_BINDING_PREPARED_BUT_QUEUE_MISSING") return planned(classification, basis, [{ type: "RECREATE_QUEUE_RESPONSIBILITY", queueBindingId: basis.queueBindingId!, operationKey: basis.queueBindingId! }], "Prepared durable binding has no queue responsibility.");
  if (classification === "PENDING_EXECUTION") {
    if (!basis.queueItemId) return fail(classification, "Pending queue responsibility lacks a durable queue item identity.");
    return planned(classification, basis, [{ type: "PRESERVE_EXISTING_QUEUE_RESPONSIBILITY", queueItemId: basis.queueItemId }], "Pending queue responsibility is preserved without adapter execution.");
  }
  if (classification === "OUTCOME_UNKNOWN") return planned(classification, basis, [{ type: "TRANSITION_TASK_SEMANTIC_STATE", target: "delivery_unknown" }, { type: "RECORD_RECONCILIATION_APPLIED" }], "Unknown outcome permits only a conservative semantic transition plan.");
  if (classification === "ADAPTER_ACCEPTED_EVIDENCE") return planned(classification, basis, [{ type: "TRANSITION_TASK_SEMANTIC_STATE", target: "adapter_accepted" }, { type: "RECORD_RECONCILIATION_APPLIED" }], "Adapter acceptance is planned without any receipt claim.");
  if (classification === "QUEUE_STALE_AFTER_ADAPTER_EVIDENCE") {
    if (!basis.queueItemId) return fail(classification, "Stale queue responsibility lacks a durable queue item identity.");
    return planned(classification, basis, [{ type: "TRANSITION_TASK_SEMANTIC_STATE", target: "adapter_accepted" }, { type: "RECORD_RECONCILIATION_APPLIED" }, { type: "RELEASE_STALE_QUEUE_RESPONSIBILITY", queueItemId: basis.queueItemId }], "Adapter acceptance precedes release of stale queue responsibility.");
  }
  return noAction(classification, reconciliation.reason);
}
