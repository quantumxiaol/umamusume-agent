// frontend/src/services/api.js
import axios from 'axios';

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:1111';
export const API_ACCESS_KEY = import.meta.env.VITE_API_ACCESS_KEY || '';

const buildAuthHeaders = (headers = {}) => {
  if (!API_ACCESS_KEY) {
    return headers;
  }
  return {
    ...headers,
    'X-API-Key': API_ACCESS_KEY,
  };
};

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 600000,
  headers: buildAuthHeaders({
    'Content-Type': 'application/json',
  }),
});

const parseError = (error) => {
  if (error.response) {
    return new Error(`Server Error: ${error.response.status} - ${error.response.data?.detail || 'Unknown error'}`);
  }
  if (error.request) {
    return new Error('Network Error: No response received from server.');
  }
  return new Error(`Request Error: ${error.message}`);
};

export const fetchCharacters = async () => {
  try {
    const response = await apiClient.get('/characters');
    return response.data || {};
  } catch (error) {
    throw parseError(error);
  }
};

export const loadCharacter = async (characterName, forceRebuild = false, userUuid = '') => {
  try {
    const response = await apiClient.post('/load_character', {
      character_name: characterName,
      force_rebuild: forceRebuild,
      user_uuid: userUuid || undefined,
    });
    return response.data || {};
  } catch (error) {
    throw parseError(error);
  }
};

export const fetchCapabilities = async () => {
  try {
    const response = await apiClient.get('/capabilities');
    return response.data || {};
  } catch (error) {
    throw parseError(error);
  }
};

const buildDialogueEventFields = (dialogueEvent) => {
  if (!dialogueEvent) {
    return {};
  }
  return {
    speaker: dialogueEvent.speaker || undefined,
    event_type: dialogueEvent.event_type || undefined,
    target_actor_ids: dialogueEvent.target_actor_ids || undefined,
    context_events: dialogueEvent.context_events?.length
      ? dialogueEvent.context_events
      : undefined,
  };
};

export const chatOnce = async (sessionId, message, generateVoice = false, dialogueEvent = null) => {
  try {
    const response = await apiClient.post('/chat', {
      session_id: sessionId,
      message,
      generate_voice: generateVoice,
      ...buildDialogueEventFields(dialogueEvent),
    });
    return response.data || {};
  } catch (error) {
    throw parseError(error);
  }
};

const emitEvent = (onEvent, type, data) => {
  if (!onEvent) {
    return;
  }
  onEvent({ type, data });
};

export const chatStream = async (
  sessionId,
  message,
  generateVoice = false,
  onEvent,
  dialogueEvent = null,
) => {
  try {
    const url = `${API_BASE_URL}/chat_stream`;
    const response = await fetch(url, {
      method: 'POST',
      headers: buildAuthHeaders({
        'Content-Type': 'application/json',
      }),
      body: JSON.stringify({
        session_id: sessionId,
        message,
        generate_voice: generateVoice,
        ...buildDialogueEventFields(dialogueEvent),
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Server Error: ${response.status} - ${errorText}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let eventName = null;
    let dataLines = [];
    let doneSeen = false;

    const flushEvent = () => {
      if (!dataLines.length) {
        eventName = null;
        return;
      }
      const data = dataLines.join('\n');
      const type = eventName || 'token';
      if (type === 'voice_pending' || type === 'structured_reply') {
        try {
          emitEvent(onEvent, type, JSON.parse(data));
        } catch (err) {
          emitEvent(onEvent, 'error', `${type} decode failed: ${data}`);
        }
      } else {
        emitEvent(onEvent, type, data);
      }
      if (type === 'done') {
        doneSeen = true;
      }
      eventName = null;
      dataLines = [];
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const rawLine of lines) {
        const line = rawLine.replace(/\r$/, '');
        if (!line) {
          flushEvent();
          continue;
        }
        if (line.startsWith(':')) {
          continue;
        }
        if (line.startsWith('event:')) {
          eventName = line.slice(6).trim();
          continue;
        }
        if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trimStart());
        }
      }
    }

    if (buffer.trim()) {
      dataLines.push(buffer.trim());
      flushEvent();
    }

    if (!doneSeen) {
      emitEvent(onEvent, 'done', '');
    }
  } catch (error) {
    emitEvent(onEvent, 'error', error.message || 'Stream error occurred');
    throw error;
  }
};

export const fetchHistory = async (userUuid, characterName = '', limit = 200) => {
  try {
    const response = await apiClient.get('/history', {
      params: {
        user_uuid: userUuid,
        character_name: characterName || undefined,
        limit,
      },
    });
    return response.data || {};
  } catch (error) {
    throw parseError(error);
  }
};

export const importHistory = async (sessionId, messages, replaceCurrent = true, source = 'manual') => {
  try {
    const response = await apiClient.post('/history/import', {
      session_id: sessionId,
      messages,
      replace_current: replaceCurrent,
      source,
    });
    return response.data || {};
  } catch (error) {
    throw parseError(error);
  }
};

export const clearHistory = async (userUuid, characterName) => {
  try {
    const response = await apiClient.delete('/history', {
      params: {
        user_uuid: userUuid,
        character_name: characterName,
      },
    });
    return response.data || {};
  } catch (error) {
    throw parseError(error);
  }
};

export const fetchDirectorTemplates = async () => {
  try {
    const response = await apiClient.get('/director/templates');
    return response.data || {};
  } catch (error) {
    throw parseError(error);
  }
};

export const createDirectorSession = async (
  templateId,
  characterNames,
  userUuid = '',
) => {
  try {
    const response = await apiClient.post('/director/sessions', {
      template_id: templateId,
      character_names: characterNames,
      user_uuid: userUuid || undefined,
    });
    return response.data || {};
  } catch (error) {
    throw parseError(error);
  }
};

export const deleteDirectorSession = async (sessionId) => {
  try {
    const response = await apiClient.delete(`/director/sessions/${encodeURIComponent(sessionId)}`);
    return response.data || {};
  } catch (error) {
    throw parseError(error);
  }
};

export const directorTurnStream = async (sessionId, events, onEvent) => {
  try {
    const response = await fetch(`${API_BASE_URL}/director/turn_stream`, {
      method: 'POST',
      headers: buildAuthHeaders({
        'Content-Type': 'application/json',
      }),
      body: JSON.stringify({
        session_id: sessionId,
        events,
      }),
    });
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Server Error: ${response.status} - ${errorText}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let eventName = null;
    let dataLines = [];

    const flushEvent = () => {
      if (!dataLines.length) {
        eventName = null;
        return;
      }
      const type = eventName || 'scene_event';
      const rawData = dataLines.join('\n');
      try {
        emitEvent(onEvent, type, JSON.parse(rawData));
      } catch (_err) {
        emitEvent(onEvent, 'error', { detail: `${type} decode failed` });
      }
      eventName = null;
      dataLines = [];
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      lines.forEach((rawLine) => {
        const line = rawLine.replace(/\r$/, '');
        if (!line) {
          flushEvent();
        } else if (line.startsWith('event:')) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trimStart());
        }
      });
    }
    if (buffer.trim()) {
      dataLines.push(buffer.trim());
    }
    flushEvent();
  } catch (error) {
    emitEvent(onEvent, 'error', { detail: error.message || 'Director stream error' });
    throw error;
  }
};
