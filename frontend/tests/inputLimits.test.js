import test from 'node:test';
import assert from 'node:assert/strict';
import { characterCount, inputBatchError, assertHistorySize, assertHistoryFileSize,
  assertHistoryPayloadSize, MAX_HISTORY_BYTES } from '../src/services/inputLimits.js';

test('input boundaries count Unicode code points and the whole queued batch', () => {
  assert.equal(characterCount('中😀𠮷'), 3);
  assert.equal(inputBatchError(['😀'.repeat(10000)]), '');
  assert.match(inputBatchError(['😀'.repeat(10001)]), /10,000/);
  assert.equal(inputBatchError(['中'.repeat(5000), 'a'.repeat(5000)]), '');
  assert.match(inputBatchError(['中'.repeat(5000), 'a'.repeat(5001)]), /10,000/);
  assert.equal(inputBatchError(Array(20).fill('x')), '');
  assert.match(inputBatchError(Array(21).fill('x')), /20/);
});

test('old long messages remain valid but records, summaries and aliases are bounded', () => {
  assert.doesNotThrow(() => assertHistorySize([{ content: '中'.repeat(10001) }]));
  assert.doesNotThrow(() => assertHistorySize([{ content: '😀'.repeat(200000) }]));
  assert.throws(() => assertHistorySize([{ modelContent: 'x'.repeat(200001) }]), /modelContent/);
  assert.throws(() => assertHistorySize(Array(20001).fill({ content: 'x' })), /数量/);
  assert.throws(() => assertHistorySize([], null, Array(101).fill({ summary: 'x' })), /摘要版本/);
  assert.throws(() => assertHistorySize([], { summary: 'x'.repeat(2000001) }), /单个历史摘要/);
  assert.throws(() => assertHistorySize(Array(41).fill({ content: 'x'.repeat(200000) })), /合计/);
  assert.throws(() => assertHistorySize([{ content: '中'.repeat(50001) }], null, [], { scene: true }), /场景第/);
  assert.throws(() => assertHistorySize(Array(5001).fill({ content: 'x' }), null, [], { scene: true }), /5000/);
});

test('file and wire limits are bytes, not JavaScript string length', () => {
  assert.doesNotThrow(() => assertHistoryFileSize({ size: MAX_HISTORY_BYTES }));
  assert.throws(() => assertHistoryFileSize({ size: MAX_HISTORY_BYTES + 1 }), /32 MiB/);
  assertHistoryPayloadSize({ messages: [{ content: '小文件' }] });
  // Multibyte metadata must also count, even when ignored by the history schema.
  assert.throws(() => assertHistoryPayloadSize({ extra: '中'.repeat(Math.floor(MAX_HISTORY_BYTES / 3)) }), /32 MiB/);
});
