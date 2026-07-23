<!-- frontend/src/App.vue -->
<script setup>
import {
  ref,
  computed,
  onMounted,
  nextTick,
  watch,
} from 'vue';
import DirectorMode from '@/components/DirectorMode.vue';
import { DIALOGUE_INPUT_MODES, useChatStore } from '@/stores/chatStore';

const chatStore = useChatStore();
const ttsEnabled = import.meta.env.VITE_ENABLE_TTS === 'true';
const APP_MODE_STORAGE_KEY = 'umamusume_app_mode_v1';

const messageInput = ref('');
const messageInputRef = ref(null);
const isEditingLastUser = ref(false);
const isComposingMessage = ref(false);
const characterFilter = ref('');
const promptOpen = ref(true);
const audioRefs = ref({});
const historyFileInput = ref(null);
const appMode = ref('dialogue');

watch(appMode, (value) => {
  localStorage.setItem(APP_MODE_STORAGE_KEY, value);
});

const characters = computed(() => chatStore.characters);
const userUuid = computed(() => chatStore.userUuid);
const selectedCharacter = computed(() => chatStore.selectedCharacter);
const systemPrompt = computed(() => chatStore.systemPrompt);
const voicePreviewUrl = computed(() => chatStore.voicePreviewUrl);
const outputDir = computed(() => chatStore.outputDir);
const restoredHistoryMessages = computed(() => chatStore.restoredHistoryMessages);
const messages = computed(() => chatStore.messages);
const queuedEvents = computed(() => chatStore.queuedEvents);
const isLoading = computed(() => chatStore.isLoading);
const error = computed(() => chatStore.error);
const exportNotice = computed(() => chatStore.exportNotice);
const streamMode = computed(() => chatStore.streamMode);
const voiceEnabled = computed(() => chatStore.voiceEnabled);
const dialogueEventsEnabled = computed(() => chatStore.dialogueEventsEnabled);
const contextEventBatchEnabled = computed(() => chatStore.contextEventBatchEnabled);
const directorEnabled = computed(() => Number(chatStore.capabilities?.director_mode || 0) >= 1);
const directorMaxParticipants = computed(() => Number(
  chatStore.capabilities?.director_max_participants || 3,
));
const directorMaxSpeakers = computed(() => Number(
  chatStore.capabilities?.director_max_speakers_per_turn || 2,
));
const inputMode = computed(() => chatStore.inputMode);
const inputModeOptions = Object.entries(DIALOGUE_INPUT_MODES).map(([value, preset]) => ({
  value,
  ...preset,
}));
const activeInputPreset = computed(() => (
  DIALOGUE_INPUT_MODES[inputMode.value] || DIALOGUE_INPUT_MODES.dialogue
));
const canExportConversation = computed(() => Boolean(selectedCharacter.value && messages.value.length && !isLoading.value));
const cachedMessageCount = computed(() => chatStore.cachedMessageCount);
const canImportBrowserCache = computed(() => Boolean(selectedCharacter.value && cachedMessageCount.value && !isLoading.value));
const canImportFile = computed(() => Boolean(selectedCharacter.value && !isLoading.value));
const lastUserMessage = computed(() => {
  for (let index = messages.value.length - 1; index >= 0; index -= 1) {
    if (messages.value[index]?.role === 'user') {
      return messages.value[index];
    }
  }
  return null;
});
const canRegenerateLast = computed(() => Boolean(selectedCharacter.value && lastUserMessage.value && !isLoading.value));
const messageParts = computed(() => {
  const map = {};
  messages.value.forEach((message) => {
    if (message.role === 'assistant' && message.renderMode === 'raw') {
      map[message.id] = { action: '', dialogue: '' };
      return;
    }
    if (message.role === 'assistant' && (message.action || message.dialogue)) {
      map[message.id] = {
        action: message.action && message.action !== '无' ? message.action : '',
        dialogue: message.dialogue || message.content || '',
      };
      return;
    }
    map[message.id] = formatMessage(message.content);
  });
  return map;
});

const filteredCharacters = computed(() => {
  const keyword = characterFilter.value.trim().toLowerCase();
  if (!keyword) {
    return characters.value;
  }
  return characters.value.filter((name) => name.toLowerCase().includes(keyword));
});

const handleSelectCharacter = async (name) => {
  if (!name || name === selectedCharacter.value) {
    return;
  }
  isEditingLastUser.value = false;
  await chatStore.selectCharacter(name);
};

const handleSend = async () => {
  if (!messageInput.value.trim() && !queuedEvents.value.length) {
    return;
  }
  const text = messageInput.value;
  messageInput.value = '';
  if (isEditingLastUser.value) {
    if (!text.trim()) {
      return;
    }
    isEditingLastUser.value = false;
    await chatStore.regenerateFromLastUser(text, inputMode.value);
    return;
  }
  await chatStore.sendMessage(text, inputMode.value);
};

const handleQueueMessage = async () => {
  if (isEditingLastUser.value) {
    return;
  }
  const queued = chatStore.queueMessage(messageInput.value, inputMode.value);
  if (!queued) {
    return;
  }
  messageInput.value = '';
  await nextTick();
  messageInputRef.value?.focus();
};

const handleRemoveQueuedEvent = (eventId) => {
  chatStore.removeQueuedEvent(eventId);
};

const handleClearQueuedEvents = () => {
  chatStore.clearQueuedEvents();
};

const handleInputMode = (value) => {
  chatStore.setInputMode(value);
  nextTick(() => messageInputRef.value?.focus());
};

const handleKeydown = async (event) => {
  if (event.isComposing || isComposingMessage.value || event.keyCode === 229) {
    return;
  }
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    await handleSend();
  }
};

const handleCompositionStart = () => {
  isComposingMessage.value = true;
};

const handleCompositionEnd = () => {
  isComposingMessage.value = false;
};

const toggleStreamMode = () => {
  chatStore.setStreamMode(!streamMode.value);
};

const toggleVoice = () => {
  chatStore.setVoiceEnabled(!voiceEnabled.value);
};

const handleRefreshHistory = async () => {
  await chatStore.refreshHistory();
};

const handleClearHistory = async () => {
  if (!selectedCharacter.value) {
    return;
  }
  const confirmed = window.confirm(`确认清空你与「${selectedCharacter.value}」的历史对话吗？`);
  if (!confirmed) {
    return;
  }
  isEditingLastUser.value = false;
  await chatStore.clearCurrentCharacterHistory();
};

const handleRegenerateLast = async () => {
  if (!canRegenerateLast.value) {
    return;
  }
  isEditingLastUser.value = false;
  messageInput.value = '';
  await chatStore.regenerateFromLastUser(lastUserMessage.value.content);
};

const handleEditLastUser = async () => {
  if (!canRegenerateLast.value) {
    return;
  }
  chatStore.clearQueuedEvents();
  messageInput.value = lastUserMessage.value.content || '';
  chatStore.setInputMode(lastUserMessage.value.inputMode || 'dialogue');
  isEditingLastUser.value = true;
  await nextTick();
  messageInputRef.value?.focus();
};

const handleCancelEditLastUser = () => {
  isEditingLastUser.value = false;
};

const handleCopyMarkdown = async () => {
  if (!canExportConversation.value) {
    return;
  }
  await chatStore.copyConversationMarkdown();
};

const handleCopyJson = async () => {
  if (!canExportConversation.value) {
    return;
  }
  await chatStore.copyConversationJson();
};

const handleDownloadMarkdown = () => {
  if (!canExportConversation.value) {
    return;
  }
  chatStore.downloadConversationMarkdown();
};

const handleDownloadJson = () => {
  if (!canExportConversation.value) {
    return;
  }
  chatStore.downloadConversationJson();
};

const confirmHistoryImport = (sourceLabel) => (
  window.confirm(`${sourceLabel}会替换当前会话上下文，并同步到后端用于后续对话。确认继续吗？`)
);

const handleImportBrowserCache = async () => {
  if (!canImportBrowserCache.value) {
    return;
  }
  if (!confirmHistoryImport('导入浏览器缓存')) {
    return;
  }
  isEditingLastUser.value = false;
  await chatStore.importFromBrowserCache();
};

const handleImportFileClick = () => {
  if (!canImportFile.value) {
    return;
  }
  historyFileInput.value?.click();
};

const handleImportFileChange = async (event) => {
  const file = event.target.files?.[0];
  event.target.value = '';
  if (!file) {
    return;
  }
  if (!confirmHistoryImport(`导入「${file.name}」`)) {
    return;
  }
  isEditingLastUser.value = false;
  await chatStore.importConversationFile(file);
};

const playAudio = (messageId) => {
  const audio = audioRefs.value[messageId];
  if (audio) {
    audio.play();
  }
};

const messageActorName = (message) => (
  message.actor?.display_name
  || (message.role === 'user' ? '训练员' : selectedCharacter.value || '角色')
);

const messageEventLabel = (message) => {
  if (!message.eventType) {
    return '';
  }
  if (message.eventType === 'narration') {
    return '旁白';
  }
  return DIALOGUE_INPUT_MODES[message.inputMode]?.shortLabel || message.eventType;
};

const formatMessage = (text) => {
  if (!text) {
    return { action: '', dialogue: '' };
  }
  const actionLines = [];
  const dialogueLines = [];
  let capturingDialogue = false;
  const actionLabels = new Set(['动作', '神态', '场景', '神情', '表情']);
  const dialogueLabels = new Set(['对白', '台词', '对话', 'tts', 'dialogue', 'speech']);
  const dialogueMarkers = ['对白：', '对白:', '台词：', '台词:', '对话：', '对话:', 'TTS：', 'TTS:'];

  const parseLabelledLine = (line) => {
    for (const marker of ['动作：', '动作:', '神态：', '神态:', '场景：', '场景:']) {
      if (line.startsWith(marker)) {
        return { kind: 'action', content: line.slice(marker.length).trim() };
      }
    }
    for (const marker of dialogueMarkers) {
      if (line.startsWith(marker)) {
        return { kind: 'dialogue', content: line.slice(marker.length).trim() };
      }
    }
    const match = line.match(/^([\u4e00-\u9fffA-Za-z]{1,8})[:：]\s*(.*)$/);
    if (!match) {
      return null;
    }
    const label = (match[1] || '').trim().toLowerCase();
    const content = (match[2] || '').trim();
    if (actionLabels.has(label)) {
      return { kind: 'action', content };
    }
    if (dialogueLabels.has(label)) {
      return { kind: 'dialogue', content };
    }
    return { kind: 'unknown', content };
  };

  const splitInlineDialogue = (content) => {
    let match = null;
    dialogueMarkers.forEach((marker) => {
      const index = content.indexOf(marker);
      if (index > 0 && (!match || index < match.index)) {
        match = { index, marker };
      }
    });
    if (!match) {
      return null;
    }
    const action = content.slice(0, match.index).trim();
    const dialogue = content.slice(match.index + match.marker.length).trim();
    if (!action || !dialogue) {
      return null;
    }
    return { action, dialogue };
  };

  text.split(/\n/).forEach((raw) => {
    const line = raw.trim();
    if (!line) {
      return;
    }

    const labelled = parseLabelledLine(line);
    if (labelled) {
      if (labelled.kind === 'action') {
        const inlineSplit = splitInlineDialogue(labelled.content);
        if (inlineSplit) {
          actionLines.push(inlineSplit.action);
          dialogueLines.push(inlineSplit.dialogue);
          capturingDialogue = true;
          return;
        }
        const inlineMatch = labelled.content.match(/^(.*?[。！？；;…])\s*([\u4e00-\u9fffA-Za-z]{1,8})[:：]\s*(.+)$/);
        if (inlineMatch) {
          const inlineAction = (inlineMatch[1] || '').trim();
          const inlineLabel = (inlineMatch[2] || '').trim().toLowerCase();
          const inlineDialogue = (inlineMatch[3] || '').trim();
          if (inlineAction && inlineDialogue && dialogueLabels.has(inlineLabel)) {
            actionLines.push(inlineAction);
            dialogueLines.push(inlineDialogue);
            capturingDialogue = true;
            return;
          }
        }
        capturingDialogue = false;
        if (labelled.content) {
          actionLines.push(labelled.content);
        }
        return;
      }

      if (labelled.kind === 'dialogue') {
        capturingDialogue = true;
        if (labelled.content) {
          dialogueLines.push(labelled.content);
        }
        return;
      }

      if (labelled.kind === 'unknown' && (capturingDialogue || actionLines.length)) {
        capturingDialogue = true;
        if (labelled.content) {
          dialogueLines.push(labelled.content);
        }
        return;
      }
    }

    if (capturingDialogue) {
      dialogueLines.push(line);
      return;
    }
    actionLines.push(line);
  });

  if (dialogueLines.length) {
    return {
      action: actionLines.join(' '),
      dialogue: dialogueLines.join(' '),
    };
  }

  const fallback = actionLines.join(' ') || text.trim();
  const splitMatch = fallback.match(/([。！？；;])(.+)/);
  if (splitMatch) {
    const index = splitMatch.index ?? -1;
    if (index >= 0) {
      const actionPart = fallback.slice(0, index + 1).trim();
      const dialoguePart = fallback.slice(index + 1).trim();
      if (dialoguePart) {
        return {
          action: actionPart,
          dialogue: dialoguePart,
        };
      }
    }
  }

  return {
    action: '',
    dialogue: fallback,
  };
};

onMounted(async () => {
  await chatStore.initCharacters();
  const savedMode = localStorage.getItem(APP_MODE_STORAGE_KEY);
  if (savedMode === 'director' && directorEnabled.value) {
    appMode.value = 'director';
  }
});
</script>

<template>
  <div class="app-shell">
    <div class="app-glow"></div>
    <header class="topbar">
      <div>
        <p class="eyebrow">Umamusume Voice Agent</p>
        <h1>{{ appMode === 'director' ? '赛马娘导演模式' : '赛马娘对话控制台' }}</h1>
        <p class="subtitle">
          {{ appMode === 'director'
            ? '选择场景和参加角色，让导演安排环境变化与依次回应。'
            : '选择角色、查看人格提示词，开启多轮文本对话。' }}
        </p>
      </div>
      <div class="status-panel">
        <div v-if="directorEnabled" class="app-mode-switch">
          <button
            type="button"
            :class="{ active: appMode === 'dialogue' }"
            @click="appMode = 'dialogue'"
          >
            单角色
          </button>
          <button
            type="button"
            :class="{ active: appMode === 'director' }"
            @click="appMode = 'director'"
          >
            导演模式
          </button>
        </div>
        <div class="status-pill">{{ streamMode ? '流式' : '非流式' }}</div>
        <div class="status-pill">{{ ttsEnabled ? `TTS ${voiceEnabled ? '开启' : '关闭'}` : '文本模式' }}</div>
        <div class="status-pill" v-if="outputDir">{{ outputDir.split('/').slice(-1)[0] }}</div>
      </div>
    </header>

    <div v-if="appMode === 'dialogue'" class="layout">
      <aside class="sidebar">
        <section class="card">
          <div class="card-header">
            <h2>角色选择</h2>
            <span class="meta">{{ characters.length }} 人</span>
          </div>
          <input
            v-model="characterFilter"
            class="search-input"
            type="text"
            placeholder="搜索角色"
          />
          <div class="character-list">
            <button
              v-for="name in filteredCharacters"
              :key="name"
              :class="['character-item', { active: name === selectedCharacter }]"
              @click="handleSelectCharacter(name)"
            >
              <span class="character-name">{{ name }}</span>
              <span class="character-tag" v-if="name === selectedCharacter">已加载</span>
            </button>
          </div>
        </section>

        <section v-if="ttsEnabled" class="card">
          <div class="card-header">
            <h2>角色声音</h2>
            <span class="meta">参考音色</span>
          </div>
          <div v-if="voicePreviewUrl" class="audio-preview">
            <audio :src="voicePreviewUrl" controls preload="none"></audio>
            <p class="hint">点击试听，感受角色音色。</p>
          </div>
          <p v-else class="hint muted">加载角色后即可试听声音。</p>
        </section>

        <section class="card">
          <div class="card-header" @click="promptOpen = !promptOpen">
            <h2>人格提示词</h2>
            <button class="link-button">{{ promptOpen ? '收起' : '展开' }}</button>
          </div>
          <div v-if="promptOpen" class="prompt-preview">
            <pre>{{ systemPrompt || '尚未加载角色提示词。' }}</pre>
          </div>
          <p v-else class="hint">点击展开查看完整提示词。</p>
        </section>
      </aside>

      <main class="chat-panel">
        <section class="chat-header">
          <div>
            <h2>{{ selectedCharacter || '未选择角色' }}</h2>
            <p class="meta">历史按当前浏览器与后端临时保存，服务重启后可能丢失。</p>
            <p v-if="selectedCharacter" class="meta">已恢复历史 {{ restoredHistoryMessages }} 条，当前显示 {{ messages.length }} 条。</p>
          </div>
          <div class="toggle-group">
            <label class="toggle">
              <input type="checkbox" :checked="streamMode" @change="toggleStreamMode" />
              <span>流式输出</span>
            </label>
            <label v-if="ttsEnabled" class="toggle">
              <input type="checkbox" :checked="voiceEnabled" @change="toggleVoice" />
              <span>语音合成</span>
            </label>
            <button class="tool-button" :disabled="!canExportConversation" @click="handleCopyMarkdown">复制 Markdown</button>
            <button class="tool-button" :disabled="!canExportConversation" @click="handleDownloadMarkdown">下载 Markdown</button>
            <button class="tool-button" :disabled="!canExportConversation" @click="handleCopyJson">复制 JSON</button>
            <button class="tool-button" :disabled="!canExportConversation" @click="handleDownloadJson">下载 JSON</button>
            <button class="tool-button" :disabled="!canImportBrowserCache" @click="handleImportBrowserCache">
              导入缓存<span v-if="cachedMessageCount"> {{ cachedMessageCount }}</span>
            </button>
            <button class="tool-button" :disabled="!canImportFile" @click="handleImportFileClick">导入文件</button>
            <input
              ref="historyFileInput"
              class="file-input"
              type="file"
              accept=".md,.markdown,.json,text/markdown,application/json"
              @change="handleImportFileChange"
            />
            <button class="tool-button" :disabled="!canRegenerateLast" @click="handleRegenerateLast">重生成上一轮</button>
            <button class="tool-button" :disabled="!canRegenerateLast" @click="handleEditLastUser">编辑上一句</button>
            <button class="tool-button" :disabled="!selectedCharacter || isLoading" @click="handleRefreshHistory">查看历史</button>
            <button class="tool-button danger" :disabled="!selectedCharacter || isLoading" @click="handleClearHistory">清空本角色历史</button>
          </div>
        </section>

        <section class="chat-window">
          <div v-if="!messages.length" class="empty-state">
            <h3>开始对话吧</h3>
            <p>输入一句话，角色会用指定人格与你对话。</p>
          </div>

          <div
            v-for="message in messages"
            :key="message.id"
            :class="['message', message.role, message.inputMode]"
          >
            <div class="message-meta">
              <span>
                {{ messageActorName(message) }}
                <span v-if="messageEventLabel(message)" class="event-label">
                  · {{ messageEventLabel(message) }}
                </span>
              </span>
              <span class="status" v-if="message.status === 'streaming'">生成中…</span>
            </div>
            <div class="message-body">
              <template v-if="message.role === 'assistant'">
                <template v-if="message.renderMode === 'raw'">
                  <pre class="stream-raw">{{ message.content }}</pre>
                </template>
                <template v-else>
                  <div v-if="messageParts[message.id]?.action" class="line action-line">
                    <span class="line-tag action-tag">动作</span>
                    <span class="line-text">{{ messageParts[message.id].action }}</span>
                  </div>
                  <div v-if="messageParts[message.id]?.dialogue" class="line dialogue-line">
                    <span class="line-tag dialogue-tag">对白</span>
                    <p class="line-text">{{ messageParts[message.id].dialogue }}</p>
                  </div>
                </template>
              </template>
              <template v-else>
                <p>{{ message.content }}</p>
              </template>
            </div>

            <div v-if="ttsEnabled && message.role === 'assistant'" class="message-audio">
              <button
                class="audio-button"
                :disabled="!message.voice || !message.voice.audio_url || message.voice.status === 'pending'"
                @click="playAudio(message.id)"
              >
                ▶ 播放语音
              </button>
              <span class="audio-status" v-if="message.voice?.status === 'pending'">合成中</span>
              <audio
                v-if="message.voice?.audio_url"
                :ref="(el) => (audioRefs[message.id] = el)"
                :src="message.voice.audio_url"
                preload="none"
              ></audio>
            </div>
          </div>
        </section>

        <section class="chat-input">
          <div v-if="dialogueEventsEnabled" class="input-mode-selector" aria-label="消息类型">
            <button
              v-for="option in inputModeOptions"
              :key="option.value"
              type="button"
              :class="['input-mode-button', { active: inputMode === option.value }]"
              :disabled="!selectedCharacter || isLoading"
              @click="handleInputMode(option.value)"
            >
              {{ option.label }}
            </button>
          </div>
          <div v-if="queuedEvents.length" class="queued-events">
            <div class="queued-events-header">
              <span>待发送事件 {{ queuedEvents.length }} 条</span>
              <button type="button" class="inline-link" @click="handleClearQueuedEvents">全部清除</button>
            </div>
            <div class="queued-event-list">
              <div v-for="event in queuedEvents" :key="event.id" class="queued-event">
                <span class="queued-event-type">
                  {{ DIALOGUE_INPUT_MODES[event.inputMode]?.shortLabel || event.event_type }}
                </span>
                <span class="queued-event-content">{{ event.content }}</span>
                <button
                  type="button"
                  class="queued-event-remove"
                  :aria-label="`删除${event.content}`"
                  @click="handleRemoveQueuedEvent(event.id)"
                >
                  ×
                </button>
              </div>
            </div>
          </div>
          <div class="input-box">
            <textarea
              ref="messageInputRef"
              v-model="messageInput"
              rows="3"
              :placeholder="dialogueEventsEnabled ? activeInputPreset.placeholder : '输入对话内容，Enter 发送，Shift+Enter 换行'"
              @keydown="handleKeydown"
              @compositionstart="handleCompositionStart"
              @compositionend="handleCompositionEnd"
              :disabled="!selectedCharacter || isLoading"
            ></textarea>
            <div class="input-actions">
              <button
                v-if="contextEventBatchEnabled"
                class="queue-button"
                :disabled="!selectedCharacter || isLoading || isEditingLastUser || !messageInput.trim()"
                @click="handleQueueMessage"
              >
                加入
              </button>
              <button
                class="send-button"
                :disabled="!selectedCharacter || isLoading || (!messageInput.trim() && !queuedEvents.length)"
                @click="handleSend"
              >
                发送
              </button>
            </div>
          </div>
          <div class="meta-row">
            <span v-if="error" class="error">{{ error }}</span>
            <span v-else-if="exportNotice" class="success">{{ exportNotice }}</span>
            <span v-else-if="isEditingLastUser" class="hint">
              正在编辑上一句，发送后会从该轮重新生成。
              <button class="inline-link" type="button" @click="handleCancelEditLastUser">取消</button>
            </span>
            <span v-else-if="queuedEvents.length" class="hint">
              “加入”不会调用模型；点击“发送”后会把这些事件按顺序加入上下文，并只生成一次回复。
            </span>
            <span v-else-if="dialogueEventsEnabled" class="hint">
              {{ inputMode === 'scene_event'
                ? '环境变化会作为共享剧情信息发送，当前角色会观察并主动回应。'
                : inputMode === 'action'
                  ? '动作会明确标记为训练员行为，避免被误认为对白。'
                  : '对白会明确标记为训练员发言。' }}
            </span>
            <span v-else class="hint">支持多轮文本对话；当前后端使用兼容对话协议。</span>
          </div>
        </section>
      </main>
    </div>
    <DirectorMode
      v-else
      :characters="characters"
      :user-uuid="userUuid"
      :max-participants="directorMaxParticipants"
      :max-speakers="directorMaxSpeakers"
    />
  </div>
</template>

<style scoped>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600;700&family=ZCOOL+KuaiLe&display=swap');

:global(:root) {
  --bg: #f3ece2;
  --bg-strong: #e8d8c7;
  --panel: #fffaf2;
  --panel-strong: #f7efe4;
  --accent: #1a6f6b;
  --accent-strong: #165e5a;
  --accent-warm: #d1783b;
  --text: #182526;
  --muted: #6a6f6a;
  --border: #e2d2c2;
  --shadow: 0 18px 50px rgba(33, 41, 38, 0.12);
}

.app-shell {
  min-height: 100vh;
  padding: 32px clamp(16px, 4vw, 48px) 48px;
  font-family: 'Sora', 'Noto Sans SC', sans-serif;
  color: var(--text);
  background:
    radial-gradient(circle at top right, rgba(209, 120, 59, 0.18), transparent 40%),
    radial-gradient(circle at 10% 20%, rgba(26, 111, 107, 0.18), transparent 45%),
    linear-gradient(135deg, var(--bg), var(--bg-strong));
  position: relative;
}

.app-glow {
  position: fixed;
  inset: 0;
  background-image: radial-gradient(rgba(255, 255, 255, 0.8) 1px, transparent 1px);
  background-size: 28px 28px;
  opacity: 0.35;
  pointer-events: none;
}

.topbar {
  position: relative;
  z-index: 1;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 24px;
  padding: 24px 32px;
  border-radius: 24px;
  background: var(--panel);
  box-shadow: var(--shadow);
  margin-bottom: 28px;
}

.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.22em;
  font-size: 12px;
  color: var(--accent);
  margin-bottom: 8px;
}

h1 {
  font-family: 'ZCOOL KuaiLe', sans-serif;
  font-size: clamp(32px, 3.6vw, 46px);
  margin: 0 0 6px;
  letter-spacing: 0.02em;
}

.subtitle {
  margin: 0;
  color: var(--muted);
  font-size: 14px;
}

.status-panel {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.app-mode-switch {
  display: flex;
  padding: 3px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--panel-strong);
}

.app-mode-switch button {
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  padding: 5px 10px;
  font: inherit;
  font-size: 11px;
}

.app-mode-switch button.active {
  background: #fff;
  color: var(--accent-strong);
  box-shadow: 0 2px 8px rgba(33, 41, 38, 0.08);
}

.status-pill {
  padding: 8px 14px;
  border-radius: 999px;
  background: var(--panel-strong);
  border: 1px solid var(--border);
  font-size: 12px;
  color: var(--accent-strong);
}

.layout {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: minmax(260px, 320px) 1fr;
  gap: 24px;
}

.sidebar {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.card {
  background: var(--panel);
  border-radius: 20px;
  padding: 18px;
  border: 1px solid var(--border);
  box-shadow: var(--shadow);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.card-header h2 {
  font-size: 18px;
  margin: 0;
}

.meta {
  font-size: 12px;
  color: var(--muted);
}

.search-input {
  width: 100%;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid var(--border);
  background: #fff;
  margin-bottom: 12px;
}

.character-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 320px;
  overflow: auto;
}

.character-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid transparent;
  background: #fff;
  cursor: pointer;
  transition: all 0.2s ease;
}

.character-item:hover {
  border-color: var(--accent-warm);
  transform: translateX(2px);
}

.character-item.active {
  background: rgba(26, 111, 107, 0.12);
  border-color: var(--accent);
}

.character-tag {
  font-size: 11px;
  color: var(--accent-strong);
  background: rgba(26, 111, 107, 0.12);
  padding: 2px 8px;
  border-radius: 999px;
}

.audio-preview audio {
  width: 100%;
  margin-top: 6px;
}

.hint {
  font-size: 13px;
  color: var(--muted);
  margin: 6px 0 0;
}

.hint.muted {
  color: #9aa09c;
}

.link-button {
  border: none;
  background: none;
  color: var(--accent);
  cursor: pointer;
  font-size: 12px;
}

.inline-link {
  border: none;
  background: transparent;
  color: var(--accent-strong);
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  padding: 0 0 0 6px;
}

.prompt-preview {
  max-height: 240px;
  overflow: auto;
  background: #fff;
  border-radius: 12px;
  padding: 12px;
  border: 1px solid var(--border);
}

.prompt-preview pre {
  margin: 0;
  font-size: 12px;
  white-space: pre-wrap;
  line-height: 1.5;
}

.chat-panel {
  display: grid;
  grid-template-rows: auto 1fr auto;
  gap: 16px;
}

.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 18px 22px;
  background: var(--panel);
  border-radius: 20px;
  border: 1px solid var(--border);
  box-shadow: var(--shadow);
}

.chat-header h2 {
  margin: 0 0 4px;
}

.toggle-group {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 12px;
}

.toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}

.tool-button {
  border: 1px solid var(--border);
  background: #fff;
  color: var(--text);
  border-radius: 999px;
  padding: 6px 12px;
  font-size: 12px;
  cursor: pointer;
}

.tool-button:hover {
  border-color: var(--accent);
  color: var(--accent-strong);
}

.tool-button.danger {
  border-color: rgba(192, 72, 61, 0.35);
  color: #b1443b;
}

.tool-button:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.file-input {
  display: none;
}

.chat-window {
  background: rgba(255, 255, 255, 0.7);
  border-radius: 22px;
  padding: 24px;
  border: 1px solid var(--border);
  box-shadow: var(--shadow);
  min-height: 420px;
  overflow-y: auto;
}

.empty-state {
  text-align: center;
  color: var(--muted);
  margin-top: 120px;
}

.message {
  padding: 14px 16px;
  border-radius: 16px;
  margin-bottom: 16px;
  border: 1px solid transparent;
}

.message.user {
  background: #fff;
  border-color: var(--border);
}

.message.user.action {
  border-left: 4px solid var(--accent-warm);
}

.message.user.scene_event {
  background: rgba(64, 91, 127, 0.08);
  border-color: rgba(64, 91, 127, 0.24);
  border-left: 4px solid #546d8c;
}

.message.assistant {
  background: rgba(26, 111, 107, 0.08);
  border-color: rgba(26, 111, 107, 0.2);
}

.message-meta {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 6px;
}

.event-label {
  color: var(--accent-strong);
  font-weight: 600;
}

.message-body {
  font-size: 14px;
  line-height: 1.6;
}

.stream-raw {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font: inherit;
  line-height: 1.7;
}

.line {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 8px;
}

.line:last-child {
  margin-bottom: 0;
}

.line-tag {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
}

.action-tag {
  background: rgba(106, 111, 106, 0.15);
  color: var(--muted);
}

.dialogue-tag {
  background: rgba(26, 111, 107, 0.18);
  color: var(--accent-strong);
}

.line-text {
  margin: 0;
  line-height: 1.6;
  flex: 1;
}

.message-audio {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
}

.audio-button {
  background: var(--accent);
  color: #fff;
  border: none;
  padding: 6px 12px;
  border-radius: 999px;
  cursor: pointer;
  font-size: 12px;
}

.audio-button:disabled {
  background: #9bbab8;
  cursor: not-allowed;
}

.audio-status {
  font-size: 12px;
  color: var(--muted);
}

.chat-input {
  background: var(--panel);
  border-radius: 20px;
  padding: 18px;
  border: 1px solid var(--border);
  box-shadow: var(--shadow);
}

.input-mode-selector {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.input-mode-button {
  border: 1px solid var(--border);
  border-radius: 999px;
  background: #fff;
  color: var(--muted);
  padding: 7px 13px;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  transition: 0.15s ease;
}

.input-mode-button:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent-strong);
}

.input-mode-button.active {
  border-color: var(--accent);
  background: rgba(26, 111, 107, 0.12);
  color: var(--accent-strong);
  font-weight: 600;
}

.input-mode-button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.queued-events {
  margin-bottom: 12px;
  padding: 12px;
  border: 1px dashed rgba(26, 111, 107, 0.38);
  border-radius: 14px;
  background: rgba(26, 111, 107, 0.05);
}

.queued-events-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
  color: var(--accent-strong);
  font-size: 12px;
  font-weight: 600;
}

.queued-event-list {
  display: grid;
  gap: 7px;
}

.queued-event {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  padding: 8px 10px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.78);
}

.queued-event-type {
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(26, 111, 107, 0.13);
  color: var(--accent-strong);
  font-size: 11px;
  font-weight: 600;
}

.queued-event-content {
  overflow: hidden;
  color: var(--text);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.queued-event-remove {
  border: 0;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font-size: 18px;
  line-height: 1;
}

.input-box {
  display: flex;
  gap: 12px;
}

.input-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

textarea {
  flex: 1;
  border-radius: 14px;
  border: 1px solid var(--border);
  padding: 12px;
  font-family: inherit;
  resize: none;
}

.send-button {
  background: var(--accent-warm);
  border: none;
  color: #fff;
  padding: 0 18px;
  border-radius: 14px;
  cursor: pointer;
  font-weight: 600;
  flex: 1;
}

.send-button:disabled {
  background: #d6b59f;
  cursor: not-allowed;
}

.queue-button {
  flex: 1;
  min-width: 72px;
  border: 1px solid rgba(26, 111, 107, 0.38);
  border-radius: 14px;
  background: rgba(26, 111, 107, 0.08);
  color: var(--accent-strong);
  cursor: pointer;
  font-weight: 600;
}

.queue-button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.meta-row {
  margin-top: 8px;
  font-size: 12px;
}

.error {
  color: #c0483d;
}

.success {
  color: var(--accent-strong);
}

@media (max-width: 980px) {
  .layout {
    grid-template-columns: 1fr;
  }

  .topbar {
    flex-direction: column;
    align-items: flex-start;
  }

  .toggle-group {
    flex-direction: column;
    align-items: flex-start;
  }

  .input-box {
    flex-direction: column;
  }

  .input-actions {
    flex-direction: row;
  }

  .send-button,
  .queue-button {
    height: 44px;
  }
}
</style>
