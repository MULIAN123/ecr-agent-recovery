// D1 arm (ECR experiment 3): durable dispatch evidence before a side-effecting tool runs.
// DSH appends the `tool/call` record before `tools/pre-execute`; this listener awaits
// DSH's own durability barrier (`sessions.flush`) so the record is on disk before the
// tool body can cause an external effect. No DSH source is modified.
import fs from 'node:fs';
import path from 'node:path';

export const name = 'ecr-durable-dispatch';
export const inject = ['tools', 'sessions'];

const SIDE_EFFECTING = new Set((process.env.ECR_SIDE_EFFECTING_TOOLS ?? 'send_message').split(','));

export function apply(ctx) {
  ctx.on('tools/pre-execute', async (exec, next) => {
    if (SIDE_EFFECTING.has(exec.name) && exec.agent !== undefined) {
      const started = process.hrtime.bigint();
      await ctx.sessions.flush(exec.agent.session);
      const micros = Number((process.hrtime.bigint() - started) / 1000n);
      // Cost measurement only (not fsynced): flush latency added before dispatch.
      fs.appendFileSync(path.join(process.env.ECR_SIGNAL_DIR, 'flush-latency.jsonl'), JSON.stringify({ callId: exec.callId, micros }) + '\n');
    }
    return next();
  });
}
