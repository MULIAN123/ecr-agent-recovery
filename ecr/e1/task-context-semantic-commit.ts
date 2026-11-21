import type { ReconciliationExecutionPlan } from "./delivery-reconciliation-execution.js";
import {
  type SemanticTransitionResult,
  type TaskContextSemanticReference,
  type TaskContextSemanticSnapshot,
} from "./task-context-semantic-transition.js";

export type TaskContextCommitStore = {
  load(taskId: string): Promise<TaskContextSemanticSnapshot | undefined>;
  replace(taskId: string, expectedVersion: number, next: TaskContextSemanticSnapshot): Promise<"COMMITTED" | "STALE" | "OUTCOME_UNKNOWN">;
};

export type DurableTaskContextCommitReceipt = TaskContextSemanticReference & {
  readonly semanticState: "adapter_accepted" | "delivery_unknown";
  readonly expectedTaskStateVersion: number;
  readonly committedTaskStateVersion: number;
};

export type TaskContextCommitResult =
  | { readonly decision: "COMMITTED" | "ALREADY_COMMITTED"; readonly receipt: DurableTaskContextCommitReceipt; readonly snapshot: TaskContextSemanticSnapshot }
  | { readonly decision: "FAIL_CLOSED" | "COMMIT_OUTCOME_UNKNOWN"; readonly reason: string };

export type CommitTaskContextSemanticTransitionInput = {
  readonly store: TaskContextCommitStore;
  readonly transitionResult: SemanticTransitionResult;
  readonly plan: ReconciliationExecutionPlan;
};

const identifier = (value: unknown): value is string => typeof value === "string" && value.trim() !== "";
const same = (left: unknown, right: unknown): boolean => JSON.stringify(left) === JSON.stringify(right);

function receipt(snapshot: TaskContextSemanticSnapshot, reference: TaskContextSemanticReference): DurableTaskContextCommitReceipt {
  return { ...reference, semanticState: reference.target, expectedTaskStateVersion: reference.expectedTaskStateVersion, committedTaskStateVersion: snapshot.stateVersion };
}

function validReference(snapshot: TaskContextSemanticSnapshot, reference: TaskContextSemanticReference): boolean {
  return snapshot.semanticState === reference.target
    && same(snapshot.semanticReference, reference)
    && snapshot.stateVersion === reference.expectedTaskStateVersion + 1
    && Array.isArray(reference.evidenceEventIds)
    && reference.evidenceEventIds.length > 0
    && reference.evidenceEventIds.every(identifier)
    && new Set(reference.evidenceEventIds).size === reference.evidenceEventIds.length;
}

export async function commitTaskContextSemanticTransition(input: CommitTaskContextSemanticTransitionInput): Promise<TaskContextCommitResult> {
  const { store, transitionResult, plan } = input;
  if (!store || !plan || plan.decision !== "PLANNED" || !transitionResult) return { decision: "FAIL_CLOSED", reason: "Commit requires a planned semantic transition." };
  const candidate = transitionResult.nextSnapshot;
  const reference = candidate?.semanticReference;
  if (!candidate || transitionResult.decision !== "APPLIED" || !reference || !validReference(candidate, reference)) return { decision: "FAIL_CLOSED", reason: "Transition result is not a complete APPLIED semantic snapshot." };
  if (candidate.taskId !== reference.taskId || candidate.stateVersion !== reference.expectedTaskStateVersion + 1) return { decision: "FAIL_CLOSED", reason: "Candidate version or task identity is inconsistent." };
  const current = await store.load(reference.taskId);
  if (!current) return { decision: "FAIL_CLOSED", reason: "Authoritative TaskContext is missing or unreadable." };
  if (validReference(current, reference)) return { decision: "ALREADY_COMMITTED", receipt: receipt(current, reference), snapshot: current };
  if (current.stateVersion !== reference.expectedTaskStateVersion) return { decision: "FAIL_CLOSED", reason: "Authoritative TaskContext version is stale or conflicting." };
  if (current.taskId !== reference.taskId || current.attemptId !== reference.attemptId || current.deliverableId !== reference.deliverableId || current.artifactSetId !== reference.artifactSetId || current.artifactId !== reference.artifactId || current.bindingGeneration !== reference.bindingGeneration) return { decision: "FAIL_CLOSED", reason: "Authoritative TaskContext identity conflicts with the transition." };
  let outcome: "COMMITTED" | "STALE" | "OUTCOME_UNKNOWN";
  try { outcome = await store.replace(reference.taskId, reference.expectedTaskStateVersion, candidate); } catch { return { decision: "COMMIT_OUTCOME_UNKNOWN", reason: "Commit failed without a reliable post-write outcome." }; }
  if (outcome === "OUTCOME_UNKNOWN") return { decision: "COMMIT_OUTCOME_UNKNOWN", reason: "Commit outcome is unknown; automatic overwrite is forbidden." };
  if (outcome === "STALE") return { decision: "FAIL_CLOSED", reason: "Version fence rejected the durable replacement." };
  const committed = await store.load(reference.taskId);
  if (!committed || !validReference(committed, reference)) return { decision: "COMMIT_OUTCOME_UNKNOWN", reason: "Replacement returned success but durable reload did not prove the candidate." };
  return { decision: "COMMITTED", receipt: receipt(committed, reference), snapshot: committed };
}
