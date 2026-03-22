// frontend/src/stores/chatStore.js
import { defineStore } from 'pinia';
import {
  API_BASE_URL,
  fetchCharacters,
  loadCharacter,
  chatOnce,
  chatStream,
  fetchHistory,
  clearHistory,
} from '@/services/api';

const USER_UUID_STORAGE_KEY = 'umamusume_user_uuid';

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
    streamMode: true,
    voiceEnabled: true,
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
        this.voicePreviewUrl = resolveAudioUrl(data.voice_preview_url || '');
        this.outputDir = data.output_dir || '';
        this.restoredHistoryMessages = Number(data.restored_history_messages || 0);
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
      this.voiceEnabled = value;
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
        const data = await fetchHistory(this.userUuid, characterName, 400);
        this.historyCharacters = Array.isArray(data.characters) ? data.characters : [];
        this.messages = this._toHistoryMessages(data.messages || []);
      } catch (err) {
        this.error = err.message || '读取历史失败。';
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
          this.voiceEnabled,
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
            } else if (type === 'voice_pending') {
              target.voice = {
                status: 'pending',
                audio_url: resolveAudioUrl(data.audio_url),
                audio_path: data.audio_path,
              };
              this._startVoicePolling(target);
            } else if (type === 'done') {
              target.status = 'ready';
              this.isLoading = false;
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
          const data = await chatOnce(this.sessionId, text, this.voiceEnabled);
          if (data.error) {
            this.error = data.error;
          } else {
            const assistantMessage = createMessage('assistant', data.reply || '', {
              renderMode: 'structured',
            });
            if (data.voice) {
              assistantMessage.voice = {
                status: 'ready',
                audio_url: resolveAudioUrl(data.voice.audio_url),
                audio_path: data.voice.audio_path,
              };
            }
            this.messages.push(assistantMessage);
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
  },
});
