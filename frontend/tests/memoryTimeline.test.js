import test from 'node:test';
import assert from 'node:assert/strict';
import { buildMemoryTimeline } from '../src/services/memoryTimeline.js';

const messages = Array.from({ length: 10 }, (_, index) => ({
  id: `m${index + 1}`, role: index % 2 ? 'assistant' : 'user', content: `消息${index + 1}`,
}));
const checkpoint = { checkpoint_id: 'one', revision: 1, covered_messages: 4, trigger_message_count: 8, summary: '记忆' };

test('marker appears where compression happened, not at the covered-prefix boundary', () => {
  const rows = buildMemoryTimeline(messages, [checkpoint]);
  const index = rows.findIndex((row) => row.kind === 'memory');
  assert.equal(rows[index - 1].id, 'm8');
  assert.equal(rows[index + 1].id, 'm9');
  assert.equal(rows[index].locationKnown, true);
  assert.equal(rows[index].checkpoint.covered_messages, 4);
});

test('multiple markers preserve original message order and do not mutate the archive', () => {
  const before = JSON.stringify(messages);
  const second = { ...checkpoint, checkpoint_id: 'two', revision: 2, covered_messages: 8, trigger_message_count: 10 };
  const rows = buildMemoryTimeline(messages, [checkpoint, second, second]);
  assert.deepEqual(rows.filter((row) => row.kind === 'message').map((row) => row.id), messages.map((m) => m.id));
  assert.equal(rows.filter((row) => row.kind === 'memory').length, 2);
  assert.equal(rows.at(-1).checkpoint.checkpoint_id, 'two');
  assert.equal(JSON.stringify(messages), before);
});

test('legacy or cropped trigger positions explicitly fall back to covered boundary', () => {
  for (const trigger of [null, undefined, 100, -1, 2]) {
    const rows = buildMemoryTimeline(messages, [{ ...checkpoint, trigger_message_count: trigger }]);
    const index = rows.findIndex((row) => row.kind === 'memory');
    assert.equal(rows[index].locationKnown, false);
    assert.equal(rows[index - 1].id, 'm4');
  }
});

test('empty streaming placeholders do not shift server message indices', () => {
  const withPlaceholder = [...messages.slice(0, 2), { id: 'empty', role: 'assistant', content: '' }, ...messages.slice(2)];
  const rows = buildMemoryTimeline(withPlaceholder, [checkpoint]);
  const index = rows.findIndex((row) => row.kind === 'memory');
  assert.equal(rows[index - 1].id, 'm8');
});

test('missing history and invalid checkpoints do not invent markers', () => {
  assert.equal(buildMemoryTimeline([], [checkpoint]).length, 0);
  const invalid = [0, -1, 100, 1.5, '4'].map((covered_messages) => ({ ...checkpoint, covered_messages }));
  invalid.push({ ...checkpoint, summary: '' });
  assert.equal(buildMemoryTimeline(messages, invalid).length, messages.length);
});
