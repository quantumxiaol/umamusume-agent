import test from 'node:test';
import assert from 'node:assert/strict';
import { createHistoryCache } from '../src/services/historyCache.js';

const fixture = () => {
  const old = new Map();
  const data = new Map();
  const legacy = { getItem: (k) => old.get(k), removeItem: (k) => old.delete(k) };
  const storage = { async transact(mode, key, value) {
    if (mode === 'put') data.set(key, value);
    if (mode === 'delete') data.delete(key);
    return data.get(key);
  } };
  return { old, data, storage, cache: createHistoryCache({ storage, legacyStorage: () => legacy }) };
};

test('migrates legacy caches only after an atomic successful write', async () => {
  const { old, cache, data } = fixture();
  old.set('umamusume_history_cache_v2:user:%E7%B1%B3%E6%B5%B4', JSON.stringify({ messages: [{ content: '旧历史' }] }));
  assert.equal((await cache.read('user', '米浴')).messages[0].content, '旧历史');
  assert.equal(old.size, 0);
  assert.equal(data.size, 1);
});

test('failed migration retains the original cache and returns a visible warning', async () => {
  const { old, cache, storage } = fixture();
  old.set('umamusume_history_cache_v1:user:character', JSON.stringify({ messages: [{ content: '不能丢' }] }));
  storage.transact = async () => { throw new Error('quota'); };
  const saved = await cache.read('user', 'character');
  assert.equal(saved.messages[0].content, '不能丢');
  assert.ok(saved.warning);
  assert.equal(old.size, 1);
  await assert.rejects(cache.write('user', 'character', { messages: [] }), /quota/);
});

test('queued writes snapshot transcript and memory together, then delete in order', async () => {
  const { cache } = fixture();
  const first = { messages: ['one'], context_checkpoint: { revision: 1 } };
  const written = cache.write('u', 'c', first);
  first.messages.push('later mutation');
  first.context_checkpoint.revision = 99;
  await written;
  assert.deepEqual(await cache.read('u', 'c'), { messages: ['one'], context_checkpoint: { revision: 1 } });
  const writes = [cache.write('u', 'c', { messages: ['two'] }), cache.write('u', 'c', { messages: ['three'] }), cache.remove('u', 'c')];
  await Promise.all(writes);
  assert.deepEqual((await cache.read('u', 'c')).messages, []);
});

test('a failed write does not poison later saves or another browser/character key', async () => {
  const { cache, storage } = fixture();
  const transact = storage.transact;
  storage.transact = async () => { throw new Error('temporary failure'); };
  await assert.rejects(cache.write('a', 'c', { messages: ['lost'] }));
  storage.transact = transact;
  await cache.write('a', 'c', { messages: ['a'] });
  await cache.write('b', 'c', { messages: ['b'] });
  await cache.write('a', 'd', { messages: ['d'] });
  assert.deepEqual((await cache.read('a', 'c')).messages, ['a']);
  assert.deepEqual((await cache.read('b', 'c')).messages, ['b']);
  assert.deepEqual((await cache.read('a', 'd')).messages, ['d']);
});
