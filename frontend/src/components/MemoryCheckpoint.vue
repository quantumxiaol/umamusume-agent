<script setup>
import { ref } from 'vue';

defineProps({
  checkpoint: { type: Object, required: true },
  locationKnown: { type: Boolean, default: false },
  active: { type: Boolean, default: false },
  scene: { type: Boolean, default: false },
});
const expanded = ref(false);
</script>

<template>
  <details class="memory-checkpoint" @toggle="expanded = $event.target.open">
    <summary>
      历史压缩 · 第 {{ checkpoint.revision }} 版
      <span class="memory-state">{{ active ? '当前使用' : '历史版本' }}</span>
      <span v-if="!locationKnown" class="memory-state">覆盖范围末尾</span>
      <span class="memory-toggle">{{ expanded ? '收起摘要' : '查看摘要' }}</span>
    </summary>
    <div v-if="expanded" class="memory-details">
      <template v-if="scene">
        <p>在第 {{ checkpoint.trigger_turn_index }} 轮后触发；摘要覆盖前 {{ checkpoint.covered_events }} 条公开事件，由导演和各角色共享。</p>
        <p>触发时其余 {{ checkpoint.trigger_event_count - checkpoint.covered_events }} 条近期事件仍以原文保留，之后继续追加新事件。</p>
      </template>
      <template v-else>
      <p v-if="locationKnown">在第 {{ checkpoint.trigger_message_count }} 条消息后触发；摘要覆盖第 1–{{ checkpoint.covered_messages }} 条消息。</p>
      <p v-else>摘要覆盖第 1–{{ checkpoint.covered_messages }} 条消息。原触发位置未记录或已不在当前历史中，此处展示在覆盖范围末尾。</p>
      <p v-if="locationKnown && checkpoint.trigger_message_count > checkpoint.covered_messages">
        触发时，第 {{ checkpoint.covered_messages + 1 }}–{{ checkpoint.trigger_message_count }} 条消息仍以原文保留在模型上下文中。
      </p>
      </template>
      <p class="memory-note">{{ active ? '仅替换模型读取的早期上下文，聊天原文未删除。' : '此版本仅供回看，不会叠加发送给模型。' }}</p>
      <time v-if="checkpoint.created_at" :datetime="checkpoint.created_at">生成时间：{{ checkpoint.created_at }}</time>
      <!-- Text interpolation keeps imported/model-generated HTML inert. Mount
           the large summary only when open; bound its scrolling area. -->
      <pre class="memory-text" tabindex="0" aria-label="压缩后的历史摘要">{{ checkpoint.summary }}</pre>
    </div>
  </details>
</template>

<style scoped>
.memory-checkpoint {
  margin: 8px 0 16px;
  padding: 12px 16px;
  border: 1px dashed var(--accent-strong, #1e716f);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.6);
  color: var(--accent-strong, #1e716f);
  font-size: 13px;
}
summary {
  cursor: pointer;
  line-height: 1.8;
}
summary:focus-visible {
  outline: 2px solid currentColor;
  outline-offset: 4px;
  border-radius: 4px;
}
.memory-state, .memory-toggle { margin-left: 10px; font-size: 12px; }
.memory-toggle { opacity: 0.8; }
.memory-details { color: var(--ink, #1c2b2a); }
.memory-details p { margin: 10px 0; }
.memory-note, time { opacity: 0.75; }
.memory-text {
  max-height: 24rem;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
  line-height: 1.75;
  padding: 14px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.7);
}
</style>
