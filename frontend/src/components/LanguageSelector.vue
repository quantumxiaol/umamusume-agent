<script setup>
import { useI18n } from 'vue-i18n';
import { ref, watch } from 'vue';

const { locale, t } = useI18n();

const languages = [
  { code: 'sc', label: 'SC', name: 'language.sc' },
  { code: 'tc', label: 'TC', name: 'language.tc' },
  { code: 'en', label: 'EN', name: 'language.en' },
  { code: 'jp', label: 'JP', name: 'language.jp' }
];

const showDropdown = ref(false);

// 切换语言
const changeLanguage = (langCode) => {
  locale.value = langCode;
  localStorage.setItem('locale', langCode);
  showDropdown.value = false;
};

// 获取当前语言标签
const currentLabel = () => {
  const current = languages.find(lang => lang.code === locale.value);
  return current ? current.label : 'SC';
};

// 点击外部关闭下拉菜单
const handleClickOutside = (event) => {
  const selector = document.querySelector('.language-selector');
  if (selector && !selector.contains(event.target)) {
    showDropdown.value = false;
  }
};

// 监听点击事件
watch(showDropdown, (newVal) => {
  if (newVal) {
    document.addEventListener('click', handleClickOutside);
  } else {
    document.removeEventListener('click', handleClickOutside);
  }
});
</script>

<template>
  <div class="language-selector">
    <button 
      class="language-button" 
      @click.stop="showDropdown = !showDropdown"
      :title="t('language.select')"
    >
      <span class="language-icon">🌐</span>
      <span class="language-label">{{ currentLabel() }}</span>
      <span class="dropdown-arrow" :class="{ 'open': showDropdown }">▼</span>
    </button>
    
    <transition name="dropdown">
      <div v-if="showDropdown" class="language-dropdown">
        <button
          v-for="lang in languages"
          :key="lang.code"
          class="language-option"
          :class="{ 'active': locale === lang.code }"
          @click="changeLanguage(lang.code)"
        >
          <span class="option-label">{{ lang.label }}</span>
          <span class="option-name">{{ t(lang.name) }}</span>
          <span v-if="locale === lang.code" class="check-mark">✓</span>
        </button>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.language-selector {
  position: relative;
  display: inline-block;
}

.language-button {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  background: rgba(255, 255, 255, 0.95);
  border: 2px solid #ddd;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  transition: all 0.3s ease;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.language-button:hover {
  border-color: #42b983;
  transform: translateY(-2px);
  box-shadow: 0 4px 8px rgba(0, 0, 0, 0.15);
}

.language-icon {
  font-size: 18px;
}

.language-label {
  font-weight: 600;
  color: #2c3e50;
  min-width: 24px;
  text-align: center;
}

.dropdown-arrow {
  font-size: 10px;
  color: #666;
  transition: transform 0.3s ease;
}

.dropdown-arrow.open {
  transform: rotate(180deg);
}

.language-dropdown {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  min-width: 180px;
  background: rgba(255, 255, 255, 0.98);
  border: 2px solid #ddd;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  overflow: hidden;
  z-index: 1000;
}

.language-option {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 12px 16px;
  background: transparent;
  border: none;
  cursor: pointer;
  font-size: 14px;
  transition: background-color 0.2s ease;
  text-align: left;
}

.language-option:hover {
  background-color: rgba(66, 185, 131, 0.1);
}

.language-option.active {
  background-color: rgba(66, 185, 131, 0.15);
}

.option-label {
  font-weight: 600;
  color: #42b983;
  min-width: 28px;
}

.option-name {
  flex: 1;
  color: #2c3e50;
}

.check-mark {
  color: #42b983;
  font-weight: bold;
  font-size: 16px;
}

/* 下拉动画 */
.dropdown-enter-active,
.dropdown-leave-active {
  transition: all 0.3s ease;
}

.dropdown-enter-from {
  opacity: 0;
  transform: translateY(-10px);
}

.dropdown-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}

/* 响应式设计 */
@media (max-width: 768px) {
  .language-button {
    padding: 6px 12px;
    font-size: 13px;
  }
  
  .language-dropdown {
    min-width: 160px;
  }
  
  .language-option {
    padding: 10px 12px;
    font-size: 13px;
  }
}
</style>

