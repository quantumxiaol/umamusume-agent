import { defineStore } from 'pinia';

import {
  API_BASE_URL,
  createDirectorSession,
  deleteDirectorHistory,
  deleteDirectorSession,
  directorTurnStream,
  fetchDirectorHistory,
  fetchDirectorSession,
  fetchDirectorTemplates,
  recoverDirectorSession,
  resumeDirectorHistory,
  fetchTtsJob,
} from '@/services/api';
import { DIALOGUE_INPUT_MODES } from '@/stores/chatStore';


const DIRECTOR_ACTIVE_SESSION_PREFIX = 'umamusume_director_active_v1';
const DIRECTOR_SCENE_CACHE_PREFIX = 'umamusume_director_scene_v1';
const DIRECTOR_HISTORY_INDEX_PREFIX = 'umamusume_director_history_index_v1';
const DIRECTOR_DELETED_INDEX_PREFIX = 'umamusume_director_deleted_v1';
const DIRECTOR_LOCAL_HISTORY_LIMIT = 30;
const TERMINAL_TTS_STATES = new Set(['ready', 'failed', 'cancelled', 'expired']);


const resolveAudioUrl = (url) => {
  if (!url) {
    return '';
  }
  if (url.startsWith('http://') || url.startsWith('https://')) {
    return url;
  }
  return url.startsWith('/') ? `${API_BASE_URL}${url}` : url;
};


const normalizeVoice = (value) => {
  const jobId = String(value?.job_id || value?.jobId || '').trim();
  if (!jobId) {
    return null;
  }
  return {
    requested: true,
    job_id: jobId,
    status: String(value?.state || value?.status || 'queued'),
    audio_url: resolveAudioUrl(value?.audio_url || ''),
    error: String(value?.error || ''),
  };
};


const eventForBrowserCache = (event) => ({
  ...event,
  ...(event.voice?.job_id ? {
    voice: {
      requested: true,
      job_id: event.voice.job_id,
      status: event.voice.status || 'queued',
    },
  } : { voice: undefined }),
});


const activeSessionKey = (userUuid) => (
  `${DIRECTOR_ACTIVE_SESSION_PREFIX}:${encodeURIComponent(userUuid || '')}`
);


const sceneCacheKey = (userUuid, sessionId) => (
  `${DIRECTOR_SCENE_CACHE_PREFIX}:${encodeURIComponent(userUuid || '')}:${encodeURIComponent(sessionId || '')}`
);


const historyIndexKey = (userUuid) => (
  `${DIRECTOR_HISTORY_INDEX_PREFIX}:${encodeURIComponent(userUuid || '')}`
);


const deletedIndexKey = (userUuid) => (
  `${DIRECTOR_DELETED_INDEX_PREFIX}:${encodeURIComponent(userUuid || '')}`
);


const readStringArray = (key) => {
  if (typeof localStorage === 'undefined') {
    return [];
  }
  try {
    const value = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(value)
      ? value.map((item) => String(item || '')).filter(Boolean)
      : [];
  } catch (_err) {
    return [];
  }
};


const writeStringArray = (key, values) => {
  if (typeof localStorage === 'undefined') {
    return false;
  }
  try {
    localStorage.setItem(key, JSON.stringify([...new Set(values)]));
    return true;
  } catch (_err) {
    return false;
  }
};


const readSceneSnapshot = (userUuid, sessionId) => {
  if (!userUuid || !sessionId || typeof localStorage === 'undefined') {
    return null;
  }
  try {
    const snapshot = JSON.parse(
      localStorage.getItem(sceneCacheKey(userUuid, sessionId)) || 'null',
    );
    if (
      !snapshot
      || snapshot.schema_version !== 1
      || snapshot.user_uuid !== userUuid
      || snapshot.session_id !== sessionId
    ) {
      return null;
    }
    return snapshot;
  } catch (_err) {
    return null;
  }
};


const writeSceneSnapshot = (userUuid, snapshot) => {
  const sessionId = String(snapshot?.session_id || '');
  if (!userUuid || !sessionId || typeof localStorage === 'undefined') {
    return false;
  }
  const indexKey = historyIndexKey(userUuid);
  let sessionIds = [
    sessionId,
    ...readStringArray(indexKey).filter((item) => item !== sessionId),
  ];
  const evicted = sessionIds.slice(DIRECTOR_LOCAL_HISTORY_LIMIT);
  sessionIds = sessionIds.slice(0, DIRECTOR_LOCAL_HISTORY_LIMIT);
  try {
    localStorage.setItem(
      sceneCacheKey(userUuid, sessionId),
      JSON.stringify(snapshot),
    );
    localStorage.setItem(indexKey, JSON.stringify(sessionIds));
    evicted.forEach((item) => {
      localStorage.removeItem(sceneCacheKey(userUuid, item));
    });
    return true;
  } catch (_err) {
    const oldest = sessionIds[sessionIds.length - 1];
    if (oldest && oldest !== sessionId) {
      localStorage.removeItem(sceneCacheKey(userUuid, oldest));
      try {
        const reduced = sessionIds.filter((item) => item !== oldest);
        localStorage.setItem(
          sceneCacheKey(userUuid, sessionId),
          JSON.stringify(snapshot),
        );
        localStorage.setItem(indexKey, JSON.stringify(reduced));
        return true;
      } catch (_retryError) {
        return false;
      }
    }
    return false;
  }
};


const removeSceneSnapshot = (userUuid, sessionId) => {
  if (!userUuid || !sessionId || typeof localStorage === 'undefined') {
    return;
  }
  localStorage.removeItem(sceneCacheKey(userUuid, sessionId));
  writeStringArray(
    historyIndexKey(userUuid),
    readStringArray(historyIndexKey(userUuid)).filter((item) => item !== sessionId),
  );
};


const markSceneDeleted = (userUuid, sessionId) => {
  writeStringArray(
    deletedIndexKey(userUuid),
    [
      sessionId,
      ...readStringArray(deletedIndexKey(userUuid))
        .filter((item) => item !== sessionId),
    ].slice(0, 100),
  );
};


const localSceneSnapshots = (userUuid) => (
  readStringArray(historyIndexKey(userUuid))
    .map((sessionId) => readSceneSnapshot(userUuid, sessionId))
    .filter(Boolean)
);


const snapshotSummary = (snapshot) => {
  const events = Array.isArray(snapshot?.events) ? snapshot.events : [];
  const latest = events[events.length - 1] || {};
  return {
    session_id: snapshot.session_id,
    template_id: snapshot.template?.template_id || '',
    scene_name: snapshot.template?.name || '导演场景',
    location: snapshot.scene_state?.location || '',
    time: snapshot.scene_state?.time || '',
    character_names: (snapshot.participants || [])
      .filter((item) => ['umamusume', 'npc'].includes(item?.actor?.actor_type))
      .map((item) => item.actor.display_name),
    turn_index: Number(snapshot.turn_index || 0),
    event_count: events.length,
    preview: String(latest.dialogue || latest.content || latest.action || '').slice(0, 160),
    created_at: snapshot.created_at,
    updated_at: snapshot.last_active_at || snapshot.created_at,
    is_custom: String(snapshot.template?.template_id || '').startsWith('custom_'),
    source: 'browser',
  };
};


const readActiveSession = (userUuid) => {
  if (!userUuid || typeof localStorage === 'undefined') {
    return '';
  }
  try {
    const payload = JSON.parse(localStorage.getItem(activeSessionKey(userUuid)) || '{}');
    return String(payload?.sessionId || '');
  } catch (_err) {
    return '';
  }
};


const writeActiveSession = (userUuid, sessionId) => {
  if (!userUuid || !sessionId || typeof localStorage === 'undefined') {
    return;
  }
  localStorage.setItem(activeSessionKey(userUuid), JSON.stringify({
    version: 1,
    sessionId,
    savedAt: new Date().toISOString(),
  }));
};


const clearActiveSession = (userUuid) => {
  if (!userUuid || typeof localStorage === 'undefined') {
    return;
  }
  localStorage.removeItem(activeSessionKey(userUuid));
};


const defaultCustomScene = () => ({
  name: '自定义场景',
  location: '',
  subLocation: '',
  time: '',
  weather: '',
  lighting: '',
  atmosphere: '',
  ambientSound: '',
  props: '',
  openingNarration: '',
});


const queuedEvent = (content, inputMode) => {
  const mode = DIALOGUE_INPUT_MODES[inputMode]
    ? inputMode
    : 'dialogue';
  const preset = DIALOGUE_INPUT_MODES[mode];
  return {
    id: `director-queued-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    content: String(content || '').trim(),
    inputMode: mode,
    speaker: { ...preset.speaker },
    event_type: preset.eventType,
  };
};


export const useDirectorStore = defineStore('director', {
  state: () => ({
    templates: [],
    sceneSource: 'preset',
    selectedTemplateId: '',
    customScene: defaultCustomScene(),
    storyOutline: '',
    selectedCharacterNames: [],
    sessionId: '',
    activeTemplate: null,
    participants: [],
    sceneState: {},
    events: [],
    turnIndex: 0,
    createdAt: '',
    lastActiveAt: '',
    inputMode: 'dialogue',
    queuedEvents: [],
    historyScenes: [],
    currentUserUuid: '',
    isRestoring: false,
    isHistoryLoading: false,
    isLoading: false,
    error: null,
    historyError: null,
    voicePollers: {},
  }),

  actions: {
    async init(userUuid = '') {
      if (!this.templates.length) {
        try {
          const data = await fetchDirectorTemplates();
          this.templates = data.templates || [];
          if (!this.selectedTemplateId && this.templates.length) {
            this.selectedTemplateId = this.templates[0].template_id;
          }
        } catch (err) {
          this.error = err.message || '读取场景预设失败。';
        }
      }

      const normalizedUserUuid = String(userUuid || '').trim();
      if (!normalizedUserUuid) {
        return;
      }
      this.currentUserUuid = normalizedUserUuid;
      if (!this.sessionId) {
        const activeSessionId = readActiveSession(normalizedUserUuid);
        if (activeSessionId) {
          await this.restoreActiveSession(activeSessionId, normalizedUserUuid);
        }
      }
      await this.refreshHistory(normalizedUserUuid);
    },

    _applySnapshot(data, userUuid = '') {
      this._clearVoicePollers();
      const resolvedUserUuid = data.user_uuid || userUuid || this.currentUserUuid;
      const localSnapshot = readSceneSnapshot(
        resolvedUserUuid,
        data.session_id || '',
      );
      const localVoiceByEvent = new Map(
        (localSnapshot?.events || [])
          .filter((event) => event.event_id && event.voice?.job_id)
          .map((event) => [event.event_id, event.voice]),
      );
      this.sessionId = data.session_id || '';
      this.activeTemplate = data.template || null;
      this.participants = data.participants || [];
      this.sceneState = data.scene_state || {};
      this.events = (data.events || []).map((event) => ({
        ...event,
        voice: normalizeVoice(
          event.voice || localVoiceByEvent.get(event.event_id),
        ),
      }));
      this.turnIndex = data.turn_index || 0;
      this.createdAt = data.created_at || new Date().toISOString();
      this.lastActiveAt = data.last_active_at || this.createdAt;
      this.storyOutline = data.story_outline || '';
      this.selectedCharacterNames = this.participants
        .filter((item) => ['umamusume', 'npc'].includes(item?.actor?.actor_type))
        .map((item) => item.actor.display_name);
      this.queuedEvents = [];
      if (resolvedUserUuid) {
        this.currentUserUuid = resolvedUserUuid;
      }
      if (this.sessionId && this.currentUserUuid) {
        writeActiveSession(this.currentUserUuid, this.sessionId);
        this._persistCurrentScene();
        this.events.forEach((event) => {
          if (
            event.voice?.job_id
            && (
              !event.voice.audio_url
              || !TERMINAL_TTS_STATES.has(event.voice.status)
            )
          ) {
            this._startVoicePolling(event);
          }
        });
      }
    },

    _currentSnapshot() {
      if (!this.sessionId || !this.currentUserUuid || !this.activeTemplate) {
        return null;
      }
      return {
        schema_version: 1,
        session_id: this.sessionId,
        user_uuid: this.currentUserUuid,
        template: this.activeTemplate,
        story_outline: this.storyOutline || '',
        player: this.participants.find(
          (item) => item?.actor?.actor_id === 'player',
        )?.actor || {
          actor_id: 'player',
          actor_type: 'trainer',
          display_name: '训练员',
          role_in_scene: 'trainer',
        },
        participants: this.participants,
        scene_state: this.sceneState,
        turn_index: this.turnIndex,
        events: this.events.map(eventForBrowserCache),
        created_at: this.createdAt || new Date().toISOString(),
        last_active_at: this.lastActiveAt || new Date().toISOString(),
      };
    },

    _persistCurrentScene() {
      const snapshot = this._currentSnapshot();
      if (!snapshot) {
        return false;
      }
      const saved = writeSceneSnapshot(this.currentUserUuid, snapshot);
      if (!saved) {
        this.historyError = '浏览器存储空间不足，当前场景可能无法在服务重启后恢复。';
      }
      return saved;
    },

    _clearCurrentScene() {
      this._clearVoicePollers();
      this.sessionId = '';
      this.activeTemplate = null;
      this.participants = [];
      this.sceneState = {};
      this.events = [];
      this.turnIndex = 0;
      this.createdAt = '';
      this.lastActiveAt = '';
      this.queuedEvents = [];
    },

    _clearVoicePollers() {
      Object.values(this.voicePollers).forEach(
        (timerId) => clearInterval(timerId),
      );
      this.voicePollers = {};
    },

    _startVoicePolling(event) {
      if (!event?.voice?.job_id || !this.currentUserUuid) {
        return;
      }
      const eventId = event.event_id;
      const jobId = event.voice.job_id;
      if (this.voicePollers[eventId]) {
        return;
      }
      let attempts = 0;
      let inFlight = false;
      // Keep polling long enough for queued, sequential Fish Speech jobs.
      const maxAttempts = 1800;
      const poll = async () => {
        if (inFlight) {
          return;
        }
        const currentEvent = this.events.find(
          (item) => item.event_id === eventId,
        );
        if (
          !currentEvent?.voice
          || currentEvent.voice.job_id !== jobId
        ) {
          clearInterval(timerId);
          delete this.voicePollers[eventId];
          return;
        }
        inFlight = true;
        attempts += 1;
        try {
          const job = await fetchTtsJob(
            jobId,
            this.currentUserUuid,
          );
          const target = this.events.find(
            (item) => item.event_id === eventId,
          );
          if (!target?.voice || target.voice.job_id !== jobId) {
            clearInterval(timerId);
            delete this.voicePollers[eventId];
            return;
          }
          // Always update the object held by Pinia. The event passed into this
          // method can be the raw object that existed before Array.push(), and
          // mutating that raw reference does not notify Vue's reactive proxy.
          target.voice = {
            ...target.voice,
            status: job.state || 'queued',
            audio_url: resolveAudioUrl(job.audio_url || ''),
            error: job.error || '',
          };
        } catch (err) {
          const target = this.events.find(
            (item) => item.event_id === eventId,
          );
          if (!target?.voice || target.voice.job_id !== jobId) {
            clearInterval(timerId);
            delete this.voicePollers[eventId];
            return;
          }
          if (err?.status === 404) {
            target.voice = {
              ...target.voice,
              status: 'expired',
              audio_url: '',
            };
          } else {
            target.voice = {
              ...target.voice,
              error: '配音状态查询暂时失败，正在重试。',
            };
          }
        } finally {
          inFlight = false;
        }
        const target = this.events.find(
          (item) => item.event_id === eventId,
        );
        if (!target?.voice || target.voice.job_id !== jobId) {
          clearInterval(timerId);
          delete this.voicePollers[eventId];
          return;
        }
        if (
          TERMINAL_TTS_STATES.has(target.voice.status)
          || attempts >= maxAttempts
        ) {
          clearInterval(timerId);
          delete this.voicePollers[eventId];
          if (
            attempts >= maxAttempts
            && !TERMINAL_TTS_STATES.has(target.voice.status)
          ) {
            target.voice = {
              ...target.voice,
              status: 'expired',
            };
          }
          this._persistCurrentScene();
        }
      };
      const timerId = setInterval(poll, 2000);
      this.voicePollers[eventId] = timerId;
      poll();
    },

    async _loadSessionWithFallback(sessionId, userUuid) {
      const localSnapshot = readSceneSnapshot(userUuid, sessionId);
      try {
        return await fetchDirectorSession(sessionId, userUuid);
      } catch (_liveError) {
        try {
          return await resumeDirectorHistory(sessionId, userUuid);
        } catch (historyError) {
          if (!localSnapshot) {
            throw historyError;
          }
          return recoverDirectorSession(localSnapshot, userUuid);
        }
      }
    },

    async restoreActiveSession(sessionId, userUuid) {
      this.isRestoring = true;
      this.historyError = null;
      try {
        const data = await this._loadSessionWithFallback(sessionId, userUuid);
        this._applySnapshot(data, userUuid);
        return true;
      } catch (err) {
        clearActiveSession(userUuid);
        this.historyError = err.message || '上次场景已经无法恢复。';
        return false;
      } finally {
        this.isRestoring = false;
      }
    },

    async refreshHistory(userUuid = '') {
      const resolvedUserUuid = userUuid || this.currentUserUuid;
      if (!resolvedUserUuid) {
        return;
      }
      this.currentUserUuid = resolvedUserUuid;
      this.isHistoryLoading = true;
      this.historyError = null;
      const deletedIds = new Set(
        readStringArray(deletedIndexKey(resolvedUserUuid)),
      );
      const localScenes = localSceneSnapshots(resolvedUserUuid)
        .filter((snapshot) => !deletedIds.has(snapshot.session_id))
        .map(snapshotSummary);
      this.historyScenes = localScenes;
      try {
        const data = await fetchDirectorHistory(resolvedUserUuid);
        const merged = new Map(
          (data.scenes || [])
            .filter((scene) => !deletedIds.has(scene.session_id))
            .map((scene) => [scene.session_id, scene]),
        );
        localScenes.forEach((scene) => merged.set(scene.session_id, scene));
        this.historyScenes = [...merged.values()].sort(
          (left, right) => (
            Date.parse(right.updated_at || 0) - Date.parse(left.updated_at || 0)
          ),
        );
      } catch (err) {
        if (!localScenes.length) {
          this.historyError = err.message || '读取场景历史失败。';
        }
      } finally {
        this.isHistoryLoading = false;
      }
    },

    async resumeScene(sessionId, userUuid = '') {
      const resolvedUserUuid = userUuid || this.currentUserUuid;
      if (!resolvedUserUuid || !sessionId || this.isRestoring) {
        return false;
      }
      this.isRestoring = true;
      this.error = null;
      this.historyError = null;
      try {
        const data = await this._loadSessionWithFallback(
          sessionId,
          resolvedUserUuid,
        );
        this._applySnapshot(data, resolvedUserUuid);
        return true;
      } catch (err) {
        this.historyError = err.message || '恢复场景失败。';
        return false;
      } finally {
        this.isRestoring = false;
      }
    },

    async deleteHistoryScene(sessionId, userUuid = '') {
      const resolvedUserUuid = userUuid || this.currentUserUuid;
      if (!resolvedUserUuid || !sessionId) {
        return false;
      }
      this.isHistoryLoading = true;
      this.historyError = null;
      removeSceneSnapshot(resolvedUserUuid, sessionId);
      markSceneDeleted(resolvedUserUuid, sessionId);
      this.historyScenes = this.historyScenes.filter(
        (item) => item.session_id !== sessionId,
      );
      if (this.sessionId === sessionId) {
        this._clearCurrentScene();
        clearActiveSession(resolvedUserUuid);
      }
      try {
        await deleteDirectorHistory(sessionId, resolvedUserUuid);
        return true;
      } catch (err) {
        this.historyError = '浏览器历史已删除；后端当前不可用，残留记录将在服务端可用时忽略。';
        return true;
      } finally {
        this.isHistoryLoading = false;
      }
    },

    setTemplate(templateId) {
      if (!this.sessionId) {
        this.selectedTemplateId = templateId;
        this.sceneSource = 'preset';
      }
    },

    setSceneSource(source) {
      if (!this.sessionId && ['preset', 'custom'].includes(source)) {
        this.sceneSource = source;
        this.error = null;
      }
    },

    toggleCharacter(name, maxParticipants = 3) {
      if (this.sessionId) {
        return;
      }
      if (this.selectedCharacterNames.includes(name)) {
        this.selectedCharacterNames = this.selectedCharacterNames.filter(
          (item) => item !== name,
        );
        return;
      }
      if (this.selectedCharacterNames.length >= maxParticipants) {
        this.error = `最多选择 ${maxParticipants} 个角色。`;
        return;
      }
      this.selectedCharacterNames.push(name);
      this.error = null;
    },

    setInputMode(mode) {
      if (DIALOGUE_INPUT_MODES[mode]) {
        this.inputMode = mode;
      }
    },

    queueEvent(content) {
      const text = String(content || '').trim();
      if (!text) {
        this.error = '请输入要加入的事件。';
        return false;
      }
      this.queuedEvents.push(queuedEvent(text, this.inputMode));
      this.error = null;
      return true;
    },

    removeQueuedEvent(eventId) {
      this.queuedEvents = this.queuedEvents.filter((item) => item.id !== eventId);
    },

    clearQueuedEvents() {
      this.queuedEvents = [];
    },

    async createScene(userUuid) {
      if (this.sceneSource === 'preset' && !this.selectedTemplateId) {
        this.error = '请选择场景预设。';
        return false;
      }
      if (this.sceneSource === 'custom' && !this.customScene.location.trim()) {
        this.error = '自定义场景必须填写地点。';
        return false;
      }
      if (!this.selectedCharacterNames.length) {
        this.error = '请至少选择一个角色。';
        return false;
      }
      this.isLoading = true;
      this.error = null;
      this.currentUserUuid = userUuid || this.currentUserUuid;
      try {
        const customScene = this.sceneSource === 'custom'
          ? {
            name: this.customScene.name.trim() || '自定义场景',
            initial_state: {
              location: this.customScene.location.trim(),
              sub_location: this.customScene.subLocation.trim() || null,
              time: this.customScene.time.trim(),
              weather: this.customScene.weather.trim(),
              lighting: this.customScene.lighting.trim(),
              atmosphere: this.customScene.atmosphere.trim(),
              ambient_sound: this.customScene.ambientSound.trim(),
              props: this.customScene.props
                .split(/[，,、]/)
                .map((item) => item.trim())
                .filter(Boolean),
            },
            opening_narration: this.customScene.openingNarration.trim(),
            tags: ['自定义'],
          }
          : null;
        const data = await createDirectorSession(
          this.sceneSource === 'preset' ? this.selectedTemplateId : '',
          this.selectedCharacterNames,
          userUuid,
          customScene,
          this.storyOutline.trim(),
        );
        this._applySnapshot(data, userUuid);
        await this.refreshHistory(userUuid);
        return Boolean(this.sessionId);
      } catch (err) {
        this.error = err.message || '创建导演场景失败。';
        return false;
      } finally {
        this.isLoading = false;
      }
    },

    async resetScene() {
      const sessionId = this.sessionId;
      const userUuid = this.currentUserUuid;
      this._clearCurrentScene();
      clearActiveSession(userUuid);
      this.error = null;
      if (sessionId) {
        try {
          await deleteDirectorSession(sessionId, userUuid);
        } catch (_err) {
          // Local reset is still complete; server TTL will remove stale state.
        }
      }
      await this.refreshHistory(userUuid);
    },

    _appendEvent(event) {
      if (!event?.event_id) {
        return;
      }
      if (this.events.some((item) => item.event_id === event.event_id)) {
        return;
      }
      const normalizedEvent = {
        ...event,
        voice: normalizeVoice(event.voice),
      };
      this.events.push(normalizedEvent);
      if (normalizedEvent.voice?.job_id) {
        this._startVoicePolling(normalizedEvent);
      }
      this.turnIndex = Math.max(this.turnIndex, Number(event.turn_index || 0));
      if (event.scene_patch && typeof event.scene_patch === 'object') {
        const patch = Object.fromEntries(
          Object.entries(event.scene_patch)
            .filter(([, value]) => value !== null && value !== undefined),
        );
        this.sceneState = { ...this.sceneState, ...patch };
      }
      this.lastActiveAt = new Date().toISOString();
      this._persistCurrentScene();
    },

    async sendTurn(content = '', generateVoice = false) {
      if (!this.sessionId || this.isLoading) {
        return false;
      }
      const text = String(content || '').trim();
      const outgoing = [...this.queuedEvents];
      if (text) {
        outgoing.push(queuedEvent(text, this.inputMode));
      }
      if (!outgoing.length) {
        this.error = '请输入内容或先加入事件。';
        return false;
      }

      this.queuedEvents = [];
      this.isLoading = true;
      this.error = null;
      try {
        await directorTurnStream(
          this.sessionId,
          outgoing.map((event) => ({
            content: event.content,
            speaker: event.speaker,
            event_type: event.event_type,
          })),
          this.currentUserUuid,
          generateVoice,
          ({ type, data }) => {
            if (type === 'scene_event' || type === 'character_reply') {
              this._appendEvent(data);
            } else if (type === 'scene_state') {
              this.sceneState = data || {};
              this.lastActiveAt = new Date().toISOString();
              this._persistCurrentScene();
            } else if (type === 'error') {
              this.error = data?.detail || '导演模式执行失败。';
            }
          },
        );
        this.lastActiveAt = new Date().toISOString();
        this._persistCurrentScene();
        return !this.error;
      } catch (err) {
        this.error = this.error || err.message || '导演模式执行失败。';
        return false;
      } finally {
        this.isLoading = false;
      }
    },
  },
});
