import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { initialState, reduceState, replyText, sumUsage, openAIUsage, RECORD } from '../state.js';
import { modelRequest, executeModelCall } from '../model.js';
import { runWorker } from '../worker.js';

const signal = () => new AbortController().signal;
const route = { provider: 'user-provider', model: 'user-model' };
const python = process.env.UMA_TEST_PYTHON || fileURLToPath(new URL('../../.venv/bin/python', import.meta.url));

test('provider request uses core messages and the user model, with no tools or credentials', () => {
  const request = modelRequest({ model: 'ignored', messages: [{ role: 'system', content: '角色卡' }, { role: 'user', content: '你好' }], max_tokens: 512 }, route, signal());
  assert.equal(request.model, route.model);
  assert.equal(request.messages[0].content[0].text, '角色卡');
  assert.equal(request.maxTokens, 512);
  assert.equal(request.tools, undefined);
  assert.equal(request.apiKey, undefined);
});

test('stream conversion counts cache and reasoning without double counting', async () => {
  const chunks = [];
  const llm = { async *stream() {
    yield { type: 'text-delta', index: 0, text: '记忆' };
    yield { type: 'usage', usage: { inputTokens: 10, cacheReadTokens: 20, cacheWriteTokens: 5, outputTokens: 8, reasoningTokens: 3 } };
    yield { type: 'finish', reason: { kind: 'stop' } };
  } };
  const result = await executeModelCall({ llm, route, signal: signal(), call: { stream: true, messages: [{ role: 'user', content: '摘要' }] }, onChunk: c => chunks.push(c) });
  assert.equal(result.response.usage.prompt_tokens, 35);
  assert.equal(result.response.usage.total_tokens, 43);
  assert.equal(chunks.at(-1).choices[0].finish_reason, 'stop');
  assert.deepEqual(sumUsage({ inputTokens: 4 }, result.usage).inputTokens, 14);
  assert.equal(openAIUsage(undefined), undefined);
  assert.equal(sumUsage({ totalTokens: 5 }, result.usage).totalTokens, 48);
});

test('final-only text blocks work without duplicating previously streamed text', async () => {
  for (const delta of ['', '你']) {
    const llm = { async *stream() {
      if (delta) yield { type: 'text-delta', index: 0, text: delta };
      yield { type: 'block-end', index: 0, block: { type: 'text', text: '你好' } };
      yield { type: 'finish', reason: { kind: 'stop' } };
    } };
    const result = await executeModelCall({ llm, route, signal: signal(), call: { messages: [{ role: 'user', content: 'hi' }] } });
    assert.equal(result.response.choices[0].message.content, '你好');
  }
});

test('truncation stays truncation and missing/error finishes cannot become success', async () => {
  for (const [finish, expected] of [[{ kind: 'max-tokens' }, 'length'], [null, null], [{ kind: 'error', failure: { message: 'quota exceeded' } }, null]]) {
    const llm = { async *stream() { yield { type: 'text-delta', index: 0, text: '{' }; if (finish) yield { type: 'finish', reason: finish }; } };
    const promise = executeModelCall({ llm, route, signal: signal(), call: { messages: [{ role: 'user', content: 'hi' }] } });
    if (expected) assert.equal((await promise).response.choices[0].finish_reason, expected);
    else await assert.rejects(promise);
  }
});

test('native settlement is the commit boundary; cancellation and replay are safe', () => {
  const state = initialState(true);
  const result = { baseRevision: 0, replaceFrom: 0, historyAppend: [{ role: 'user', content: '你好' }, { role: 'assistant', content: '你好！' }],
    checkpoint: null, checkpoints: [], tokenRatio: .5, input: { message: '你好' }, reply: { characterName: '米浴', action: '微笑', dialogue: '你好！' } };
  const prepared = { type: RECORD + 'prepared', ignorable: true, data: { version: 1, turn: 1, step: 1, result } };
  const waiting = reduceState(state, prepared);
  assert.equal(waiting.history.length, 0);
  assert.equal(reduceState(waiting, { type: 'assistant/attempt', data: {} }).history.length, 0);
  const cancelled = reduceState(waiting, { type: 'turn/end', data: {} });
  assert.equal(cancelled.prepared, null);
  assert.equal(cancelled.history.length, 0);
  const settled = { type: 'assistant/message', data: { turn: 1, step: 1,
    message: { content: [{ type: 'text', text: replyText(result.reply) }] } } };
  const committed = reduceState(waiting, settled);
  assert.equal(committed.history.length, 2);
  assert.equal(committed.turns, 1);
  assert.deepEqual([prepared, settled].reduce(reduceState, initialState(true)), committed);
  const queue = [{ content: '下一轮事件', event_type: 'scene_event' }];
  const retry = reduceState({ ...waiting, queue, turns: 1,
    prepared: { ...waiting.prepared, result: { ...result, regenerate: true } } }, settled);
  assert.deepEqual(retry.queue, queue);
  assert.equal(retry.turns, 1);
});

test('Python bridge runs the real character core, repairs JSON, restores and regenerates', { skip: !existsSync(python) }, async () => {
  let calls = 0;
  const requests = [];
  const config = { pythonExecutable: python };
  const base = { sessionId: 'test-session', state: { ...initialState(true), character: 'rice_shower' },
    input: { message: '一起去训练吧', mode: 'dialogue', queue: [] },
    options: { model: 'mock', contextWindow: 128000, maxOutputTokens: 6144 } };
  const onModel = async call => {
    requests.push(call);
    return { choices: [{ message: { content: ++calls === 1 ? 'not JSON' : JSON.stringify({ action: '轻轻点头', dialogue: '好，我们一起去。' }) }, finish_reason: 'stop' }],
      usage: { prompt_tokens: 100, completion_tokens: 10 } };
  };
  const result = await runWorker({ config, input: base, signal: signal(), onModel });
  assert.equal(calls, 2);
  assert.match(result.reply.dialogue, /一起/);
  assert.equal(result.historyAppend.length, 2);
  assert.ok(requests[0].messages[0].content.length > 100);
  assert.equal(requests[0].response_format, undefined);
  const restored = { ...base.state, history: result.historyAppend, lastInput: result.input, lastReply: result.reply, turns: 1, revision: 1 };
  const second = await runWorker({ config, input: { ...base, state: restored, input: { ...base.input, message: '还记得我们要去哪里吗？' } }, signal: signal(), onModel });
  assert.equal(second.replaceFrom, 2);
  assert.ok(requests.at(-1).messages.some(message => message.content.includes('一起去训练吧')));
  const redo = await runWorker({ config, input: { ...base, state: { ...restored, retry: true } }, signal: signal(), onModel });
  assert.equal(redo.replaceFrom, 0);
  assert.equal(redo.regenerate, true);
  assert.equal(redo.historyAppend.length, 2);
});

test('cancelling a real worker rejects promptly without a completed turn', { skip: !existsSync(python) }, async () => {
  const controller = new AbortController();
  let entered;
  const ready = new Promise(resolve => { entered = resolve; });
  const pending = runWorker({ config: { pythonExecutable: python }, signal: controller.signal,
    input: { sessionId: 'cancel-test', state: { ...initialState(true), character: 'rice_shower' },
      input: { message: '你好', mode: 'dialogue', queue: [] }, options: { model: 'mock', contextWindow: 128000, maxOutputTokens: 6144 } },
    onModel: async () => { entered(); return new Promise(resolve => controller.signal.addEventListener('abort', () => resolve({}), { once: true })); } });
  await ready;
  controller.abort(new Error('test cancellation'));
  await assert.rejects(pending, /test cancellation/);
});

test('long history compacts through streamed provider calls and restores its checkpoint', { skip: !existsSync(python) }, async () => {
  const history = Array.from({ length: 14 }, (_, i) => [
    { role: 'user', content: `第 ${i} 天训练。` + '一起跑步并休息。'.repeat(100) },
    { role: 'assistant', content: JSON.stringify({ action: '点头', dialogue: '记住了。'.repeat(100) }) },
  ]).flat();
  const state = { ...initialState(true), character: 'rice_shower', history, turns: 30 };
  const input = { sessionId: 'memory-test', state, input: { message: '继续吧', mode: 'dialogue', queue: [] },
    options: { model: 'mock', contextWindow: 32768, maxOutputTokens: 1024 } };
  let summaries = 0;
  const requests = [];
  const onModel = async (call, onChunk) => {
    requests.push(call);
    const text = call.stream ? '训练员与米浴每天一起跑步，然后休息。' : JSON.stringify({ action: '点头', dialogue: '好。' });
    if (call.stream) {
      summaries++;
      onChunk({ choices: [{ delta: { content: text }, finish_reason: null }] });
      onChunk({ choices: [{ delta: {}, finish_reason: 'stop' }] });
    }
    return { choices: [{ message: { content: text }, finish_reason: 'stop' }] };
  };
  const result = await runWorker({ config: { pythonExecutable: python }, input, signal: signal(), onModel });
  assert.ok(summaries > 0);
  assert.ok(result.checkpoint.covered_messages > 0);
  assert.match(result.checkpoint.summary, /每天一起/);
  assert.equal(result.replaceFrom, history.length);
  const count = summaries;
  await runWorker({ config: { pythonExecutable: python }, signal: signal(), onModel,
    input: { ...input, state: { ...state, history: [...history, ...result.historyAppend],
      checkpoint: result.checkpoint, checkpoints: result.checkpoints, revision: 1 } } });
  assert.equal(summaries, count, 'restored checkpoint should avoid paying to summarize the same history again');
  assert.ok(requests.at(-1).messages.some(message => message.content.includes(result.checkpoint.summary)));
});
