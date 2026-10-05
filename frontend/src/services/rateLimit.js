// Match the server's two existing buckets. Cooldowns are tab-local and never
// persisted; expiry permits a manual retry, never a replay of a POST request.
const CHAT_PATHS = new Set(['/chat', '/chat_stream', '/director/turn', '/director/turn_stream', '/stage/turn']);
const bucketFor = (url) => {
  const path = new URL(url, 'http://local').pathname;
  if (path === '/' || path === '/audio' || (path.startsWith('/tts/jobs/') && path.endsWith('/audio'))) return null;
  return CHAT_PATHS.has(path) ? 'chat' : 'default';
};

export function createRateLimitTracker(now = Date.now) {
  const deadlines = new Map();
  const listeners = new Set();
  const remaining = (bucket) => Math.max(0, Math.ceil(((deadlines.get(bucket) || 0) - now()) / 1000));
  const makeError = (bucket) => Object.assign(
    new Error(`请求过于频繁，请等待 ${remaining(bucket)} 秒后手动重试（不会自动发送）。`),
    { code: 'RATE_LIMIT', status: 429, retryAfter: remaining(bucket), retryAt: deadlines.get(bucket), bucket },
  );
  return {
    check(url) {
      const bucket = bucketFor(url);
      if (remaining(bucket)) throw makeError(bucket);
    },
    record(url, header, bodySeconds) {
      let seconds;
      if (header != null && String(header).trim()) {
        seconds = Number(header);
        if (!Number.isFinite(seconds)) seconds = (Date.parse(header) - now()) / 1000;
      }
      if (!Number.isFinite(seconds) || seconds < 0) seconds = Number(bodySeconds);
      if (!Number.isFinite(seconds) || seconds < 0) seconds = 60;
      const bucket = bucketFor(url) || 'default';
      deadlines.set(bucket, Math.max(deadlines.get(bucket) || 0, now() + Math.max(1, Math.ceil(seconds)) * 1000));
      listeners.forEach((listener) => listener());
      return makeError(bucket);
    },
    snapshot() {
      return [...deadlines.keys()].map((bucket) => ({ bucket, seconds: remaining(bucket) }))
        .filter((item) => item.seconds > 0);
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

export const rateLimits = createRateLimitTracker();
