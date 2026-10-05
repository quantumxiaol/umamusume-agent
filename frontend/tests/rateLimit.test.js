import test from 'node:test';
import assert from 'node:assert/strict';
import { createRateLimitTracker } from '../src/services/rateLimit.js';

test('cooldowns follow the two server buckets and expire without replaying requests', () => {
  let now = 1000;
  const tracker = createRateLimitTracker(() => now);
  const error = tracker.record('https://test/chat_stream', '5');
  assert.equal(error.status, 429);
  assert.equal(error.retryAfter, 5);
  assert.match(error.message, /等待 5 秒/);
  assert.throws(() => tracker.check('/director/turn_stream'), { status: 429 });
  assert.doesNotThrow(() => tracker.check('/characters'));
  assert.doesNotThrow(() => tracker.check('/audio'));
  now = 5001;
  assert.equal(tracker.snapshot()[0].seconds, 1);
  now = 6000;
  assert.doesNotThrow(() => tracker.check('/chat'));
  assert.deepEqual(tracker.snapshot(), []);
  tracker.record('/history/import', null, 10);
  assert.throws(() => tracker.check('/usage/recent'), { retryAfter: 10 });
  assert.doesNotThrow(() => tracker.check('/chat_stream'));
});

test('Retry-After supports HTTP dates, JSON fallback, malformed responses, and rounding up', () => {
  const now = Date.parse('2026-10-05T00:00:00Z');
  for (const [header, body, expected] of [
    ['Mon, 05 Oct 2026 00:00:30 GMT', undefined, 30],
    [undefined, 12, 12], ['invalid', 15, 15], [undefined, undefined, 60],
    ['0.5', undefined, 1], ['0', undefined, 1], ['-10', undefined, 60],
  ]) {
    const tracker = createRateLimitTracker(() => now);
    assert.equal(tracker.record('/chat', header, body).retryAfter, expected);
  }
});

test('a later shorter response cannot shorten an active cooldown; notices unsubscribe', () => {
  const tracker = createRateLimitTracker(() => 0);
  let updates = 0;
  const unsubscribe = tracker.subscribe(() => { updates += 1; });
  tracker.record('/chat', '30');
  tracker.record('/chat_stream', '1');
  assert.equal(tracker.snapshot()[0].seconds, 30);
  assert.equal(updates, 2);
  unsubscribe();
  tracker.record('/characters', '10');
  assert.equal(updates, 2);
});
