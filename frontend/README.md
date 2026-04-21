# 前端（对话控制台）

这是基于本项目对话后端的新 UI：可选择角色、查看人格提示词、试听参考音色、进行多轮对话，并为每条回复生成语音。

## Environment

使用 pnpm 管理环境：
```bash
# 安装 pnpm（如果尚未安装）
npm install -g pnpm
pnpm --version

# 安装依赖
cd frontend
pnpm install

# 复制配置
cat .env.template > .env

# 启动开发服务器
pnpm run dev

# 构建生产版本
pnpm run build
```

前端环境变量：

- `VITE_API_BASE_URL`：后端 API 地址，本地默认 `http://127.0.0.1:1111`
- `VITE_API_ACCESS_KEY`：可选；发送给后端的 `X-API-Key`，仅适合作为轻量门槛
- `VITE_BASE_PATH`：静态资源基础路径，本地默认 `/`；GitHub Pages 项目页应设为 `/umamusume-agent/`

## Usage

### 启动后端（本项目根目录）
```bash
# 启动 IndexTTS MCP（在 index-tts 项目里）
python mcp_service/server.py --http --host 127.0.0.1 --port 8890

# 启动对话服务
uvicorn umamusume_agent.server.dialogue_server:app --host 0.0.0.0 --port 1111
```

### 启动前端
```bash
cd frontend
pnpm run dev
```

浏览器打开：`http://localhost:5173/`

## 功能说明

- **角色选择**：从后端 `/characters` 获取已构建角色，点击即可加载
- **提示词预览**：查看已加载角色的系统提示词
- **试听声音**：播放角色参考音频
- **流式 / 非流式**：支持实时输出和一次性回复
- **语音合成**：前端默认关闭；手动开启后，每条回复可生成语音并显示状态

## GitHub Pages 发布

仓库已提供工作流 [.github/workflows/deploy-pages.yml](/Users/quantumxiaol/Desktop/dev/umamusume-agent/.github/workflows/deploy-pages.yml:1)。

- 推送 `main` 分支时自动构建 `frontend/`
- 生产构建默认指向 `https://quantumxiaol-umamusume-agent.hf.space`
- 生产静态资源基础路径默认是 `/umamusume-agent/`

发布前请在仓库设置中启用 GitHub Pages：

1. `Settings -> Pages`
2. `Build and deployment -> Source` 选择 `GitHub Actions`

如果以后更换 HF Space 或仓库名，只需要更新工作流里的：

- `VITE_API_BASE_URL`
- `VITE_BASE_PATH`

## 界面说明

- 左侧：角色列表 + 搜索、音色试听、提示词预览
- 右侧：聊天窗口 + 语音播放 + 输入区

## 项目结构

```
frontend/
|- public/                    # 静态资源
|- src/
|   |- components/            # UI 组件
|   |- services/              # API 请求封装
|   |   |- api.js
|   |- stores/                # Pinia 状态管理
|   |   |- chatStore.js        # 对话状态
|   |- App.vue                # 根组件
|   |- main.js                # 入口
|- index.html
|- .env
|- .env.template
|- vite.config.js
|- package.json
|- pnpm-lock.yaml
```

## 备注

- `.env` 里 `VITE_API_BASE_URL` 默认指向 `http://127.0.0.1:1111`
- `.env` 里 `VITE_BASE_PATH` 本地默认设为 `/`
- 语音文件会保存到后端 `outputs/<角色>_<时间戳>/reply_###.wav`
