// frontend/vite.config.js
import { fileURLToPath, URL } from 'node:url';
import { defineConfig, loadEnv } from 'vite';
import vue from '@vitejs/plugin-vue';

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');

  return {
    base: env.VITE_BASE_PATH || '/',
    plugins: [
      vue(),
    ],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)) // 设置 '@' 别名指向 src 目录
      }
    },
    server: {
      host: '0.0.0.0', // 允许外部访问 (可选，方便手机等设备访问)
      port: 5173,      // 默认端口
    }
  };
});
