// Exercise the real store actions without DOM/HTTP or calling a paid model.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const records = [
  { role: 'user', content: '明天一起训练' },
  { role: 'assistant', content: '约好了', action: '点头', dialogue: '约好了',
    model_content: '{ "action": "点头", "dialogue": "约好了" }' },
];
const checkpoint = { revision: 1, covered_messages: 2, summary: '约定明天训练', user_uuid: 'user' };

async function setup(apiOverrides = {}, cacheOverrides = {}) {
  const saved = [];
  const imports = [];
  const cache = {
    read: async () => ({ messages: [], savedAt: '' }),
    write: async (_user, _character, payload) => saved.push(JSON.parse(JSON.stringify(payload))),
    remove: async () => {},
    ...cacheOverrides,
  };
  const api = {
    API_BASE_URL: 'http://test', fetchCharacters: async () => ({}), fetchCapabilities: async () => ({}),
    loadCharacter: async () => ({}), chatOnce: async () => ({}), chatStream: async () => {},
    fetchHistory: async () => ({ messages: [] }), clearHistory: async () => ({}), fetchTtsJob: async () => ({}),
    fetchDialogueContext: async () => ({ context_checkpoint: checkpoint }),
    importHistory: async (...args) => { imports.push(args); return { context_checkpoint: args[4], context_checkpoints: args[5] }; },
    ...apiOverrides,
  };
  const context = vm.createContext({ console, Date, Math, Map, Set, URL, setInterval, clearInterval,
    localStorage: { getItem: () => null, setItem: () => {} },
  });
  const module = new vm.SourceTextModule(await readFile(new URL('../src/stores/chatStore.js', import.meta.url), 'utf8'), {
    context, initializeImportMeta: (meta) => { meta.env = {}; },
  });
  await module.link((specifier) => {
    const exports = specifier === 'pinia' ? { defineStore: (_name, options) => options }
      : specifier.endsWith('historyCache') ? { dialogueHistoryCache: cache } : api;
    return new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
    }, { context });
  });
  await module.evaluate();
  const definition = module.namespace.useChatStore;
  const store = definition.state();
  for (const [key, action] of Object.entries(definition.actions)) store[key] = action.bind(store);
  Object.assign(store, { userUuid: 'user', selectedCharacter: '米浴', sessionId: 'session', capabilities: { dialogue_memory: 1 } });
  return { store, saved, imports };
}

test('JSON and Markdown exports/imports round-trip memory and verbatim model replies', async () => {
  const { store, imports } = await setup();
  await store.importConversationMessages(records, 'test', checkpoint);
  assert.equal(imports[0][4], checkpoint);
  const json = store.buildConversationJson();
  assert.equal(JSON.parse(json).context_checkpoint.summary, checkpoint.summary);
  assert.equal(JSON.parse(json).messages[1].modelContent, records[1].model_content);
  for (const contents of [json, store.buildConversationMarkdown()]) {
    const target = await setup();
    await target.store.importConversationFile({ name: 'history', text: async () => contents });
    assert.equal(target.imports[0][4].summary, checkpoint.summary);
    assert.equal(target.store.messages[1].modelContent, records[1].model_content);
  }
});

test('HF empty history restores browser archive plus memory automatically', async () => {
  const { store, imports } = await setup({}, { read: async () => ({ messages: records, context_checkpoint: checkpoint }) });
  await store.refreshHistory();
  assert.equal(imports.length, 1);
  assert.equal(imports[0][3], 'browser_recovery');
  assert.equal(imports[0][4], checkpoint);
  assert.equal(store.messages.length, 2);
  assert.equal(store.contextCheckpoint.summary, checkpoint.summary);
});

test('memory progress does not become dialogue and completion caches the checkpoint', async () => {
  const { store, saved } = await setup({ chatStream: async (_session, _text, _voice, emit) => {
    emit({ type: 'context_status', data: { phase: 'compacting', chunk: 1, chunks: 2 } });
    assert.match(store.compactionStatus, /1\/2/);
    emit({ type: 'context_status', data: { phase: 'compacted', checkpoint } });
    emit({ type: 'structured_reply', data: { message: records[1] } });
    emit({ type: 'done' });
  } });
  await store.sendMessage('你好');
  assert.equal(store.messages.length, 2);
  assert.equal(store.messages[1].dialogue, '约好了');
  assert.equal(store.compactionStatus, '');
  assert.equal(store.isLoading, false);
  assert.equal(saved.at(-1).context_checkpoint.summary, checkpoint.summary);
});

test('failed browser persistence is visible and does not delete the conversation', async () => {
  const { store } = await setup({}, { write: async () => { throw new Error('quota exceeded'); } });
  await store.importConversationMessages(records, 'test', checkpoint);
  await store._cacheCurrentConversation();
  assert.match(store.cacheWarning, /未保存/);
  assert.equal(store.messages.length, 2);
});

test('backend rejection of stale memory clears the browser checkpoint on import', async () => {
  const { store } = await setup({ importHistory: async () => ({ context_checkpoint: null }) });
  await store.importConversationMessages(records, 'test', checkpoint);
  assert.equal(store.contextCheckpoint, null);
  assert.equal(store.messages.length, 2);
});

test('clearing history does not auto-resurrect a cache whose deletion failed', async () => {
  const { store, imports } = await setup({}, {
    read: async () => ({ messages: records, context_checkpoint: checkpoint }),
    remove: async () => { throw new Error('denied'); },
  });
  await store.clearCurrentCharacterHistory();
  assert.equal(imports.length, 0);
  assert.equal(store.messages.length, 0);
});

test('disabled browser storage does not block server history loading', async () => {
  const { store } = await setup({ fetchHistory: async () => ({ messages: records }) }, {
    read: async () => { throw new Error('denied'); },
    write: async () => { throw new Error('denied'); },
  });
  await store.refreshHistory();
  assert.equal(store.messages.length, 2);
  assert.ok(store.cacheWarning);
});

test('Markdown memory containing inline fences still imports from the authoritative JSON block', async () => {
  const source = await setup();
  const memory = { ...checkpoint, summary: '约定保留符号 ```json 和其他原话' };
  await source.store.importConversationMessages(records, 'test', memory);
  const target = await setup();
  await target.store.importConversationFile({ name: 'history.md', text: async () => source.store.buildConversationMarkdown() });
  assert.equal(target.imports[0][4].summary, memory.summary);
});

test('failed compaction restores the unaccepted draft instead of caching it as history', async () => {
  const { store, saved } = await setup({
    fetchDialogueContext: async () => ({ context_checkpoint: null, history_size: 2, busy: false }),
    chatStream: async (_session, _text, _voice, emit) => emit({ type: 'error', data: '历史压缩未完成' }),
  });
  await store.importConversationMessages(records);
  await store.sendMessage('不能丢的本次输入');
  assert.equal(store.messages.length, 2);
  assert.equal(store.failedDraft, '不能丢的本次输入');
  assert.equal(saved.at(-1).messages.length, 2);
  assert.equal(store.isLoading, false);
});

test('multiple compression snapshots survive cache and JSON/Markdown round trips without becoming messages', async () => {
  const first = { ...checkpoint, checkpoint_id: 'one', trigger_message_count: 2 };
  const second = { ...checkpoint, checkpoint_id: 'two', revision: 2, trigger_message_count: 4, summary: '第二次整理' };
  const source = await setup();
  await source.store.importConversationMessages([...records, ...records], 'test', second, [first, second]);
  await source.store._cacheCurrentConversation();
  assert.equal(source.saved.at(-1).context_checkpoints.length, 2);
  assert.equal(source.saved.at(-1).messages.length, 4);
  for (const text of [source.store.buildConversationJson(), source.store.buildConversationMarkdown()]) {
    const target = await setup();
    await target.store.importConversationFile({ name: 'history', text: async () => text });
    assert.equal(target.store.contextCheckpoints.length, 2);
    assert.equal(target.store.contextCheckpoints[0].trigger_message_count, 2);
    assert.equal(target.store.contextCheckpoints[1].trigger_message_count, 4);
    assert.equal(target.store.messages.length, 4);
    assert.equal(target.imports[0][5].length, 2);
  }
});

test('SSE checkpoint redelivery is idempotent and keeps earlier summaries', async () => {
  const { store } = await setup();
  const first = { ...checkpoint, checkpoint_id: 'one' };
  const second = { ...checkpoint, checkpoint_id: 'two', revision: 2 };
  store._rememberCheckpoint(first);
  store._rememberCheckpoint(second);
  store._rememberCheckpoint(second);
  assert.equal(store.contextCheckpoints.length, 2);
  assert.equal(store.contextCheckpoint.checkpoint_id, 'two');
  store._applyMemorySnapshot({ context_checkpoint: null, context_checkpoints: [] });
  assert.equal(store.contextCheckpoints.length, 0);
});
