// frontend/src/stores/chatStore.js
import { defineStore } from 'pinia';
import {
  API_BASE_URL,
  fetchCharacters,
  loadCharacter,
  chatOnce,
  chatStream,
  fetchHistory,
  importHistory,
  clearHistory,
} from '@/services/api';

const USER_UUID_STORAGE_KEY = 'umamusume_user_uuid';
const HISTORY_CACHE_PREFIX = 'umamusume_history_cache_v1';
const TTS_ENABLED = import.meta.env.VITE_ENABLE_TTS === 'true';

const getOrCreateUserUuid = () => {
  const cached = localStorage.getItem(USER_UUID_STORAGE_KEY);
  if (cached) {
    return cached;
  }
  const generated = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : `fallback-${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 10)}`;
  localStorage.setItem(USER_UUID_STORAGE_KEY, generated);
  return generated;
};

const createMessage = (role, content, extra = {}) => ({
  id: extra.id || `${role}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
  role,
  content,
  voice: extra.voice || null,
  status: extra.status || 'ready',
  renderMode: extra.renderMode || 'structured',
  createdAt: extra.createdAt || new Date().toISOString(),
});

const resolveAudioUrl = (url) => {
  if (!url) {
    return '';
  }
  if (url.startsWith('http://') || url.startsWith('https://')) {
    return url;
  }
  if (url.startsWith('/')) {
    return `${API_BASE_URL}${url}`;
  }
  return url;
};

const normalizeMarkdownText = (text) => {
  const normalized = String(text || '').replace(/\r\n/g, '\n').trim();
  return normalized || '_空消息_';
};

const sanitizeFilenamePart = (value) => {
  const sanitized = String(value || 'conversation')
    .trim()
    .replace(/[\\/:*?"<>|]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .slice(0, 80);
  return sanitized || 'conversation';
};

const markdownTimestamp = () => new Date().toISOString().replace(/[:.]/g, '-');

const writeTextToClipboard = async (text) => {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch (_err) {
      // Fall back for browsers that expose Clipboard API but block it by policy.
    }
  }

  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  textarea.style.top = '0';
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();

  const copied = document.execCommand('copy');
  document.body.removeChild(textarea);
  if (!copied) {
    throw new Error('当前浏览器不允许写入剪贴板。');
  }
};

const downloadMarkdownFile = (filename, text) => {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
};

const historyCacheKey = (userUuid, characterName) => (
  `${HISTORY_CACHE_PREFIX}:${encodeURIComponent(userUuid || '')}:${encodeURIComponent(characterName || '')}`
);

const normalizeConversationRecords = (records) => {
  if (!Array.isArray(records)) {
    return [];
  }

  return records
    .map((record) => {
      const rawRole = String(record?.role || record?.speaker || '').trim().toLowerCase();
      const role = rawRole === 'assistant' || rawRole === '角色' ? 'assistant' : rawRole === 'user' || rawRole === '训练员' ? 'user' : '';
      const content = String(record?.content || record?.text || '').trim();
      if (!role || !content) {
        return null;
      }
      return {
        role,
        content,
        timestamp: record?.timestamp || record?.createdAt || new Date().toISOString(),
      };
    })
    .filter(Boolean);
};

const messageToRecord = (message) => ({
  role: message.role === 'assistant' ? 'assistant' : 'user',
  content: message.content || '',
  timestamp: message.createdAt || new Date().toISOString(),
});

const readHistoryCache = (userUuid, characterName) => {
  if (!userUuid || !characterName) {
    return { savedAt: '', messages: [] };
  }

  try {
    const raw = localStorage.getItem(historyCacheKey(userUuid, characterName));
    if (!raw) {
      return { savedAt: '', messages: [] };
    }
    const payload = JSON.parse(raw);
    return {
      savedAt: payload?.savedAt || '',
      messages: normalizeConversationRecords(payload?.messages || []),
    };
  } catch (_err) {
    return { savedAt: '', messages: [] };
  }
};

const writeHistoryCache = (userUuid, characterName, messages) => {
  if (!userUuid || !characterName) {
    return;
  }
  const records = normalizeConversationRecords(messages);
  const payload = {
    version: 1,
    userUuid,
    characterName,
    savedAt: new Date().toISOString(),
    messages: records,
  };
  localStorage.setItem(historyCacheKey(userUuid, characterName), JSON.stringify(payload));
};

const removeHistoryCache = (userUuid, characterName) => {
  if (!userUuid || !characterName) {
    return;
  }
  localStorage.removeItem(historyCacheKey(userUuid, characterName));
};

const parseMarkdownConversation = (text) => {
  const records = [];
  const lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
  let currentRole = '';
  let currentContent = [];

  const pushCurrent = () => {
    const content = currentContent.join('\n').trim();
    if (currentRole && content) {
      records.push({ role: currentRole, content });
    }
    currentContent = [];
  };

  lines.forEach((line) => {
    const headingMatch = line.match(/^###\s+\d+\.\s+(.+?)(?:（[^）]*）)?\s*$/);
    if (headingMatch) {
      pushCurrent();
      const speaker = (headingMatch[1] || '').trim().toLowerCase();
      currentRole = speaker === '训练员' || speaker === 'user' ? 'user' : 'assistant';
      return;
    }

    if (currentRole) {
      currentContent.push(line);
    }
  });

  pushCurrent();
  return normalizeConversationRecords(records);
};

const parseImportedConversationText = (text) => {
  const rawText = String(text || '').trim();
  if (!rawText) {
    return [];
  }

  try {
    const payload = JSON.parse(rawText);
    const records = normalizeConversationRecords(Array.isArray(payload) ? payload : payload?.messages);
    if (records.length) {
      return records;
    }
  } catch (_err) {
    // Fall through to Markdown parsing.
  }

  return parseMarkdownConversation(rawText);
};

export const useChatStore = defineStore('chat', {
  state: () => ({
    characters: [],
    userUuid: '',
    selectedCharacter: '',
    sessionId: '',
    systemPrompt: '',
    voicePreviewUrl: '',
    outputDir: '',
    restoredHistoryMessages: 0,
    historyCharacters: [],
    messages: [],
    isLoading: false,
    error: null,
    exportNotice: '',
    cachedMessageCount: 0,
    cachedSavedAt: '',
    streamMode: true,
    voiceEnabled: false,
    currentAssistantId: '',
    voicePollers: {},
  }),

  actions: {
    async initCharacters() {
      this.userUuid = getOrCreateUserUuid();
      try {
        const data = await fetchCharacters();
        this.characters = data.characters || [];
      } catch (err) {
        this.error = err.message || '获取角色失败。';
      }
    },

    async selectCharacter(name, forceRebuild = false) {
      if (!name) {
        return;
      }
      this.isLoading = true;
      this.error = null;
      try {
        const userUuid = this.userUuid || getOrCreateUserUuid();
        const data = await loadCharacter(name, forceRebuild, userUuid);
        if (data.error) {
          this.error = data.error;
          return;
        }
        this.userUuid = data.user_uuid || userUuid;
        localStorage.setItem(USER_UUID_STORAGE_KEY, this.userUuid);
        this.selectedCharacter = name;
        this.sessionId = data.session_id || '';
        this.systemPrompt = data.system_prompt || '';
        this.voicePreviewUrl = TTS_ENABLED ? resolveAudioUrl(data.voice_preview_url || '') : '';
        this.outputDir = data.output_dir || '';
        this.restoredHistoryMessages = Number(data.restored_history_messages || 0);
        this.exportNotice = '';
        this._refreshCacheInfo(name);
        this._clearVoicePollers();
        await this.refreshHistory(name);
      } catch (err) {
        this.error = err.message || '加载角色失败。';
      } finally {
        this.isLoading = false;
      }
    },

    setStreamMode(value) {
      this.streamMode = value;
    },

    setVoiceEnabled(value) {
      this.voiceEnabled = TTS_ENABLED ? value : false;
    },

    _refreshCacheInfo(characterName = this.selectedCharacter) {
      const cache = readHistoryCache(this.userUuid, characterName);
      this.cachedMessageCount = cache.messages.length;
      this.cachedSavedAt = cache.savedAt;
    },

    _cacheCurrentConversation() {
      if (!this.userUuid || !this.selectedCharacter || !this.messages.length) {
        this._refreshCacheInfo();
        return;
      }

      try {
        writeHistoryCache(
          this.userUuid,
          this.selectedCharacter,
          this.messages.map(messageToRecord)
        );
      } catch (_err) {
        // Local storage can be full or disabled; backend history remains the source of truth.
      }
      this._refreshCacheInfo();
    },

    _removeCurrentConversationCache() {
      removeHistoryCache(this.userUuid, this.selectedCharacter);
      this._refreshCacheInfo();
    },

    _clearVoicePollers() {
      Object.values(this.voicePollers).forEach((timerId) => clearInterval(timerId));
      this.voicePollers = {};
    },

    async _checkAudioReady(audioUrl) {
      if (!audioUrl) {
        return false;
      }
      try {
        const head = await fetch(audioUrl, { method: 'HEAD' });
        if (head.ok) {
          return true;
        }
        if (head.status !== 405) {
          return false;
        }
      } catch (_err) {
        // fall through to GET
      }

      try {
        const resp = await fetch(audioUrl, {
          method: 'GET',
          headers: { Range: 'bytes=0-1' },
        });
        return resp.ok;
      } catch (_err) {
        return false;
      }
    },

    _startVoicePolling(message) {
      if (!message?.voice?.audio_url) {
        return;
      }
      const messageId = message.id;
      if (this.voicePollers[messageId]) {
        return;
      }
      let attempts = 0;
      const maxAttempts = 300;
      const intervalMs = 1000;

      const timerId = setInterval(async () => {
        attempts += 1;
        const ready = await this._checkAudioReady(message.voice.audio_url);
        if (ready) {
          message.voice.status = 'ready';
          clearInterval(timerId);
          delete this.voicePollers[messageId];
          return;
        }
        if (attempts >= maxAttempts) {
          clearInterval(timerId);
          delete this.voicePollers[messageId];
        }
      }, intervalMs);

      this.voicePollers[messageId] = timerId;
    },

    _toHistoryMessages(records) {
      if (!Array.isArray(records)) {
        return [];
      }
      return records.map((record, index) => {
        const role = record.role === 'assistant' ? 'assistant' : 'user';
        const createdAt = record.timestamp || new Date().toISOString();
        const id = [
          'history',
          record.session_id || 'session',
          record.message_index ?? index,
          index,
        ].join('-');
        return createMessage(role, record.content || '', {
          id,
          createdAt,
          renderMode: role === 'assistant' ? 'structured' : 'structured',
          status: 'ready',
        });
      });
    },

    async refreshHistory(characterName = this.selectedCharacter) {
      if (!this.userUuid || !characterName) {
        return;
      }
      try {
        const data = await fetchHistory(this.userUuid, characterName, 0);
        this.historyCharacters = Array.isArray(data.characters) ? data.characters : [];
        this.messages = this._toHistoryMessages(data.messages || []);
        if (this.messages.length) {
          this._cacheCurrentConversation();
        } else {
          this._refreshCacheInfo(characterName);
        }
      } catch (err) {
        this.error = err.message || '读取历史失败。';
        this._refreshCacheInfo(characterName);
      }
    },

    async clearCurrentCharacterHistory() {
      if (!this.userUuid || !this.selectedCharacter) {
        this.error = '请先选择角色。';
        return;
      }
      this.isLoading = true;
      this.error = null;
      try {
        const result = await clearHistory(this.userUuid, this.selectedCharacter);
        if (result.error) {
          this.error = result.error;
          return;
        }
        this.messages = [];
        this.restoredHistoryMessages = 0;
        this.exportNotice = '';
        this._removeCurrentConversationCache();
        await this.refreshHistory(this.selectedCharacter);
      } catch (err) {
        this.error = err.message || '清理历史失败。';
      } finally {
        this.isLoading = false;
      }
    },

    async sendMessage(text) {
      if (!text.trim()) {
        this.error = '请输入内容。';
        return;
      }
      if (!this.sessionId) {
        this.error = '请先加载角色。';
        return;
      }

      const userMessage = createMessage('user', text);
      this.messages.push(userMessage);
      this.error = null;
      this.exportNotice = '';

      if (this.streamMode) {
        const assistantMessage = createMessage('assistant', '', {
          status: 'streaming',
          renderMode: 'raw',
        });
        this.messages.push(assistantMessage);
        this.currentAssistantId = assistantMessage.id;
        this.isLoading = true;

        await chatStream(
          this.sessionId,
          text,
          TTS_ENABLED && this.voiceEnabled,
          (event) => {
            const { type, data } = event;
            if (!this.currentAssistantId) {
              return;
            }
            const target = this.messages.find((msg) => msg.id === this.currentAssistantId);
            if (!target) {
              return;
            }

            if (type === 'token') {
              target.content += data || '';
            } else if (type === 'voice_pending' && TTS_ENABLED) {
              target.voice = {
                status: 'pending',
                audio_url: resolveAudioUrl(data.audio_url),
                audio_path: data.audio_path,
              };
              this._startVoicePolling(target);
            } else if (type === 'done') {
              target.renderMode = 'structured';
              target.status = 'ready';
              this.isLoading = false;
              this._cacheCurrentConversation();
            } else if (type === 'error') {
              this.error = data || '流式对话发生错误。';
              target.status = 'ready';
              this.isLoading = false;
            }
          }
        );
      } else {
        this.isLoading = true;
        try {
          const data = await chatOnce(this.sessionId, text, TTS_ENABLED && this.voiceEnabled);
          if (data.error) {
            this.error = data.error;
          } else {
            const assistantMessage = createMessage('assistant', data.reply || '', {
              renderMode: 'structured',
            });
            if (TTS_ENABLED && data.voice) {
              assistantMessage.voice = {
                status: 'ready',
                audio_url: resolveAudioUrl(data.voice.audio_url),
                audio_path: data.voice.audio_path,
              };
            }
            this.messages.push(assistantMessage);
            this._cacheCurrentConversation();
          }
        } catch (err) {
          this.error = err.message || '对话失败。';
        } finally {
          this.isLoading = false;
        }
      }
    },

    clearMessages() {
      this.messages = [];
      this._clearVoicePollers();
    },

    async importConversationMessages(records, source = 'manual') {
      this.error = null;
      this.exportNotice = '';
      if (!this.selectedCharacter || !this.sessionId) {
        this.error = '请先加载角色。';
        return false;
      }

      const normalizedRecords = normalizeConversationRecords(records);
      if (!normalizedRecords.length) {
        this.exportNotice = '未找到可导入的对话消息。';
        return false;
      }

      let backendSynced = false;
      try {
        await importHistory(this.sessionId, normalizedRecords, true, source);
        backendSynced = true;
      } catch (err) {
        this.error = `已导入到浏览器缓存，但同步后端失败，后续 LLM 不会使用这份历史：${err.message || err}`;
      }

      this.messages = normalizedRecords.map((record, index) => createMessage(record.role, record.content, {
        id: `import-${Date.now()}-${index}`,
        createdAt: record.timestamp,
        renderMode: 'structured',
        status: 'ready',
      }));
      this.restoredHistoryMessages = this.messages.length;
      this._cacheCurrentConversation();
      if (backendSynced) {
        this.exportNotice = `已导入 ${this.messages.length} 条历史，并同步到当前会话上下文。`;
      }
      return backendSynced;
    },

    async importFromBrowserCache() {
      this.error = null;
      this.exportNotice = '';
      const cache = readHistoryCache(this.userUuid, this.selectedCharacter);
      if (!cache.messages.length) {
        this.exportNotice = '当前角色没有可导入的浏览器缓存。';
        this._refreshCacheInfo();
        return false;
      }
      return this.importConversationMessages(cache.messages, 'browser_cache');
    },

    async importConversationFile(file) {
      this.error = null;
      this.exportNotice = '';
      if (!file) {
        return false;
      }

      try {
        const text = await file.text();
        const records = parseImportedConversationText(text);
        return await this.importConversationMessages(records, `file:${file.name || 'history'}`);
      } catch (err) {
        this.error = err.message || '导入历史文件失败。';
        return false;
      }
    },

    buildConversationMarkdown() {
      const characterName = this.selectedCharacter || '未选择角色';
      const exportedAt = new Date().toLocaleString();
      const lines = [
        `# ${characterName} 对话记录`,
        '',
        `- 导出时间：${exportedAt}`,
        `- 角色：${characterName}`,
        `- 消息数：${this.messages.length}`,
        '',
        '## 对话',
        '',
      ];

      this.messages.forEach((message, index) => {
        const speaker = message.role === 'assistant' ? characterName : '训练员';
        const status = message.status === 'streaming' ? '（生成中）' : '';
        lines.push(`### ${index + 1}. ${speaker}${status}`);
        lines.push('');
        lines.push(normalizeMarkdownText(message.content));
        lines.push('');
      });

      return lines.join('\n').replace(/\n{3,}/g, '\n\n').trimEnd() + '\n';
    },

    async copyConversationMarkdown() {
      this.error = null;
      this.exportNotice = '';
      if (!this.messages.length) {
        this.exportNotice = '当前没有可复制的对话。';
        return false;
      }

      try {
        await writeTextToClipboard(this.buildConversationMarkdown());
        this.exportNotice = '已复制 Markdown 到剪贴板。';
        return true;
      } catch (err) {
        this.error = err.message || '复制 Markdown 失败。';
        return false;
      }
    },

    downloadConversationMarkdown() {
      this.error = null;
      this.exportNotice = '';
      if (!this.messages.length) {
        this.exportNotice = '当前没有可下载的对话。';
        return false;
      }

      try {
        const filenameBase = sanitizeFilenamePart(this.selectedCharacter || 'umamusume-dialogue');
        const filename = `${filenameBase}-${markdownTimestamp()}.md`;
        downloadMarkdownFile(filename, this.buildConversationMarkdown());
        this.exportNotice = `已下载 ${filename}。`;
        return true;
      } catch (err) {
        this.error = err.message || '下载 Markdown 失败。';
        return false;
      }
    },
  },
});
