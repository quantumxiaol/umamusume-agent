<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import { rateLimits } from '@/services/rateLimit';

const waits = ref([]);
let timer;
let unsubscribe;
function refresh() {
  waits.value = rateLimits.snapshot();
  if (waits.value.length && !timer) timer = setInterval(refresh, 1000);
  if (!waits.value.length && timer) {
    clearInterval(timer);
    timer = null;
  }
}
onMounted(() => {
  unsubscribe = rateLimits.subscribe(refresh);
  refresh();
});
onUnmounted(() => {
  unsubscribe?.();
  clearInterval(timer);
});
</script>

<template>
  <aside v-if="waits.length" class="rate-limit-notice" role="status">
    请求过于频繁：
    <span v-for="wait in waits" :key="wait.bucket">
      {{ wait.bucket === 'chat' ? '对话生成' : '历史同步、列表等操作' }}请等待 {{ wait.seconds }} 秒。
    </span>
    等待结束后请手动重试，不会自动发送对话。
  </aside>
</template>

<style scoped>
.rate-limit-notice {
  margin: 0 0 20px;
  padding: 14px 20px;
  border: 1px solid #d9b593;
  border-radius: 16px;
  background: #fff5e7;
  color: #79502d;
  line-height: 1.6;
}
</style>
