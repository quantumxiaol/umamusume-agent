// frontend/src/services/api.js
import axios from 'axios';

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:1111';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 600000,
  headers: {
    'Content-Type': 'application/json',
  },
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

export const chatOnce = async (sessionId, message, generateVoice = false) => {
  try {
    const response = await apiClient.post('/chat', {
      session_id: sessionId,
      message,
      generate_voice: generateVoice,
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

export const chatStream = async (sessionId, message, generateVoice = false, onEvent) => {
  try {
    const url = `${API_BASE_URL}/chat_stream`;
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        session_id: sessionId,
        message,
        generate_voice: generateVoice,
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
      if (type === 'voice_pending') {
        try {
          emitEvent(onEvent, type, JSON.parse(data));
        } catch (err) {
          emitEvent(onEvent, 'error', `voice_pending decode failed: ${data}`);
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
