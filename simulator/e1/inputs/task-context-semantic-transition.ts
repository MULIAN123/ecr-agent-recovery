import type {
  ReconciliationExecutionIntent,
  ReconciliationExecutionPlan,
  ReconciliationExecutionBasis,
} from "./delivery-reconciliation-execution.js";

export type TaskSemanticState = "adapter_accepted" | "delivery_unknown";

export type TaskContextSemanticReference = ReconciliationExecutionBasis & {
  readonly classification: "ADAPTER_ACCEPTED_EVIDENCE" | "QUEUE_STALE_AFTER_ADAPTER_EVIDENCE" | "OUTCOME_UNKNOWN";
  readonly target: TaskSemanticState;
};

export type TaskContextSemanticSnapshot = {
  readonly taskId: string;
  readonly attemptId: string;
  readonly deliverableId: string;
  readonly artifactSetId: string;
  readonly artifactId: string;
  readonly bindingGeneration: number;
  readonly stateVersion: number;
  readonly deliveryState?: string;
  readonly semanticState?: TaskSemanticState;
  readonly semanticReference?: TaskContextSemanticReference;
  readonly taskContext?: Record<string, unknown>;
  readonly journalAttempt?: unknown;
  readonly queueItem?: Record<string, unknown> | null;
  readonly [key: string]: unknown;
};

export type SemanticTransitionResult =
  | { readonly decision: "APPLIED"; readonly nextSnapshot: TaskContextSemanticSnapshot }
  | { readonly decision: "ALREADY_APPLIED"; readonly nextSnapshot: TaskContextSemanticSnapshot }
  | { readonly decision: "FAIL_CLOSED"; readonly reason: string };

export type TaskContextSemanticTransitionInput = {
  readonly snapshot: TaskContextSemanticSnapshot;
  readonly plan: ReconciliationExecutionPlan;
  readonly intent: ReconciliationExecutionIntent;
};

const identifier = (value: unknown): value is string => typeof value === "string" && value.trim() !== "";
const version = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const same = (left: unknown, right: unknown): boolean => JSON.stringify(left) === JSON.stringify(right);

function fail(reason: string): SemanticTransitionResult {
  return { decision: "FAIL_CLOSED", reason };
}

function intentEqual(left: ReconciliationExecutionIntent, right: ReconciliationExecutionIntent): boolean {
  return same(left, right);
}

function validBasis(snapshot: TaskContextSemanticSnapshot, basis: ReconciliationExecutionBasis): boolean {
  const identity: Array<keyof ReconciliationExecutionBasis> = ["taskId", "attemptId", "deliverableId", "artifactSetId", "artifactId", "bindingGeneration"];
  if (!identity.every((key) => snapshot[key] === basis[key])) return false;
  if (!version(basis.expectedTaskStateVersion) || basis.expectedTaskStateVersion !== snapshot.stateVersion) return false;
  if (!Array.isArray(basis.evidenceEventIds) || basis.evidenceEventIds.length === 0) return false;
  if (basis.evidenceEventIds.some((eventId) => !identifier(eventId)) || new Set(basis.evidenceEventIds).size !== basis.evidenceEventIds.length) return false;
  if (basis.queueBindingId !== undefined && !identifier(basis.queueBindingId)) return false;
  if (basis.queueItemId !== undefined && !identifier(basis.queueItemId)) return false;
  const taskContext = snapshot.taskContext;
  if (taskContext) {
    for (const key of ["taskId", "attemptId", "deliverableId", "artifactSetId", "artifactId", "bindingGeneration"] as const) {
      if (taskContext[key] !== undefined && taskContext[key] !== basis[key]) return false;
    }
  }
  if (basis.queueBindingId !== undefined && snapshot.queueItem && snapshot.queueItem.queueBindingId !== basis.queueBindingId) return false;
  if (basis.queueItemId !== undefined && snapshot.queueItem && snapshot.queueItem.queueItemId !== basis.queueItemId) return false;
  return true;
}

function compatible(classification: ReconciliationExecutionPlan["classification"], target: TaskSemanticState): boolean {
  return (target === "adapter_accepted" && (classification === "ADAPTER_ACCEPTED_EVIDENCE" || classification === "QUEUE_STALE_AFTER_ADAPTER_EVIDENCE"))
    || (target === "delivery_unknown" && classification === "OUTCOME_UNKNOWN");
}

function exactReplay(snapshot: TaskContextSemanticSnapshot, reference: TaskContextSemanticReference): boolean {
  return snapshot.stateVersion === reference.expectedTaskStateVersion + 1
    && snapshot.semanticState === reference.target
    && same(snapshot.semanticReference, reference);
}

export function applyTaskContextSemanticTransition(input: TaskContextSemanticTransitionInput): SemanticTransitionResult {
  const { snapshot, plan, intent } = input;
  if (!snapshot || !plan || !intent || plan.decision !== "PLANNED") return fail("Only a PLANNED reconciliation plan may be applied.");
  if (!plan.orderedIntents.some((candidate) => intentEqual(candidate, intent))) return fail("The transition intent is not contained in the supplied plan.");
  if (intent.type !== "TRANSITION_TASK_SEMANTIC_STATE") return fail("Only semantic state transition intents are accepted.");
  const basis = plan.basis;
  if (!basis || !compatible(plan.classification, intent.target) || !validBasis(snapshot, basis)) return fail("Version, identity, projection, or evidence fencing failed.");
  const reference: TaskContextSemanticReference = { ...basis, classification: plan.classification, target: intent.target } as TaskContextSemanticReference;
  if (snapshot.semanticState !== undefined || snapshot.semanticReference !== undefined) {
    if (!snapshot.semanticState || !snapshot.semanticReference) return fail("Semantic reference is partial.");
    if (exactReplay(snapshot, reference)) return { decision: "ALREADY_APPLIED", nextSnapshot: snapshot };
    return fail("Existing semantic reference conflicts with the requested transition.");
  }
  const nextSnapshot: TaskContextSemanticSnapshot = {
    ...snapshot,
    stateVersion: snapshot.stateVersion + 1,
    semanticState: intent.target,
    semanticReference: reference,
  };
  return { decision: "APPLIED", nextSnapshot };
}
