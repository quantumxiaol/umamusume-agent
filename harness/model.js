import { randomUUID } from 'node:crypto';
import { openAIUsage } from './state.js';

/** Convert only the text vocabulary used by our core. Never inherit Harness tools/history. */
export function modelRequest(call, route, signal) {
  if (!Array.isArray(call.messages) || !call.messages.length) throw new Error('Empty roleplay model request');
  const messages = call.messages.map(message => {
    if (!['system', 'user', 'assistant'].includes(message.role) || typeof message.content !== 'string') {
      throw new Error('Unsupported roleplay message');
    }
    const source = message.role === 'system' ? { kind: 'system-prompt' }
      : message.role === 'assistant' ? { kind: 'model', provider: route.provider, model: route.model }
      : { kind: 'user' };
    return { id: randomUUID(), role: message.role, content: [{ type: 'text', text: message.content }], source };
  });
  return { provider: route.provider, model: route.model, messages, signal,
    ...(route.reasoningEffort ? { reasoningEffort: route.reasoningEffort } : {}),
    ...(call.max_tokens ? { maxTokens: call.max_tokens } : {}),
    ...(call.temperature !== undefined ? { temperature: call.temperature } : {}) };
}

/** One provider attempt. JSON/length repair remains owned by CharacterRuntime. */
export async function executeModelCall({ llm, call, route, signal, onChunk = () => {} }) {
  const request = modelRequest(call, route, signal);
  const stream = await llm.stream(request);
  let text = '', usage, finish;
  const blocks = new Map();
  const append = (index, delta) => {
    blocks.set(index, (blocks.get(index) ?? '') + delta);
    text += delta;
    if (call.stream && delta) onChunk({ choices: [{ delta: { content: delta }, finish_reason: null }] });
  };
  for await (const chunk of stream) {
    signal.throwIfAborted();
    if (chunk.type === 'text-delta') {
      append(chunk.index, chunk.text);
    } else if (chunk.type === 'block-end' && chunk.block.type === 'text') {
      const received = blocks.get(chunk.index) ?? '';
      if (!chunk.block.text.startsWith(received)) throw new Error('Model final text disagrees with streamed deltas');
      append(chunk.index, chunk.block.text.slice(received.length));
    } else if (chunk.type === 'usage') usage = chunk.usage;
    else if (chunk.type === 'finish') finish = chunk.reason;
    else if (chunk.type === 'tool-call-delta' || (chunk.type === 'block-end' && chunk.block.type === 'tool-call')) {
      throw new Error('The roleplay model returned a tool call; this preset accepts text only.');
    }
  }
  signal.throwIfAborted();
  if (!finish) throw new Error('Model stream ended without a finish marker');
  if (!['stop', 'max-tokens'].includes(finish.kind)) throw new Error(finish.failure?.message ?? `Model stopped: ${finish.kind}`);
  const finishReason = finish.kind === 'max-tokens' ? 'length' : 'stop';
  const normalizedUsage = openAIUsage(usage);
  if (call.stream) onChunk({ choices: [{ delta: {}, finish_reason: finishReason }], ...(normalizedUsage ? { usage: normalizedUsage } : {}) });
  return { usage, response: { id: randomUUID(), model: route.model,
    choices: [{ message: { content: text }, finish_reason: finishReason }],
    ...(normalizedUsage ? { usage: normalizedUsage } : {}) } };
}
