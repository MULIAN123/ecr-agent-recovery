// D2 arm (ECR experiment 3): evidence-constrained authorization of a resend.
// A monotonic DSH tool guard (synchronous, cannot be overridden by later listeners)
// denies a side-effecting call when the durable session already holds a call for the
// same logical operation without a successful result: dispatch evidence without an
// outcome is OUTCOME_UNKNOWN, and a new effect needs an endpoint guarantee (fenced
// negative query or server idempotency), which this endpoint does not offer.
// A prior successful call also denies a resend (the effect already exists).
export const name = 'ecr-evidence-guard';
export const inject = ['tools'];

const SIDE_EFFECTING = new Set((process.env.ECR_SIDE_EFFECTING_TOOLS ?? 'send_message').split(','));
const ENDPOINT_GUARANTEE = process.env.ECR_ENDPOINT_GUARANTEE ?? 'none';

function operationOf(args) {
  try {
    const value = typeof args === 'string' ? JSON.parse(args) : args;
    return value?.operation_id;
  } catch {
    return undefined;
  }
}

export function apply(ctx) {
  ctx.tools.guard((exec) => {
    if (!SIDE_EFFECTING.has(exec.name) || exec.agent === undefined) return undefined;
    const operation = operationOf(exec.arguments);
    if (operation === undefined) return 'ECR guard: side-effecting call without a logical operation id';
    const events = exec.agent.session.snapshotEvents();
    const calls = events.filter((e) => e.type === 'tool/call' && e.data.name === exec.name
      && e.data.callId !== exec.callId && operationOf(e.data.arguments) === operation);
    if (calls.length === 0) return undefined;
    const results = new Map(events.filter((e) => e.type === 'tool/result').map((e) => [e.data.message.toolCallId, e.data.message]));
    for (const call of calls) {
      const result = results.get(call.data.callId);
      if (result !== undefined && !result.isError) {
        return `ECR guard: operation ${operation} already has a successful effect (call ${call.data.callId}); a resend would duplicate it.`;
      }
      if (ENDPOINT_GUARANTEE === 'none') {
        return `ECR guard: operation ${operation} crossed the dispatch boundary in call ${call.data.callId} without a durable outcome (OUTCOME_UNKNOWN). A resend needs an endpoint guarantee (fenced negative query or server idempotency); none is available, so this send is not authorized.`;
      }
    }
    return undefined;
  });
}
