// Side-effecting tool for the DSH feasibility probe (ECR experiment 3).
// Each execution durably appends one effect record (fsync) to an external ledger
// outside the DSH session store, then publishes a marker so the supervisor can
// kill the process at a controlled delay after the effect.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const nodeModules = process.env.ECR_DSH_NODE_MODULES;
const { defineTool } = await import(pathToFileURL(`${nodeModules}/@deepseek-ai/dsh-tools/lib/index.js`).href);

export const name = 'ecr-effect-tool';
export const inject = ['tools'];

function durableAppend(file, line) {
  const fd = fs.openSync(file, 'a');
  try {
    fs.writeSync(fd, line + '\n');
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
}

export function apply(ctx) {
  ctx.tools.register(defineTool({
    name: 'send_message',
    description: 'Send the message for one operation. Irreversible external side effect; not idempotent.',
    parameters: { operation_id: { type: 'string', required: true, description: 'Logical operation id' } },
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
    async execute(args) {
      // Pre-effect window (formal kill point "pre"): the tool body has started but the
      // external effect has not happened yet.
      const preHold = Number(process.env.ECR_PRE_EFFECT_HOLD_MS ?? 0);
      if (preHold > 0) {
        fs.writeFileSync(path.join(process.env.ECR_SIGNAL_DIR, `pre-effect-${process.pid}-${Date.now()}.marker`), '{}');
        await new Promise((resolve) => setTimeout(resolve, preHold));
      }
      const record = { operation_id: args.operation_id, pid: process.pid, time_ns: process.hrtime.bigint().toString(), wall: Date.now() };
      durableAppend(process.env.ECR_EFFECT_LEDGER, JSON.stringify(record));
      fs.writeFileSync(path.join(process.env.ECR_SIGNAL_DIR, `effect-${process.pid}-${Date.now()}.marker`), JSON.stringify(record));
      // Slow-tool variant: the effect is committed but the response takes time to return.
      const hold = Number(process.env.ECR_TOOL_HOLD_MS ?? 0);
      if (hold > 0) await new Promise((resolve) => setTimeout(resolve, hold));
      return 'sent';
    },
  }));
}
