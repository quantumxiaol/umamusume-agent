// One atomic record contains the full transcript and its matching checkpoint.
// No audio bytes or playback URLs belong in this store.
export const indexedHistoryStorage = {
  async transact(mode, key, value) {
    const db = await new Promise((resolve, reject) => {
      const request = indexedDB.open('umamusume-dialogue-history', 1);
      let blocked = false;
      request.onupgradeneeded = () => request.result.createObjectStore('histories');
      request.onsuccess = () => {
        if (blocked) request.result.close();
        else resolve(request.result);
      };
      request.onerror = () => reject(request.error);
      request.onblocked = () => {
        blocked = true;
        reject(new Error('历史数据库被旧页面占用，请关闭其他标签页后重试。'));
      };
    });
    try {
      return await new Promise((resolve, reject) => {
        const tx = db.transaction('histories', mode === 'get' ? 'readonly' : 'readwrite');
        const store = tx.objectStore('histories');
        const request = mode === 'get' ? store.get(key)
          : mode === 'put' ? store.put(value, key) : store.delete(key);
        tx.oncomplete = () => resolve(request.result);
        tx.onabort = () => reject(tx.error || new Error('浏览器历史保存事务被取消。'));
        tx.onerror = () => reject(tx.error || new Error('浏览器历史保存失败。'));
      });
    } finally {
      db.close();
    }
  },
};

export const createHistoryCache = ({ storage = indexedHistoryStorage, legacyStorage = () => localStorage } = {}) => {
  const pending = new Map();
  const keyFor = (user, character) => JSON.stringify([user, character]);
  const oldKeys = (user, character) => [2, 1].map((version) => (
    `umamusume_history_cache_v${version}:${encodeURIComponent(user)}:${encodeURIComponent(character)}`
  ));
  const serial = (key, operation) => {
    const next = (pending.get(key) || Promise.resolve()).catch(() => {}).then(operation);
    pending.set(key, next);
    next.finally(() => { if (pending.get(key) === next) pending.delete(key); }).catch(() => {});
    return next;
  };
  const cleanLegacy = (user, character) => {
    for (const key of oldKeys(user, character)) legacyStorage().removeItem(key);
  };
  return {
    read(user, character) {
      return serial(keyFor(user, character), async () => {
        let warning = '';
        try {
          const saved = await storage.transact('get', keyFor(user, character));
          if (saved) return saved;
        } catch (_err) {
          warning = '无法使用大容量浏览器缓存，请导出历史备份。';
        }
        for (const key of oldKeys(user, character)) {
          const raw = legacyStorage().getItem(key);
          if (!raw) continue;
          const saved = JSON.parse(raw);
          try {
            await storage.transact('put', keyFor(user, character), saved);
            cleanLegacy(user, character);
            warning = '';
          } catch (_err) {
            warning = '旧历史缓存尚未迁移成功，已保留原缓存，请导出备份。';
          }
          return { ...saved, warning };
        }
        return { messages: [], savedAt: '', warning };
      });
    },
    write(user, character, payload) {
      // Snapshot Vue proxies before awaiting; later UI mutations cannot pair an
      // old transcript with a newer checkpoint in an outstanding write.
      const snapshot = JSON.parse(JSON.stringify(payload));
      return serial(keyFor(user, character), async () => {
        await storage.transact('put', keyFor(user, character), snapshot);
        try { cleanLegacy(user, character); } catch (_err) { /* IDB is committed. */ }
      });
    },
    remove(user, character) {
      return serial(keyFor(user, character), async () => {
        await storage.transact('delete', keyFor(user, character));
        cleanLegacy(user, character);
      });
    },
  };
};

export const dialogueHistoryCache = createHistoryCache();
