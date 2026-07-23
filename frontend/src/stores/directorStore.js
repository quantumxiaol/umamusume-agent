import { defineStore } from 'pinia';

import {
  createDirectorSession,
  deleteDirectorHistory,
  deleteDirectorSession,
  directorTurnStream,
  fetchDirectorHistory,
  fetchDirectorSession,
  fetchDirectorTemplates,
  resumeDirectorHistory,
} from '@/services/api';
import { DIALOGUE_INPUT_MODES } from '@/stores/chatStore';


const DIRECTOR_ACTIVE_SESSION_PREFIX = 'umamusume_director_active_v1';


const activeSessionKey = (userUuid) => (
  `${DIRECTOR_ACTIVE_SESSION_PREFIX}:${encodeURIComponent(userUuid || '')}`
);


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
    inputMode: 'dialogue',
    queuedEvents: [],
    historyScenes: [],
    currentUserUuid: '',
    isRestoring: false,
    isHistoryLoading: false,
    isLoading: false,
    error: null,
    historyError: null,
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
      this.sessionId = data.session_id || '';
      this.activeTemplate = data.template || null;
      this.participants = data.participants || [];
      this.sceneState = data.scene_state || {};
      this.events = data.events || [];
      this.turnIndex = data.turn_index || 0;
      this.storyOutline = data.story_outline || '';
      this.selectedCharacterNames = this.participants
        .filter((item) => ['umamusume', 'npc'].includes(item?.actor?.actor_type))
        .map((item) => item.actor.display_name);
      this.queuedEvents = [];
      const resolvedUserUuid = data.user_uuid || userUuid || this.currentUserUuid;
      if (resolvedUserUuid) {
        this.currentUserUuid = resolvedUserUuid;
      }
      if (this.sessionId && this.currentUserUuid) {
        writeActiveSession(this.currentUserUuid, this.sessionId);
      }
    },

    _clearCurrentScene() {
      this.sessionId = '';
      this.activeTemplate = null;
      this.participants = [];
      this.sceneState = {};
      this.events = [];
      this.turnIndex = 0;
      this.queuedEvents = [];
    },

    async restoreActiveSession(sessionId, userUuid) {
      this.isRestoring = true;
      this.historyError = null;
      try {
        let data;
        try {
          data = await fetchDirectorSession(sessionId);
        } catch (_liveError) {
          data = await resumeDirectorHistory(sessionId, userUuid);
        }
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
      try {
        const data = await fetchDirectorHistory(resolvedUserUuid);
        this.historyScenes = data.scenes || [];
      } catch (err) {
        this.historyError = err.message || '读取场景历史失败。';
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
        const data = await resumeDirectorHistory(sessionId, resolvedUserUuid);
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
      try {
        await deleteDirectorHistory(sessionId, resolvedUserUuid);
        this.historyScenes = this.historyScenes.filter(
          (item) => item.session_id !== sessionId,
        );
        if (this.sessionId === sessionId) {
          this._clearCurrentScene();
          clearActiveSession(resolvedUserUuid);
        }
        return true;
      } catch (err) {
        this.historyError = err.message || '删除场景历史失败。';
        return false;
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
          await deleteDirectorSession(sessionId);
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
      this.events.push(event);
      this.turnIndex = Math.max(this.turnIndex, Number(event.turn_index || 0));
    },

    async sendTurn(content = '') {
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
          ({ type, data }) => {
            if (type === 'scene_event' || type === 'character_reply') {
              this._appendEvent(data);
            } else if (type === 'scene_state') {
              this.sceneState = data || {};
            } else if (type === 'error') {
              this.error = data?.detail || '导演模式执行失败。';
            }
          },
        );
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
