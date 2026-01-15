// i18n 配置
import { createI18n } from 'vue-i18n';
import sc from './locales/sc.js';
import tc from './locales/tc.js';
import en from './locales/en.js';
import jp from './locales/jp.js';

// 从 localStorage 获取保存的语言设置，默认为简体中文
const savedLocale = localStorage.getItem('locale') || 'sc';

const i18n = createI18n({
  legacy: false, // 使用 Composition API 模式
  locale: savedLocale, // 默认语言
  fallbackLocale: 'sc', // 回退语言
  messages: {
    'sc': sc,
    'tc': tc,
    'en': en,
    'jp': jp
  }
});

export default i18n;

