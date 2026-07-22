// frontend/src/stores/chatStore.js
import { defineStore } from 'pinia';
import {
  API_BASE_URL,
  fetchCharacters,
  fetchCapabilities,
  loadCharacter,
  chatOnce,
  chatStream,
  fetchHistory,
  importHistory,
  clearHistory,
} from '@/services/api';

const USER_UUID_STORAGE_KEY = 'umamusume_user_uuid';
const HISTORY_CACHE_PREFIX_V1 = 'umamusume_history_cache_v1';
const HISTORY_CACHE_PREFIX_V2 = 'umamusume_history_cache_v2';
const HISTORY_SCHEMA_VERSION = 2;
const EVENT_SCHEMA_VERSION = 1;
const TTS_ENABLED = import.meta.env.VITE_ENABLE_TTS === 'true';

export const DIALOGUE_INPUT_MODES = Object.freeze({
  dialogue: {
    eventType: 'dialogue',
    label: '训练员对白',
    shortLabel: '对白',
    placeholder: '输入训练员要说的话，Enter 发送，Shift+Enter 换行',
    speaker: {
      actor_id: 'player',
      actor_type: 'trainer',
      display_name: '训练员',
      role_in_scene: 'trainer',
    },
  },
  action: {
    eventType: 'action',
    label: '训练员动作',
    shortLabel: '动作',
    placeholder: '描述训练员的动作，例如：把毛巾递给她。',
    speaker: {
      actor_id: 'player',
      actor_type: 'trainer',
      display_name: '训练员',
      role_in_scene: 'trainer',
    },
  },
  scene_event: {
    eventType: 'scene_event',
    label: '环境事件',
    shortLabel: '环境',
    placeholder: '描述时间、天气或环境变化，例如：夜幕降临，开始下起小雨。',
    speaker: {
      actor_id: 'narrator',
      actor_type: 'narrator',
      display_name: '环境',
      role_in_scene: 'environment',
    },
  },
});

const normalizeActor = (value) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  const actorId = String(value.actor_id || value.actorId || '').trim();
  const actorType = String(value.actor_type || value.actorType || '').trim();
  const displayName = String(value.display_name || value.displayName || '').trim();
  if (!actorId || !actorType || !displayName) {
    return null;
  }
  return {
    actor_id: actorId,
    actor_type: actorType,
    display_name: displayName,
    ...(value.character_id || value.characterId ? {
      character_id: value.character_id || value.characterId,
    } : {}),
    ...(value.role_in_scene || value.roleInScene ? {
      role_in_scene: value.role_in_scene || value.roleInScene,
    } : {}),
  };
};

const inputModeFromEvent = (actor, eventType) => {
  if (eventType === 'scene_event' || eventType === 'narration' || actor?.actor_type === 'narrator') {
    return 'scene_event';
  }
  if (eventType === 'action') {
    return 'action';
  }
  return 'dialogue';
};

const createDialogueEvent = (inputMode) => {
  const normalizedMode = DIALOGUE_INPUT_MODES[inputMode] ? inputMode : 'dialogue';
  const preset = DIALOGUE_INPUT_MODES[normalizedMode];
  return {
    inputMode: normalizedMode,
    speaker: { ...preset.speaker },
    event_type: preset.eventType,
  };
};

const createQueuedEvent = (content, inputMode) => {
  const event = createDialogueEvent(inputMode);
  return {
    id: `queued-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    content: String(content || '').trim(),
    ...event,
  };
};

const queuedEventToRequest = (event) => ({
  content: event.content,
  speaker: event.speaker,
  event_type: event.event_type,
  ...(event.target_actor_ids?.length ? {
    target_actor_ids: [...event.target_actor_ids],
  } : {}),
});

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
  action: extra.action || '',
  dialogue: extra.dialogue || (role === 'assistant' ? content : ''),
  legacyReply: extra.legacyReply || '',
  voice: extra.voice || null,
  status: extra.status || 'ready',
  renderMode: extra.renderMode || 'structured',
  createdAt: extra.createdAt || new Date().toISOString(),
  schemaVersion: extra.schemaVersion || extra.schema_version || (role === 'assistant' ? HISTORY_SCHEMA_VERSION : undefined),
  sourceFormat: extra.sourceFormat || extra.source_format || (role === 'assistant' ? 'legacy_text' : 'text'),
  actor: normalizeActor(extra.actor || extra.speaker),
  eventType: extra.eventType || extra.event_type || '',
  targetActorIds: Array.isArray(extra.targetActorIds || extra.target_actor_ids)
    ? [...(extra.targetActorIds || extra.target_actor_ids)]
    : [],
  eventSchemaVersion: extra.eventSchemaVersion || extra.event_schema_version || undefined,
  inputMode: extra.inputMode || inputModeFromEvent(
    normalizeActor(extra.actor || extra.speaker),
    extra.eventType || extra.event_type || '',
  ),
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

const downloadTextFile = (filename, text, mimeType = 'text/plain;charset=utf-8') => {
  const blob = new Blob([text], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
};

const historyCacheKey = (prefix, userUuid, characterName) => (
  `${prefix}:${encodeURIComponent(userUuid || '')}:${encodeURIComponent(characterName || '')}`
);

const normalizeRole = (value) => {
  const rawRole = String(value || '').trim().toLowerCase();
  if (rawRole === 'assistant' || rawRole === '角色') {
    return 'assistant';
  }
  if (rawRole === 'user' || rawRole === '训练员') {
    return 'user';
  }
  return '';
};

const eventMetadataFromRecord = (record) => {
  const speaker = typeof record?.speaker === 'object' ? record.speaker : null;
  const actor = normalizeActor(record?.actor || speaker);
  const eventType = String(record?.eventType || record?.event_type || '').trim();
  const rawTargetActorIds = record?.targetActorIds || record?.target_actor_ids;
  const targetActorIds = Array.isArray(rawTargetActorIds)
    ? rawTargetActorIds.map((value) => String(value || '').trim()).filter(Boolean)
    : [];
  const eventSchemaVersion = record?.eventSchemaVersion || record?.event_schema_version;

  return {
    ...(actor ? { actor } : {}),
    ...(eventType ? { event_type: eventType } : {}),
    ...(targetActorIds.length ? { target_actor_ids: targetActorIds } : {}),
    ...(eventSchemaVersion ? { event_schema_version: eventSchemaVersion } : {}),
  };
};

const parseJsonObjectText = (text) => {
  const rawText = String(text || '').trim();
  if (!rawText) {
    return null;
  }

  const candidates = [rawText];
  const fenced = rawText.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  if (fenced) {
    candidates.push((fenced[1] || '').trim());
  }

  for (const candidate of candidates) {
    try {
      const payload = JSON.parse(candidate);
      if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
        return payload;
      }
    } catch (_err) {
      // Try the next candidate.
    }
  }
  return null;
};

const splitLegacyAssistantText = (text) => {
  const rawText = String(text || '').trim();
  if (!rawText) {
    return { action: '无', dialogue: '', sourceFormat: 'empty' };
  }

  const actionLines = [];
  const dialogueLines = [];
  let captureDialogue = false;
  const actionMarkers = ['动作：', '动作:', '神态：', '神态:', '场景：', '场景:'];
  const dialogueMarkers = ['对白：', '对白:', '台词：', '台词:', '对话：', '对话:', 'TTS：', 'TTS:'];

  rawText.split(/\n/).forEach((line) => {
    const stripped = line.trim().replace(/^>\s*/, '');
    if (!stripped) {
      return;
    }
    const actionMarker = actionMarkers.find((marker) => stripped.startsWith(marker));
    if (actionMarker) {
      captureDialogue = false;
      const content = stripped.slice(actionMarker.length).trim();
      const inlineDialogueMarker = dialogueMarkers.find((marker) => content.includes(marker));
      if (inlineDialogueMarker) {
        const index = content.indexOf(inlineDialogueMarker);
        actionLines.push(content.slice(0, index).trim());
        dialogueLines.push(content.slice(index + inlineDialogueMarker.length).trim());
        captureDialogue = true;
      } else if (content) {
        actionLines.push(content);
      }
      return;
    }
    const dialogueMarker = dialogueMarkers.find((marker) => stripped.startsWith(marker));
    if (dialogueMarker) {
      captureDialogue = true;
      const content = stripped.slice(dialogueMarker.length).trim();
      if (content) {
        dialogueLines.push(content);
      }
      return;
    }
    if (captureDialogue) {
      dialogueLines.push(stripped);
    } else {
      actionLines.push(stripped);
    }
  });

  if (dialogueLines.length) {
    return {
      action: actionLines.join(' ').trim() || '无',
      dialogue: dialogueLines.join(' ').trim(),
      sourceFormat: 'legacy_text',
    };
  }

  return {
    action: '无',
    dialogue: rawText,
    sourceFormat: 'plain_text',
  };
};

const toLegacyReply = ({ action, dialogue }) => (
  `动作：${String(action || '无').trim() || '无'}\n对白：${String(dialogue || '').trim()}`
);

const parseAssistantRecord = (record) => {
  const content = String(record?.content || record?.text || '').trim();
  const explicitAction = typeof record?.action === 'string' ? record.action.trim() : '';
  const explicitDialogue = typeof record?.dialogue === 'string' ? record.dialogue.trim() : '';
  const explicitSource = record?.sourceFormat || record?.source_format || '';

  if (explicitDialogue) {
    return {
      action: explicitAction || '无',
      dialogue: explicitDialogue,
      sourceFormat: explicitSource || 'json_v2',
    };
  }

  const jsonPayload = parseJsonObjectText(content);
  if (jsonPayload && typeof jsonPayload.dialogue === 'string' && jsonPayload.dialogue.trim()) {
    return {
      action: typeof jsonPayload.action === 'string' && jsonPayload.action.trim() ? jsonPayload.action.trim() : '无',
      dialogue: jsonPayload.dialogue.trim(),
      sourceFormat: explicitSource || 'json_v2',
    };
  }

  return splitLegacyAssistantText(content);
};

const normalizeConversationRecord = (record) => {
  const legacySpeaker = typeof record?.speaker === 'string' ? record.speaker : '';
  const role = normalizeRole(record?.role || legacySpeaker);
  if (!role) {
    return null;
  }

  const timestamp = record?.timestamp || record?.createdAt || record?.created_at || new Date().toISOString();
  const eventMetadata = eventMetadataFromRecord(record);
  if (role === 'user') {
    const content = String(record?.content || record?.text || '').trim();
    if (!content) {
      return null;
    }
    return {
      role: 'user',
      content,
      timestamp,
      schemaVersion: HISTORY_SCHEMA_VERSION,
      ...eventMetadata,
    };
  }

  const parsed = parseAssistantRecord(record);
  if (!parsed.dialogue) {
    return null;
  }

  return {
    role: 'assistant',
    content: parsed.dialogue,
    action: parsed.action || '无',
    dialogue: parsed.dialogue,
    legacyReply: toLegacyReply(parsed),
    timestamp,
    schemaVersion: record?.schemaVersion || record?.schema_version || HISTORY_SCHEMA_VERSION,
    sourceFormat: parsed.sourceFormat || 'legacy_text',
    ...eventMetadata,
  };
};

const normalizeConversationRecords = (records) => {
  if (!Array.isArray(records)) {
    return [];
  }

  return records.map(normalizeConversationRecord).filter(Boolean);
};

const messageToRecord = (message) => ({
  role: message.role === 'assistant' ? 'assistant' : 'user',
  content: message.content || '',
  ...(message.role === 'assistant' ? {
    action: message.action || '无',
    dialogue: message.dialogue || message.content || '',
    legacyReply: message.legacyReply || toLegacyReply({
      action: message.action || '无',
      dialogue: message.dialogue || message.content || '',
    }),
    schemaVersion: message.schemaVersion || HISTORY_SCHEMA_VERSION,
    sourceFormat: message.sourceFormat || 'json_v2',
  } : {
    schemaVersion: HISTORY_SCHEMA_VERSION,
  }),
  createdAt: message.createdAt || new Date().toISOString(),
  ...(message.actor ? { actor: message.actor } : {}),
  ...(message.eventType ? { event_type: message.eventType } : {}),
  ...(message.targetActorIds?.length ? { target_actor_ids: [...message.targetActorIds] } : {}),
  ...(message.eventSchemaVersion ? { event_schema_version: message.eventSchemaVersion } : {}),
});

const readHistoryCache = (userUuid, characterName) => {
  if (!userUuid || !characterName) {
    return { savedAt: '', messages: [] };
  }

  try {
    const rawV2 = localStorage.getItem(historyCacheKey(HISTORY_CACHE_PREFIX_V2, userUuid, characterName));
    if (rawV2) {
      const payload = JSON.parse(rawV2);
      return {
        savedAt: payload?.savedAt || '',
        messages: normalizeConversationRecords(payload?.messages || []),
      };
    }

    const rawV1 = localStorage.getItem(historyCacheKey(HISTORY_CACHE_PREFIX_V1, userUuid, characterName));
    if (rawV1) {
      const payload = JSON.parse(rawV1);
      const messages = normalizeConversationRecords(payload?.messages || []);
      if (messages.length) {
        writeHistoryCache(userUuid, characterName, messages);
      }
      return {
        savedAt: payload?.savedAt || '',
        messages,
      };
    }
    return { savedAt: '', messages: [] };
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
    version: HISTORY_SCHEMA_VERSION,
    schema_version: HISTORY_SCHEMA_VERSION,
    userUuid,
    characterName,
    savedAt: new Date().toISOString(),
    messages: records,
  };
  localStorage.setItem(historyCacheKey(HISTORY_CACHE_PREFIX_V2, userUuid, characterName), JSON.stringify(payload));
};

const removeHistoryCache = (userUuid, characterName) => {
  if (!userUuid || !characterName) {
    return;
  }
  localStorage.removeItem(historyCacheKey(HISTORY_CACHE_PREFIX_V1, userUuid, characterName));
  localStorage.removeItem(historyCacheKey(HISTORY_CACHE_PREFIX_V2, userUuid, characterName));
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
    if (/^##\s+JSON\s*$/i.test(line.trim())) {
      pushCurrent();
      currentRole = '';
      return;
    }

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

const parseJsonConversationPayload = (payload) => {
  if (!payload) {
    return [];
  }
  return normalizeConversationRecords(Array.isArray(payload) ? payload : payload.messages);
};

const parseMarkdownEmbeddedJson = (text) => {
  const rawText = String(text || '');
  const matches = [...rawText.matchAll(/```json\s*([\s\S]*?)```/gi)];
  for (let index = matches.length - 1; index >= 0; index -= 1) {
    try {
      const payload = JSON.parse(matches[index][1]);
      const records = parseJsonConversationPayload(payload);
      if (records.length) {
        return records;
      }
    } catch (_err) {
      // Try earlier JSON blocks or legacy Markdown parsing.
    }
  }
  return [];
};

const parseImportedConversationText = (text) => {
  const rawText = String(text || '').trim();
  if (!rawText) {
    return [];
  }

  try {
    const payload = JSON.parse(rawText);
    const records = parseJsonConversationPayload(payload);
    if (records.length) {
      return records;
    }
  } catch (_err) {
    // Fall through to Markdown parsing.
  }

  const embeddedRecords = parseMarkdownEmbeddedJson(rawText);
  if (embeddedRecords.length) {
    return embeddedRecords;
  }

  return parseMarkdownConversation(rawText);
};

export const useChatStore = defineStore('chat', {
  state: () => ({
    characters: [],
    capabilities: {},
    dialogueEventsEnabled: false,
    contextEventBatchEnabled: false,
    inputMode: 'dialogue',
    userUuid: '',
    selectedCharacter: '',
    characterId: '',
    sessionId: '',
    systemPrompt: '',
    voicePreviewUrl: '',
    outputDir: '',
    restoredHistoryMessages: 0,
    historyCharacters: [],
    messages: [],
    queuedEvents: [],
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
      const [charactersResult, capabilitiesResult] = await Promise.allSettled([
        fetchCharacters(),
        fetchCapabilities(),
      ]);

      if (charactersResult.status === 'fulfilled') {
        this.characters = charactersResult.value.characters || [];
      } else {
        this.error = charactersResult.reason?.message || '获取角色失败。';
      }

      if (capabilitiesResult.status === 'fulfilled') {
        const capabilities = capabilitiesResult.value || {};
        this.capabilities = capabilities;
        this.dialogueEventsEnabled = Number(capabilities.dialogue_events || 0) >= EVENT_SCHEMA_VERSION;
        this.contextEventBatchEnabled = Number(capabilities.context_event_batch || 0) >= 1;
      } else {
        // 前后端可独立部署：旧后端没有能力接口时，前端自动退回旧对话协议。
        this.capabilities = {};
        this.dialogueEventsEnabled = false;
        this.contextEventBatchEnabled = false;
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
        this.characterId = data.character_id || '';
        this.sessionId = data.session_id || '';
        this.systemPrompt = data.system_prompt || '';
        this.voicePreviewUrl = TTS_ENABLED ? resolveAudioUrl(data.voice_preview_url || '') : '';
        this.outputDir = data.output_dir || '';
        this.restoredHistoryMessages = Number(data.restored_history_messages || 0);
        this.exportNotice = '';
        this.queuedEvents = [];
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

    setInputMode(value) {
      if (DIALOGUE_INPUT_MODES[value]) {
        this.inputMode = value;
      }
    },

    queueMessage(text, requestedInputMode = this.inputMode) {
      const content = String(text || '').trim();
      if (!content) {
        this.error = '请输入要加入的内容。';
        return false;
      }
      if (!this.sessionId) {
        this.error = '请先加载角色。';
        return false;
      }
      if (!this.contextEventBatchEnabled) {
        this.error = '当前后端不支持剧情事件队列。';
        return false;
      }
      this.queuedEvents.push(createQueuedEvent(content, requestedInputMode));
      this.error = null;
      this.exportNotice = '';
      return true;
    },

    removeQueuedEvent(eventId) {
      this.queuedEvents = this.queuedEvents.filter((event) => event.id !== eventId);
    },

    clearQueuedEvents() {
      this.queuedEvents = [];
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
        const normalized = normalizeConversationRecord(record);
        if (!normalized) {
          return null;
        }
        const id = [
          'history',
          record.session_id || 'session',
          record.message_index ?? index,
          index,
        ].join('-');
        return createMessage(role, normalized.content || '', {
          id,
          createdAt,
          action: normalized.action,
          dialogue: normalized.dialogue,
          legacyReply: normalized.legacyReply,
          schemaVersion: normalized.schemaVersion,
          sourceFormat: normalized.sourceFormat,
          actor: normalized.actor,
          eventType: normalized.event_type,
          targetActorIds: normalized.target_actor_ids,
          eventSchemaVersion: normalized.event_schema_version,
          renderMode: role === 'assistant' ? 'structured' : 'structured',
          status: 'ready',
        });
      }).filter(Boolean);
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
        this.queuedEvents = [];
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

    _lastUserMessageIndex() {
      for (let index = this.messages.length - 1; index >= 0; index -= 1) {
        if (this.messages[index]?.role === 'user') {
          return index;
        }
      }
      return -1;
    },

    getLastUserMessage() {
      const index = this._lastUserMessageIndex();
      return index >= 0 ? this.messages[index] : null;
    },

    async _replaceCurrentSessionHistory(messages, source = 'regenerate') {
      if (!this.sessionId) {
        throw new Error('请先加载角色。');
      }
      const records = normalizeConversationRecords(messages.map(messageToRecord));
      await importHistory(this.sessionId, records, true, source);
      this.messages = messages;
      this.restoredHistoryMessages = this.messages.length;
      if (this.messages.length) {
        this._cacheCurrentConversation();
      } else {
        this._removeCurrentConversationCache();
      }
    },

    async regenerateFromLastUser(editedText = '', requestedInputMode = '') {
      if (this.isLoading) {
        return false;
      }
      const userIndex = this._lastUserMessageIndex();
      if (userIndex < 0) {
        this.error = '没有可重生成的训练员发言。';
        return false;
      }

      const original = this.messages[userIndex];
      this.queuedEvents = [];
      const text = String(editedText || original.content || '').trim();
      if (!text) {
        this.error = '重生成内容不能为空。';
        return false;
      }

      this.error = null;
      this.exportNotice = '';
      const keptMessages = this.messages.slice(0, userIndex);

      try {
        await this._replaceCurrentSessionHistory(keptMessages, 'regenerate_last_user');
      } catch (err) {
        this.error = err.message || '同步重生成上下文失败。';
        return false;
      }

      await this.sendMessage(text, requestedInputMode || original.inputMode || this.inputMode);
      return !this.error;
    },

    async sendMessage(text, requestedInputMode = this.inputMode) {
      const content = String(text || '').trim();
      const pendingEvents = this.dialogueEventsEnabled
        ? [...this.queuedEvents]
        : [];
      if (!content && !pendingEvents.length) {
        this.error = '请输入内容。';
        return;
      }
      if (!this.sessionId) {
        this.error = '请先加载角色。';
        return;
      }

      let finalText = content;
      let dialogueEvent = null;
      if (this.dialogueEventsEnabled) {
        const outgoingEvents = [...pendingEvents];
        if (content) {
          outgoingEvents.push(createQueuedEvent(content, requestedInputMode));
        }
        const finalEvent = outgoingEvents[outgoingEvents.length - 1];
        const contextEvents = outgoingEvents.slice(0, -1);
        finalText = finalEvent.content;
        dialogueEvent = {
          inputMode: finalEvent.inputMode,
          speaker: finalEvent.speaker,
          event_type: finalEvent.event_type,
          target_actor_ids: finalEvent.target_actor_ids,
          context_events: contextEvents.map(queuedEventToRequest),
        };
        outgoingEvents.forEach((event) => {
          this.messages.push(createMessage('user', event.content, {
            actor: event.speaker,
            eventType: event.event_type,
            targetActorIds: event.target_actor_ids,
            eventSchemaVersion: EVENT_SCHEMA_VERSION,
            inputMode: event.inputMode,
          }));
        });
        this.queuedEvents = [];
      } else {
        this.messages.push(createMessage('user', content));
      }
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
          finalText,
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
            } else if (type === 'structured_reply') {
              const structured = normalizeConversationRecord(data?.message || {
                role: 'assistant',
                content: data?.dialogue || data?.reply || '',
                action: data?.action,
                dialogue: data?.dialogue,
                sourceFormat: data?.message?.source_format || 'json_v2',
              });
              target.content = structured?.content || data?.dialogue || data?.reply || '';
              target.action = structured?.action || data?.action || '无';
              target.dialogue = structured?.dialogue || data?.dialogue || target.content;
              target.legacyReply = data?.reply || structured?.legacyReply || '';
              target.schemaVersion = structured?.schemaVersion || HISTORY_SCHEMA_VERSION;
              target.sourceFormat = structured?.sourceFormat || 'json_v2';
              target.actor = structured?.actor || null;
              target.eventType = structured?.event_type || '';
              target.targetActorIds = structured?.target_actor_ids || [];
              target.eventSchemaVersion = structured?.event_schema_version || undefined;
              target.inputMode = inputModeFromEvent(target.actor, target.eventType);
              target.renderMode = 'structured';
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
          },
          dialogueEvent,
        );
      } else {
        this.isLoading = true;
        try {
          const data = await chatOnce(
            this.sessionId,
            finalText,
            TTS_ENABLED && this.voiceEnabled,
            dialogueEvent,
          );
          if (data.error) {
            this.error = data.error;
          } else {
            const structured = normalizeConversationRecord(data.message || {
              role: 'assistant',
              content: data.dialogue || data.reply || '',
              action: data.action,
              dialogue: data.dialogue,
              sourceFormat: data.message?.source_format || 'json_v2',
            });
            const assistantMessage = createMessage('assistant', structured?.content || data.dialogue || data.reply || '', {
              action: structured?.action || data.action || '无',
              dialogue: structured?.dialogue || data.dialogue || data.reply || '',
              legacyReply: data.reply || structured?.legacyReply || '',
              schemaVersion: structured?.schemaVersion || HISTORY_SCHEMA_VERSION,
              sourceFormat: structured?.sourceFormat || 'json_v2',
              actor: structured?.actor,
              eventType: structured?.event_type,
              targetActorIds: structured?.target_actor_ids,
              eventSchemaVersion: structured?.event_schema_version,
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
      this.queuedEvents = [];
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
      this.queuedEvents = [];

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
        action: record.action,
        dialogue: record.dialogue,
        legacyReply: record.legacyReply,
        schemaVersion: record.schemaVersion,
        sourceFormat: record.sourceFormat,
        actor: record.actor,
        eventType: record.event_type,
        targetActorIds: record.target_actor_ids,
        eventSchemaVersion: record.event_schema_version,
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

    buildConversationJsonPayload() {
      const characterName = this.selectedCharacter || '未选择角色';
      return {
        schema_version: HISTORY_SCHEMA_VERSION,
        ...(this.messages.some((message) => message.eventSchemaVersion) ? {
          event_schema_version: EVENT_SCHEMA_VERSION,
        } : {}),
        app: 'umamusume-agent',
        character: characterName,
        user_uuid: this.userUuid,
        exported_at: new Date().toISOString(),
        messages: normalizeConversationRecords(this.messages.map(messageToRecord)),
      };
    },

    buildConversationJson() {
      return `${JSON.stringify(this.buildConversationJsonPayload(), null, 2)}\n`;
    },

    buildConversationMarkdown() {
      const characterName = this.selectedCharacter || '未选择角色';
      const payload = this.buildConversationJsonPayload();
      const lines = [
        `# ${characterName} 对话记录`,
        '',
        `- schema_version: ${HISTORY_SCHEMA_VERSION}`,
        `- exported_at: ${payload.exported_at}`,
        `- 角色：${characterName}`,
        `- 消息数：${this.messages.length}`,
        '',
        '## 对话',
        '',
      ];

      this.messages.forEach((message, index) => {
        const speaker = message.actor?.display_name
          || (message.role === 'assistant' ? characterName : '训练员');
        const eventLabel = message.eventType
          ? ` · ${DIALOGUE_INPUT_MODES[message.inputMode]?.shortLabel || message.eventType}`
          : '';
        const status = message.status === 'streaming' ? '（生成中）' : '';
        lines.push(`### ${index + 1}. ${speaker}${eventLabel}${status}`);
        lines.push('');
        if (message.role === 'assistant') {
          const action = String(message.action || '').trim();
          const dialogue = String(message.dialogue || message.content || '').trim();
          if (action && action !== '无') {
            lines.push(`> 动作：${action}`);
            lines.push('');
          }
          lines.push(normalizeMarkdownText(dialogue));
        } else {
          lines.push(normalizeMarkdownText(message.content));
        }
        lines.push('');
      });

      lines.push('## JSON');
      lines.push('');
      lines.push('```json');
      lines.push(JSON.stringify(payload, null, 2));
      lines.push('```');
      lines.push('');

      return lines.join('\n').replace(/\n{3,}/g, '\n\n').trimEnd() + '\n';
    },

    async copyConversationJson() {
      this.error = null;
      this.exportNotice = '';
      if (!this.messages.length) {
        this.exportNotice = '当前没有可复制的对话。';
        return false;
      }

      try {
        await writeTextToClipboard(this.buildConversationJson());
        this.exportNotice = '已复制 JSON 到剪贴板。';
        return true;
      } catch (err) {
        this.error = err.message || '复制 JSON 失败。';
        return false;
      }
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

    downloadConversationJson() {
      this.error = null;
      this.exportNotice = '';
      if (!this.messages.length) {
        this.exportNotice = '当前没有可下载的对话。';
        return false;
      }

      try {
        const filenameBase = sanitizeFilenamePart(this.selectedCharacter || 'umamusume-dialogue');
        const filename = `${filenameBase}-${markdownTimestamp()}.json`;
        downloadTextFile(filename, this.buildConversationJson(), 'application/json;charset=utf-8');
        this.exportNotice = `已下载 ${filename}。`;
        return true;
      } catch (err) {
        this.error = err.message || '下载 JSON 失败。';
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
        downloadTextFile(filename, this.buildConversationMarkdown(), 'text/markdown;charset=utf-8');
        this.exportNotice = `已下载 ${filename}。`;
        return true;
      } catch (err) {
        this.error = err.message || '下载 Markdown 失败。';
        return false;
      }
    },
  },
});
