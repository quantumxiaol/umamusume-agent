// Explicit integration check of the end-user uv path; no API credentials or network model calls.
import assert from 'node:assert/strict';
import { runWorker } from '../worker.js';
import { initialState } from '../state.js';

const stages = [];
const result = await runWorker({ config: { pythonExecutable: '', uvExecutable: '', charactersDirectory: '' },
  signal: AbortSignal.timeout(180000),
  input: { sessionId: 'locked-runtime-test', state: { ...initialState(true), character: 'rice_shower' },
    input: { message: '你好', mode: 'dialogue', queue: [] }, options: { model: 'offline', contextWindow: 128000, maxOutputTokens: 1024 } },
  onProgress: value => stages.push(value),
  onModel: async () => ({ choices: [{ message: { content: JSON.stringify({ action: '点头', dialogue: '你好。' }) }, finish_reason: 'stop' }] }),
});
assert.equal(result.reply.dialogue, '你好。');
assert.ok(stages.some(value => value.stage === 'python-startup' && value.phase === 'end'));
console.log('Locked uv runtime passed:', stages.filter(value => value.durationMs !== undefined)
  .map(value => `${value.label} ${value.durationMs}ms`).join(', '));
