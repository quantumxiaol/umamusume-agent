<!-- frontend/src/App.vue -->
<script setup>
import { ref, computed, onMounted } from 'vue';
import { useChatStore } from '@/stores/chatStore';

const chatStore = useChatStore();
const ttsEnabled = import.meta.env.VITE_ENABLE_TTS === 'true';

const messageInput = ref('');
const characterFilter = ref('');
const promptOpen = ref(true);
const audioRefs = ref({});

const characters = computed(() => chatStore.characters);
const selectedCharacter = computed(() => chatStore.selectedCharacter);
const systemPrompt = computed(() => chatStore.systemPrompt);
const voicePreviewUrl = computed(() => chatStore.voicePreviewUrl);
const outputDir = computed(() => chatStore.outputDir);
const restoredHistoryMessages = computed(() => chatStore.restoredHistoryMessages);
const messages = computed(() => chatStore.messages);
const isLoading = computed(() => chatStore.isLoading);
const error = computed(() => chatStore.error);
const streamMode = computed(() => chatStore.streamMode);
const voiceEnabled = computed(() => chatStore.voiceEnabled);
const messageParts = computed(() => {
  const map = {};
  messages.value.forEach((message) => {
    if (message.role === 'assistant' && message.renderMode === 'raw') {
      map[message.id] = { action: '', dialogue: '' };
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
  await chatStore.selectCharacter(name);
};

const handleSend = async () => {
  if (!messageInput.value.trim()) {
    return;
  }
  const text = messageInput.value;
  messageInput.value = '';
  await chatStore.sendMessage(text);
};

const handleKeydown = async (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    await handleSend();
  }
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
  await chatStore.clearCurrentCharacterHistory();
};

const playAudio = (messageId) => {
  const audio = audioRefs.value[messageId];
  if (audio) {
    audio.play();
  }
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

  text.split(/\n/).forEach((raw) => {
    const line = raw.trim();
    if (!line) {
      return;
    }

    const labelled = parseLabelledLine(line);
    if (labelled) {
      if (labelled.kind === 'action') {
        const inlineMatch = labelled.content.match(/^(.*?[。！？；;…])\s*([\u4e00-\u9fffA-Za-z]{1,8})[:：]\s*(.+)$/);
        if (inlineMatch) {
          const inlineAction = (inlineMatch[1] || '').trim();
          const inlineLabel = (inlineMatch[2] || '').trim().toLowerCase();
          const inlineDialogue = (inlineMatch[3] || '').trim();
          if (inlineAction && inlineDialogue && !actionLabels.has(inlineLabel)) {
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

onMounted(() => {
  chatStore.initCharacters();
});
</script>

<template>
  <div class="app-shell">
    <div class="app-glow"></div>
    <header class="topbar">
      <div>
        <p class="eyebrow">Umamusume Voice Agent</p>
        <h1>赛马娘对话控制台</h1>
        <p class="subtitle">选择角色、查看人格提示词，开启多轮文本对话。</p>
      </div>
      <div class="status-panel">
        <div class="status-pill">{{ streamMode ? '流式' : '非流式' }}</div>
        <div class="status-pill">{{ ttsEnabled ? `TTS ${voiceEnabled ? '开启' : '关闭'}` : '文本模式' }}</div>
        <div class="status-pill" v-if="outputDir">{{ outputDir.split('/').slice(-1)[0] }}</div>
      </div>
    </header>

    <div class="layout">
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
            <button class="tool-button" :disabled="!selectedCharacter || isLoading" @click="handleRefreshHistory">查看历史</button>
            <button class="tool-button danger" :disabled="!selectedCharacter || isLoading" @click="handleClearHistory">清空本角色历史</button>
          </div>
        </section>

        <section class="chat-window">
          <div v-if="!messages.length" class="empty-state">
            <h3>开始对话吧</h3>
            <p>输入一句话，角色会用指定人格与你对话。</p>
          </div>

          <div v-for="message in messages" :key="message.id" :class="['message', message.role]">
            <div class="message-meta">
              <span>{{ message.role === 'user' ? '训练员' : selectedCharacter || '角色' }}</span>
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
          <div class="input-box">
            <textarea
              v-model="messageInput"
              rows="3"
              placeholder="输入对话内容，Enter 发送，Shift+Enter 换行"
              @keydown="handleKeydown"
              :disabled="!selectedCharacter || isLoading"
            ></textarea>
            <button
              class="send-button"
              :disabled="!selectedCharacter || isLoading || !messageInput.trim()"
              @click="handleSend"
            >
              发送
            </button>
          </div>
          <div class="meta-row">
            <span v-if="error" class="error">{{ error }}</span>
            <span v-else class="hint">支持多轮文本对话；历史为临时保存，不保证长期保留。</span>
          </div>
        </section>
      </main>
    </div>
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

.input-box {
  display: flex;
  gap: 12px;
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
}

.send-button:disabled {
  background: #d6b59f;
  cursor: not-allowed;
}

.meta-row {
  margin-top: 8px;
  font-size: 12px;
}

.error {
  color: #c0483d;
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

  .send-button {
    height: 44px;
  }
}
</style>
