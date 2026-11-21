// Probe driver (ECR experiment 3): creates or resumes one DSH agent session,
// sends one user message, waits for idle, flushes durable state and exits.
// ECR_MODE=start  -> fresh session, ask the agent to send the message.
// ECR_MODE=resume -> resume the persisted session (DSH appends its crash
//                    closers), then send "Continue." and let the model decide.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const nodeModules = process.env.ECR_DSH_NODE_MODULES;
const { createUserMessage } = await import(pathToFileURL(`${nodeModules}/@deepseek-ai/dsh-llm/lib/index.js`).href);

export const name = 'ecr-probe-driver';
export const inject = ['agents', 'sessions'];

function signal(name, value) {
  fs.writeFileSync(path.join(process.env.ECR_SIGNAL_DIR, name), JSON.stringify(value ?? {}));
}

async function run(ctx) {
  await ctx.get('loader')?.await();
  const agents = ctx.get('agents');
  const sessions = ctx.get('sessions');
  const sessionId = process.env.ECR_SESSION_ID;
  const agentOptions = { provider: 'ecr-scripted', model: 'scripted-1' };
  const mode = process.env.ECR_MODE;
  const agent = mode === 'start'
    ? (await agents.create({ sessionId, meta: { cwd: process.cwd() }, agentOptions })).agent
    : (await agents.resume({ resumeSessionId: sessionId, agentOptions })).agent;
  await agent.whenIdle();
  signal(`${mode}-ready.json`, { pid: process.pid, seq: agent.session.seq });
  const say = async (text) => {
    agent.followup(createUserMessage({ content: [{ type: 'text', text }], source: { kind: 'user' } }));
    await agent.whenIdle();
  };
  if (mode === 'start') {
    // Warm-up turn, made durable, so the session already exists before the side-effecting turn.
    await say('Hello.');
    await sessions.flush(agent.session);
    signal('warm-flushed.json', { pid: process.pid, seq: agent.session.seq });
    await say(`Send the message for operation ${process.env.ECR_OPERATION_ID}.`);
  } else {
    await say('Continue.');
  }
  await sessions.flush(agent.session);
  signal(`${mode}-done.json`, { pid: process.pid, seq: agent.session.seq });
}

export function apply(ctx) {
  const exit = ctx.get('appExit');
  run(ctx).then(() => exit(0), (error) => {
    signal(`${process.env.ECR_MODE}-error.json`, { message: String(error?.stack ?? error) });
    exit(1);
  });
}
