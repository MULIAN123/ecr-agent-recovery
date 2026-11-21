// Scripted model adapter for the DSH feasibility probe (ECR experiment 3).
// No network, no model API: replies are a fixed function of the request messages,
// with fixed latencies standing in for model generation time.
// Policy (ECR_MODEL_POLICY):
//   obedient - follows DSH's own recovery guidance: retries a TOOL_NOT_STARTED
//              call, does not retry a TOOL_OUTCOME_UNKNOWN call, and acts on a
//              durable user request that has no tool call after it.
//   naive    - additionally retries any interrupted (error) tool call.
// Latencies: ECR_LATENCY_BEFORE_CALL_MS (before emitting the tool call),
//            ECR_LATENCY_AFTER_TOOL_MS (before answering a tool result).
import { setTimeout as sleep } from 'node:timers/promises';
import { pathToFileURL } from 'node:url';

const nodeModules = process.env.ECR_DSH_NODE_MODULES;
const { LlmAdapter } = await import(pathToFileURL(`${nodeModules}/@deepseek-ai/dsh-llm/lib/index.js`).href);

export const name = 'ecr-scripted-llm';
export const inject = ['llm'];

const NOT_STARTED = 'before the Harness recorded it as started';
const OUTCOME_UNKNOWN = 'Its outcome is unknown';
const SEND_REQUEST = 'Send the message for operation';

function textOf(message) {
  return (message.content ?? []).filter((b) => b.type === 'text').map((b) => b.text).join('');
}

function decide(messages) {
  const policy = process.env.ECR_MODEL_POLICY ?? 'obedient';
  const last = messages.at(-1);
  if (last?.role === 'tool') return { text: last.isError ? 'The send failed.' : 'Message sent.', after: 'tool' };
  const request = messages.findLastIndex((m) => m.role === 'user' && textOf(m).startsWith(SEND_REQUEST));
  if (request === -1) return { text: 'Hello. Nothing is pending.' };
  const tools = messages.slice(request + 1).filter((m) => m.role === 'tool');
  if (tools.length === 0) return { call: true, reason: 'durable user request has no tool call after it' };
  const lastTool = tools.at(-1);
  if (!lastTool.isError) return { text: 'Already sent; nothing to do.' };
  const text = textOf(lastTool);
  if (policy === 'naive') return { call: true, reason: 'naive retry of interrupted call' };
  if (text.includes(NOT_STARTED)) return { call: true, reason: 'DSH guidance: not started, retry if still needed' };
  if (text.includes(OUTCOME_UNKNOWN)) return { text: 'Outcome unknown and the tool has side effects; not retrying.' };
  return { text: 'Unrecognised tool error; not retrying.' };
}

let callCounter = 0;

class ScriptedAdapter extends LlmAdapter {
  async *stream(options) {
    const decision = decide(options.messages);
    const delay = decision.call ? process.env.ECR_LATENCY_BEFORE_CALL_MS : decision.after === 'tool' ? process.env.ECR_LATENCY_AFTER_TOOL_MS : 0;
    if (Number(delay) > 0) await sleep(Number(delay), undefined, { signal: options.signal });
    if (decision.call) {
      callCounter += 1;
      const id = `ecr-call-${process.pid}-${callCounter}`;
      const args = JSON.stringify({ operation_id: process.env.ECR_OPERATION_ID });
      yield { type: 'block-start', index: 0, blockType: 'tool-call' };
      yield { type: 'tool-call-delta', index: 0, id, name: 'send_message', argumentsDelta: args };
      yield { type: 'block-end', index: 0, block: { type: 'tool-call', id, name: 'send_message', arguments: args } };
    } else {
      yield { type: 'block-start', index: 0, blockType: 'text' };
      yield { type: 'text-delta', index: 0, text: decision.text };
      yield { type: 'block-end', index: 0, block: { type: 'text', text: decision.text } };
    }
    yield { type: 'finish', reason: { kind: 'stop' } };
  }
}

export function apply(ctx) {
  ctx.llm.registerAdapter(['ecr-scripted'], new ScriptedAdapter());
}
