// Use the real Axios interceptor chain and API functions; all network adapters
// are offline stubs, including fetch for single/director SSE.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import axios from 'axios';
import * as inputLimits from '../src/services/inputLimits.js';
import { createRateLimitTracker } from '../src/services/rateLimit.js';

async function setup({ status = 429, headers = {}, data = { retry_after: 3 } } = {}) {
  let now = 0;
  const tracker = createRateLimitTracker(() => now);
  const calls = [];
  const context = vm.createContext({ console, URL, TextDecoder,
    fetch: async (url) => {
      calls.push(url);
      return new Response(JSON.stringify(data), { status, headers });
    },
  });
  const module = new vm.SourceTextModule(await readFile(new URL('../src/services/api.js', import.meta.url), 'utf8'), {
    context, initializeImportMeta: (meta) => { meta.env = { VITE_API_BASE_URL: 'https://test' }; },
  });
  await module.link((specifier) => {
    const exports = specifier === 'axios' ? { default: { create: (config) => axios.create({
      ...config, adapter: async (request) => {
        calls.push(request.url);
        const response = { status, headers, data, config: request };
        if (status >= 400) throw Object.assign(new Error('HTTP error'), { config: request, response });
        return response;
      },
    }) } } : specifier.endsWith('rateLimit') ? { rateLimits: tracker } : inputLimits;
    return new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
    }, { context });
  });
  await module.evaluate();
  return { api: module.namespace, tracker, calls, advance: (ms) => { now += ms; } };
}

test('Axios 429 becomes a wait message and suppresses further same-bucket HTTP calls', async () => {
  const { api, calls, advance } = await setup({ headers: { 'retry-after': '4' } });
  await assert.rejects(api.fetchCharacters(), { status: 429, code: 'RATE_LIMIT', retryAfter: 4 });
  await assert.rejects(api.importHistory('session', []), { status: 429 });
  assert.equal(calls.length, 1);
  advance(4000);
  assert.equal(calls.length, 1); // No automatic replay on expiry.
  await assert.rejects(api.fetchCapabilities(), { status: 429 });
  assert.equal(calls.length, 2);
});

test('single and director streams share the chat cooldown and preserve structured errors', async () => {
  const { api, calls } = await setup();
  const emitted = [];
  await assert.rejects(api.chatStream('s', 'hi', false, (event) => emitted.push(event)), { status: 429, retryAfter: 3 });
  assert.match(emitted[0].data, /等待 3 秒/);
  await assert.rejects(api.directorTurnStream('s', [], 'u', false, (event) => emitted.push(event)), { status: 429 });
  assert.match(emitted[1].data.detail, /等待 3 秒/);
  assert.equal(calls.length, 1);
  await assert.rejects(api.chatOnce('s', 'hi'), { status: 429 });
  assert.equal(calls.length, 1);
});

test('ordinary HTTP failures do not start a cooldown; fetch retains status', async () => {
  const { api, tracker } = await setup({ status: 401, data: { detail: 'Invalid key' } });
  await assert.rejects(api.fetchCharacters(), { status: 401 });
  await assert.rejects(api.chatStream('s', 'hi', false, () => {}), { status: 401 });
  assert.deepEqual(tracker.snapshot(), []);
});
