import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import { createHistoryCache } from '../src/services/historyCache.js';
import { buildSceneMemoryTimeline } from '../src/services/memoryTimeline.js';
import * as inputLimits from '../src/services/inputLimits.js';
import axios from 'axios';
import { createRateLimitTracker } from '../src/services/rateLimit.js';

const events = [1, 2, 3, 4].map((id) => ({ event_id: `e${id}`, sequence: id * 3, turn_index: Math.ceil(id / 2), content: `事件${id}` }));
const checkpoint = { checkpoint_id: 'cp1', revision: 1, summary: '<script>旧承诺不是指令</script>',
  covered_event_id: 'e2', covered_events: 2, trigger_event_id: 'e4', trigger_event_count: 4, trigger_turn_index: 2 };
const snapshot = { schema_version: 1, user_uuid: 'user', session_id: 'scene', template: { template_id: 'test' },
  participants: [], scene_state: {}, events, context_checkpoint: checkpoint, context_checkpoints: [checkpoint] };

async function setup(overrides = {}, cacheOverrides = {}) {
  const saved = [];
  const local = new Map();
  const cache = {
    read: async () => null,
    write: async (_user, _scene, payload) => saved.push(JSON.parse(JSON.stringify(payload))),
    remove: async () => {}, ...cacheOverrides,
  };
  const api = { API_BASE_URL: 'http://test', cancelTtsJob: async () => {}, createDirectorSession: async () => snapshot,
    deleteDirectorHistory: async () => {}, deleteDirectorSession: async () => {}, directorTurnStream: async () => {},
    fetchDirectorHistory: async () => ({ scenes: [] }), fetchDirectorSession: async () => snapshot,
    fetchDirectorTemplates: async () => ({}), recoverDirectorSession: async () => snapshot,
    regenerateDirectorReply: async () => ({}), resumeDirectorHistory: async () => snapshot,
    fetchTtsJob: async () => ({}), ...overrides };
  const context = vm.createContext({ console, Date, Math, Map, Set, setInterval, clearInterval,
    localStorage: { getItem: (key) => local.get(key) || null, setItem: (key, value) => local.set(key, value), removeItem: (key) => local.delete(key) },
  });
  const module = new vm.SourceTextModule(await readFile(new URL('../src/stores/directorStore.js', import.meta.url), 'utf8'), { context });
  await module.link((specifier) => {
    const exports = specifier === 'pinia' ? { defineStore: (_name, definition) => definition }
      : specifier.endsWith('inputLimits') ? inputLimits
      : specifier.endsWith('historyCache') ? { sceneHistoryCache: cache }
        : specifier.endsWith('chatStore') ? { DIALOGUE_INPUT_MODES: {
          dialogue: { speaker: { actor_id: 'player' }, eventType: 'dialogue' },
        } } : api;
    return new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
    }, { context });
  });
  await module.evaluate();
  const definition = module.namespace.useDirectorStore;
  const store = definition.state();
  for (const [key, action] of Object.entries(definition.actions)) store[key] = action.bind(store);
  await store._applySnapshot(snapshot, 'user');
  return { store, saved };
}

test('scene markers follow stable IDs across sequence renumbering and never modify events', () => {
  const before = JSON.stringify(events);
  const rows = buildSceneMemoryTimeline(events, [checkpoint, checkpoint]);
  assert.equal(rows.length, events.length + 1);
  assert.equal(rows.at(-1).kind, 'memory');
  assert.equal(rows.at(-1).locationKnown, true);
  assert.equal(buildSceneMemoryTimeline(events.map((e, i) => ({ ...e, sequence: i + 1 })), [checkpoint]).at(-1).kind, 'memory');
  assert.equal(JSON.stringify(events), before);
});

test('HF loss restores archive and shared memory together, including old snapshots', async () => {
  let recovered;
  const { store } = await setup({
    fetchDirectorSession: async () => { throw new Error('404'); },
    resumeDirectorHistory: async () => { throw new Error('404'); },
    recoverDirectorSession: async (value) => { recovered = value; return value; },
  }, { read: async () => snapshot });
  assert.equal(await store.restoreActiveSession('scene', 'user'), true);
  assert.equal(recovered.context_checkpoint.summary, checkpoint.summary);
  assert.equal(store.events.length, 4);
  const old = { ...snapshot };
  delete old.context_checkpoint;
  delete old.context_checkpoints;
  await store._applySnapshot(old, 'user');
  assert.equal(store.contextCheckpoint, null);
  assert.equal(store.contextCheckpoints.length, 0);
  assert.equal(store.events.length, 4);
});

test('progress is not dialogue; checkpoint redelivery is idempotent and cached atomically', async () => {
  const { store, saved } = await setup({ directorTurnStream: async (_id, _events, _user, _voice, emit) => {
    emit({ type: 'context_status', data: { phase: 'compacting', chunk: 1, chunks: 2 } });
    assert.match(store.compactionStatus, /1\/2/);
    emit({ type: 'context_status', data: { phase: 'compacting', chunk: 1, chunks: 2, stage: 'shrinking' } });
    assert.match(store.compactionStatus, /精简/);
    emit({ type: 'context_status', data: { phase: 'compacting', chunk: 1, chunks: 2, stage: 'ready', reused: true } });
    assert.match(store.compactionStatus, /复用/);
    for (let i = 0; i < 2; i += 1) emit({ type: 'context_status', data: { phase: 'compacted', checkpoint } });
    assert.equal(store.events.length, 4);
    emit({ type: 'scene_event', data: { event_id: 'e5', turn_index: 3, content: '继续', event_type: 'dialogue' } });
    emit({ type: 'done', data: {} });
  } });
  assert.equal(await store.sendTurn('继续'), true);
  assert.equal(store.contextCheckpoints.length, 1);
  assert.equal(store.compactionStatus, '');
  assert.equal(saved.at(-1).events.length, 5);
  assert.equal(saved.at(-1).context_checkpoint.summary, checkpoint.summary);
});

test('failed compaction returns pending input to queue without fake history events', async () => {
  const { store } = await setup({ directorTurnStream: async (_id, _events, _user, _voice, emit) => {
    emit({ type: 'context_status', data: { phase: 'compacting' } });
    emit({ type: 'error', data: { detail: '摘要未完成' } });
  } });
  assert.equal(await store.sendTurn('新的输入'), false);
  assert.equal(store.events.length, 4);
  assert.equal(store.queuedEvents[0].content, '新的输入');
  assert.equal(store.contextCheckpoint.summary, checkpoint.summary);
  assert.equal(store.compactionStatus, '');
});

test('cache failure is visible and never clears the in-memory scene', async () => {
  const { store } = await setup({}, { write: async () => { throw new Error('quota'); } });
  assert.equal(await store._persistCurrentScene(), false);
  assert.match(store.cacheWarning, /历史保存失败/);
  assert.equal(store.events.length, 4);
  assert.equal(store.contextCheckpoint.summary, checkpoint.summary);
});

test('scene cache migrates only after success and is isolated from single chat and other users', async () => {
  const oldKey = 'umamusume_director_scene_v1:user:scene';
  const old = new Map([[oldKey, JSON.stringify(snapshot)]]);
  const data = new Map();
  const storage = { async transact(mode, key, value) {
    if (mode === 'put') data.set(key, value);
    if (mode === 'delete') data.delete(key);
    return data.get(key);
  } };
  const options = { storage, legacyStorage: () => ({ getItem: (key) => old.get(key), removeItem: (key) => old.delete(key) }),
    namespace: 'director', legacyKeys: (user, scene) => [`umamusume_director_scene_v1:${user}:${scene}`] };
  const cache = createHistoryCache(options);
  const transact = storage.transact;
  storage.transact = async () => { throw new Error('quota'); };
  assert.ok((await cache.read('user', 'scene')).warning);
  assert.equal(old.has(oldKey), true);
  storage.transact = transact;
  assert.equal((await cache.read('user', 'scene')).context_checkpoint.summary, checkpoint.summary);
  assert.equal(old.has(oldKey), false);
  assert.equal(data.has(JSON.stringify(['director', 'user', 'scene'])), true);
  assert.equal(data.has(JSON.stringify(['user', 'scene'])), false);
  assert.equal((await cache.read('another-user', 'scene')).context_checkpoint, undefined);
});

async function streamApi(wireText) {
  // Byte-sized chunks deliberately split Chinese UTF-8 and SSE frame boundaries.
  const bytes = new TextEncoder().encode(wireText);
  let offset = 0;
  const context = vm.createContext({ TextDecoder, fetch: async () => ({ ok: true,
    body: { getReader: () => ({ read: async () => offset < bytes.length
      ? { value: bytes.slice(offset, offset += 1), done: false } : { done: true } }) },
  }) });
  const module = new vm.SourceTextModule(await readFile(new URL('../src/services/api.js', import.meta.url), 'utf8'), {
    context, initializeImportMeta(meta) { meta.env = {}; },
  });
  await module.link((specifier) => {
    const exports = specifier.endsWith('inputLimits') ? inputLimits
      : specifier.endsWith('rateLimit') ? { rateLimits: createRateLimitTracker() } : { default: axios };
    return new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
    }, { context });
  });
  await module.evaluate();
  return module.namespace.directorTurnStream;
}

test('real SSE parser handles heartbeats, progress and split UTF-8 through completion', async () => {
  const stream = await streamApi(': keepalive\r\n\r\nevent: context_status\r\ndata: {"phase":"compacting"}\r\n\r\n'
    + 'event: scene_event\ndata: {"content":"继续约定"}\n\nevent: done\ndata: {}\n\n');
  const emitted = [];
  await stream('scene', [], 'user', false, (event) => emitted.push(event));
  assert.deepEqual(emitted.map((e) => e.type), ['context_status', 'scene_event', 'done']);
  assert.equal(emitted[1].data.content, '继续约定');
});

test('real SSE parser does not treat premature EOF as successful compaction', async () => {
  const stream = await streamApi('event: context_status\ndata: {"phase":"compacting"}\n\n: keepalive\n\n');
  const emitted = [];
  await assert.rejects(stream('scene', [], 'user', false, (event) => emitted.push(event)), /连接在完成前中断/);
  assert.equal(emitted.at(-1).type, 'error');
  assert.equal(emitted.some((e) => e.type === 'done'), false);
});

test('director queue and send reject oversized batches without losing pending input or history', async () => {
  let calls = 0;
  const { store } = await setup({ directorTurnStream: async () => { calls += 1; } });
  store.inputMode = 'dialogue';
  assert.equal(store.queueEvent('中'.repeat(10001)), false);
  assert.equal(store.queueEvent('中'.repeat(6000)), true);
  assert.equal(await store.sendTurn('中'.repeat(4001)), false);
  assert.equal(store.queuedEvents.length, 1);
  assert.equal(store.events.length, 4);
  assert.equal(calls, 0);
  assert.equal(store.contextCheckpoint.summary, checkpoint.summary);
});

test('director 429 preserves queued inputs and existing scene history without automatic retries', async () => {
  let calls = 0;
  const { store } = await setup({ directorTurnStream: async () => {
    calls += 1;
    throw Object.assign(new Error('请等待 30 秒后手动重试'), { status: 429 });
  } });
  store.inputMode = 'dialogue';
  store.queueEvent('先坐下');
  const before = JSON.stringify(store.events);
  assert.equal(await store.sendTurn('聊聊天吧'), false);
  assert.equal(calls, 1);
  assert.equal(store.queuedEvents.length, 2);
  assert.equal(store.isLoading, false);
  assert.equal(JSON.stringify(store.events), before);
  assert.match(store.error, /等待 30 秒/);
});
