<script setup>
import { computed, nextTick, onMounted, ref } from 'vue';

import { DIALOGUE_INPUT_MODES } from '@/stores/chatStore';
import { useDirectorStore } from '@/stores/directorStore';


const props = defineProps({
  characters: {
    type: Array,
    default: () => [],
  },
  userUuid: {
    type: String,
    default: '',
  },
  maxParticipants: {
    type: Number,
    default: 3,
  },
  maxSpeakers: {
    type: Number,
    default: 2,
  },
});

const store = useDirectorStore();
const input = ref('');
const inputRef = ref(null);
const isComposing = ref(false);
const characterFilter = ref('');

const inputModes = Object.entries(DIALOGUE_INPUT_MODES).map(([value, item]) => ({
  value,
  ...item,
}));
const selectedTemplate = computed(() => store.templates.find(
  (item) => item.template_id === store.selectedTemplateId,
));
const filteredCharacters = computed(() => {
  const keyword = characterFilter.value.trim().toLowerCase();
  return keyword
    ? props.characters.filter((name) => name.toLowerCase().includes(keyword))
    : props.characters;
});
const stateItems = computed(() => {
  const labels = {
    location: '地点',
    sub_location: '位置',
    time: '时间',
    weather: '天气',
    lighting: '光线',
    atmosphere: '氛围',
    ambient_sound: '环境声',
  };
  return Object.entries(labels)
    .map(([key, label]) => ({ label, value: store.sceneState?.[key] }))
    .filter((item) => item.value);
});

const eventActorName = (event) => event.actor?.display_name || '环境';
const eventKind = (event) => {
  const labels = {
    dialogue: '对白',
    action: '动作',
    scene_event: '环境事件',
    scene_change: '场景变化',
    narration: '旁白',
    character_reply: '角色回应',
  };
  return labels[event.event_type] || event.event_type;
};

const toggleCharacter = (name) => {
  store.toggleCharacter(name, props.maxParticipants);
};

const createScene = async () => {
  await store.createScene(props.userUuid);
};

const queueCurrent = async () => {
  if (!store.queueEvent(input.value)) {
    return;
  }
  input.value = '';
  await nextTick();
  inputRef.value?.focus();
};

const send = async () => {
  if (!input.value.trim() && !store.queuedEvents.length) {
    return;
  }
  const text = input.value;
  input.value = '';
  await store.sendTurn(text);
};

const handleKeydown = async (event) => {
  if (event.isComposing || isComposing.value || event.keyCode === 229) {
    return;
  }
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    await send();
  }
};

onMounted(() => store.init());
</script>

<template>
  <div class="director-shell">
    <template v-if="!store.sessionId">
      <section class="director-setup-card">
        <div class="section-heading">
          <div>
            <p class="section-kicker">Scene Template</p>
            <h2>选择预制场景</h2>
          </div>
          <span>{{ store.templates.length }} 个</span>
        </div>
        <div class="template-grid">
          <button
            v-for="template in store.templates"
            :key="template.template_id"
            type="button"
            :class="['template-option', { active: store.selectedTemplateId === template.template_id }]"
            @click="store.setTemplate(template.template_id)"
          >
            <strong>{{ template.name }}</strong>
            <span>{{ template.description }}</span>
          </button>
        </div>
        <div v-if="selectedTemplate" class="initial-state">
          <span>{{ selectedTemplate.initial_state.location }}</span>
          <span v-if="selectedTemplate.initial_state.sub_location">{{ selectedTemplate.initial_state.sub_location }}</span>
          <span>{{ selectedTemplate.initial_state.time }}</span>
          <span>{{ selectedTemplate.initial_state.weather }}</span>
        </div>
      </section>

      <section class="director-setup-card">
        <div class="section-heading">
          <div>
            <p class="section-kicker">Cast</p>
            <h2>选择参加角色</h2>
          </div>
          <span>{{ store.selectedCharacterNames.length }}/{{ maxParticipants }}</span>
        </div>
        <input
          v-model="characterFilter"
          class="director-search"
          type="text"
          placeholder="搜索角色"
        />
        <div class="director-character-list">
          <button
            v-for="name in filteredCharacters"
            :key="name"
            type="button"
            :class="['director-character', { active: store.selectedCharacterNames.includes(name) }]"
            @click="toggleCharacter(name)"
          >
            <span>{{ name }}</span>
            <span>{{ store.selectedCharacterNames.includes(name) ? '已选择' : '加入场景' }}</span>
          </button>
        </div>
        <button
          type="button"
          class="create-scene-button"
          :disabled="store.isLoading || !store.selectedTemplateId || !store.selectedCharacterNames.length"
          @click="createScene"
        >
          {{ store.isLoading ? '正在创建…' : '开始导演场景' }}
        </button>
        <p v-if="store.error" class="director-error">{{ store.error }}</p>
      </section>
    </template>

    <template v-else>
      <section class="scene-toolbar">
        <div>
          <p class="section-kicker">Director Session · Turn {{ store.turnIndex }}</p>
          <h2>{{ selectedTemplate?.name || '导演场景' }}</h2>
          <div class="cast-line">
            <span v-for="participant in store.participants" :key="participant.actor.actor_id">
              {{ participant.actor.display_name }}
            </span>
          </div>
        </div>
        <button type="button" class="reset-scene-button" :disabled="store.isLoading" @click="store.resetScene">
          结束场景
        </button>
      </section>

      <section class="scene-state-card">
        <span v-for="item in stateItems" :key="item.label" class="scene-state-item">
          <small>{{ item.label }}</small>
          {{ item.value }}
        </span>
      </section>

      <section class="scene-timeline">
        <div v-if="!store.events.length" class="director-empty">场景尚未开始。</div>
        <article
          v-for="event in store.events"
          :key="event.event_id"
          :class="['scene-message', event.event_type]"
        >
          <header>
            <strong>{{ eventActorName(event) }}</strong>
            <span>{{ eventKind(event) }}</span>
          </header>
          <div v-if="event.action" class="scene-action">{{ event.action }}</div>
          <p v-if="event.dialogue || event.content">{{ event.dialogue || event.content }}</p>
        </article>
        <div v-if="store.isLoading" class="director-working">
          导演正在整理场景并安排角色回应…
        </div>
      </section>

      <section class="director-composer">
        <div class="director-mode-tabs">
          <button
            v-for="mode in inputModes"
            :key="mode.value"
            type="button"
            :class="{ active: store.inputMode === mode.value }"
            :disabled="store.isLoading"
            @click="store.setInputMode(mode.value)"
          >
            {{ mode.label }}
          </button>
        </div>
        <div v-if="store.queuedEvents.length" class="director-queue">
          <div v-for="event in store.queuedEvents" :key="event.id">
            <span>{{ DIALOGUE_INPUT_MODES[event.inputMode].shortLabel }}</span>
            <p>{{ event.content }}</p>
            <button type="button" @click="store.removeQueuedEvent(event.id)">×</button>
          </div>
          <button type="button" class="clear-queue" @click="store.clearQueuedEvents">清空待发送</button>
        </div>
        <div class="director-input-row">
          <textarea
            ref="inputRef"
            v-model="input"
            rows="3"
            :placeholder="DIALOGUE_INPUT_MODES[store.inputMode].placeholder"
            :disabled="store.isLoading"
            @keydown="handleKeydown"
            @compositionstart="isComposing = true"
            @compositionend="isComposing = false"
          ></textarea>
          <div>
            <button type="button" :disabled="store.isLoading || !input.trim()" @click="queueCurrent">加入</button>
            <button
              type="button"
              class="director-send"
              :disabled="store.isLoading || (!input.trim() && !store.queuedEvents.length)"
              @click="send"
            >
              发送
            </button>
          </div>
        </div>
        <p v-if="store.error" class="director-error">{{ store.error }}</p>
        <p v-else class="director-hint">导演每轮最多调度 {{ maxSpeakers }} 位角色；后发言的角色能听到前一位角色的公开发言。</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.director-shell {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(300px, 0.75fr);
  gap: 24px;
}

.director-setup-card,
.scene-toolbar,
.scene-state-card,
.scene-timeline,
.director-composer {
  border: 1px solid var(--border);
  border-radius: 22px;
  background: var(--panel);
  box-shadow: var(--shadow);
}

.director-setup-card {
  padding: 22px;
}

.section-heading,
.scene-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 18px;
}

.section-heading {
  margin-bottom: 16px;
}

.section-heading h2,
.scene-toolbar h2 {
  margin: 0;
}

.section-heading > span {
  color: var(--muted);
  font-size: 12px;
}

.section-kicker {
  margin: 0 0 5px;
  color: var(--accent);
  font-size: 11px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.template-grid,
.director-character-list {
  display: grid;
  gap: 9px;
}

.template-option,
.director-character {
  border: 1px solid var(--border);
  border-radius: 14px;
  background: #fff;
  color: var(--text);
  cursor: pointer;
  text-align: left;
}

.template-option {
  display: grid;
  gap: 5px;
  padding: 13px;
}

.template-option span {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.55;
}

.template-option.active,
.director-character.active {
  border-color: var(--accent);
  background: rgba(26, 111, 107, 0.1);
}

.initial-state,
.cast-line {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 14px;
}

.initial-state span,
.cast-line span {
  padding: 4px 9px;
  border-radius: 999px;
  background: var(--panel-strong);
  color: var(--accent-strong);
  font-size: 11px;
}

.director-search {
  width: 100%;
  box-sizing: border-box;
  margin-bottom: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 12px;
}

.director-character-list {
  max-height: 330px;
  overflow: auto;
}

.director-character {
  display: flex;
  justify-content: space-between;
  padding: 10px 12px;
}

.director-character span:last-child {
  color: var(--muted);
  font-size: 11px;
}

.create-scene-button,
.director-send {
  border: 0;
  background: var(--accent-warm);
  color: #fff;
  font-weight: 600;
}

.create-scene-button {
  width: 100%;
  margin-top: 14px;
  padding: 12px;
  border-radius: 13px;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.scene-toolbar {
  grid-column: 1 / -1;
  padding: 18px 22px;
}

.reset-scene-button {
  padding: 7px 12px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: #fff;
  color: #a14b43;
}

.scene-state-card {
  display: flex;
  flex-wrap: wrap;
  grid-column: 1 / -1;
  gap: 10px;
  padding: 13px 18px;
}

.scene-state-item {
  display: flex;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 10px;
  background: var(--panel-strong);
  font-size: 12px;
}

.scene-state-item small {
  color: var(--muted);
}

.scene-timeline {
  min-height: 460px;
  max-height: 66vh;
  overflow: auto;
  padding: 20px;
}

.scene-message {
  margin-bottom: 12px;
  padding: 12px 14px;
  border: 1px solid rgba(26, 111, 107, 0.18);
  border-radius: 14px;
  background: rgba(26, 111, 107, 0.07);
}

.scene-message.dialogue,
.scene-message.action {
  border-color: var(--border);
  background: #fff;
}

.scene-message.narration,
.scene-message.scene_event,
.scene-message.scene_change {
  border-color: rgba(84, 109, 140, 0.25);
  background: rgba(84, 109, 140, 0.08);
}

.scene-message header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 6px;
  color: var(--accent-strong);
  font-size: 12px;
}

.scene-message header span {
  color: var(--muted);
}

.scene-message p,
.scene-action {
  margin: 0;
  font-size: 14px;
  line-height: 1.65;
}

.scene-action {
  margin-bottom: 5px;
  color: var(--muted);
  font-style: italic;
}

.director-working,
.director-empty {
  padding: 30px;
  color: var(--muted);
  text-align: center;
}

.director-composer {
  align-self: start;
  padding: 18px;
}

.director-mode-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-bottom: 11px;
}

.director-mode-tabs button,
.director-input-row button {
  padding: 7px 11px;
  border: 1px solid var(--border);
  border-radius: 11px;
  background: #fff;
  color: var(--muted);
}

.director-mode-tabs button.active {
  border-color: var(--accent);
  background: rgba(26, 111, 107, 0.12);
  color: var(--accent-strong);
}

.director-queue {
  display: grid;
  gap: 6px;
  margin-bottom: 10px;
  padding: 10px;
  border-radius: 12px;
  background: rgba(26, 111, 107, 0.06);
}

.director-queue > div {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 7px;
}

.director-queue span {
  color: var(--accent-strong);
  font-size: 11px;
}

.director-queue p {
  overflow: hidden;
  margin: 0;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.director-queue button {
  border: 0;
  background: transparent;
  color: var(--muted);
}

.director-queue .clear-queue {
  justify-self: end;
  color: var(--accent-strong);
  font-size: 11px;
}

.director-input-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 9px;
}

.director-input-row textarea {
  box-sizing: border-box;
  width: 100%;
  padding: 11px;
  border: 1px solid var(--border);
  border-radius: 12px;
  font: inherit;
  resize: vertical;
}

.director-input-row > div {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.director-input-row button {
  flex: 1;
  min-width: 66px;
}

.director-input-row .director-send {
  border-color: transparent;
  color: #fff;
}

.director-hint,
.director-error {
  margin: 9px 0 0;
  font-size: 11px;
  line-height: 1.5;
}

.director-hint {
  color: var(--muted);
}

.director-error {
  color: #c0483d;
}

@media (max-width: 900px) {
  .director-shell {
    grid-template-columns: 1fr;
  }

  .scene-toolbar,
  .scene-state-card {
    grid-column: 1;
  }
}
</style>
