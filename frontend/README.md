# 前端（对话控制台）

这是本项目的 Vue + Pinia 前端，包含单角色对话、剧情事件队列、多角色导演场景、
浏览器历史恢复和可选异步日语配音。

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
cp .env.template .env

# 启动开发服务器
pnpm run dev

# 构建生产版本
pnpm run build
```

前端环境变量：

- `VITE_API_BASE_URL`：后端 API 地址，本地默认 `http://127.0.0.1:1111`
- `VITE_API_ACCESS_KEY`：可选；发送给后端的 `X-API-Key`，仅适合作为轻量门槛
- `VITE_ENABLE_TTS`：是否显示 TTS 开关；本地开发默认 `true`，GitHub Pages 生产构建强制为 `false`
- `VITE_BASE_PATH`：静态资源基础路径，本地默认 `/`；GitHub Pages 项目页应设为 `/umamusume-agent/`

## Usage

### 启动后端（本项目根目录）
```bash
# 启动项目内 TTS MCP（ENABLE_TTS=true 时需要；Docker 可自动启动）
uv run python -m umamusume_agent.tts.mcp_server

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
- **流式 / 非流式**：支持实时输出和一次性回复
- **剧情事件队列**：训练员对白、动作和环境事件可先加入，再整组触发一次回复
- **导演模式**：选择预设或自定义场景，并选择 1～3 位角色共享公开时间线
- **历史恢复**：单角色历史与导演场景按当前浏览器 `user_uuid` 隔离和恢复
- **异步日语配音**：TTS 打开时只处理发送后新产生的角色对白，完成后手动点击播放
- **导演回复重生成**：可重生成场景中最后一条角色回复；先取消旧配音任务，再原位置替换并只合成新版本
- **错误回复不配音**：结构化解析最终失败的安全提示不会提交 TTS，也不会触发本轮后续角色接话
- **不回填旧语音**：TTS 关闭期间的对白不会在重新打开时批量补合成
- **轻量恢复**：浏览器只保存任务引用和状态，不保存音频文件

## GitHub Pages 发布

仓库已提供工作流
[`.github/workflows/deploy-pages.yml`](../.github/workflows/deploy-pages.yml)。

- 推送 `main` 分支时自动构建 `frontend/`
- 生产构建默认指向 `https://quantumxiaol-umamusume-agent.hf.space`
- 生产构建禁用 TTS UI（`VITE_ENABLE_TTS=false`）
- 当前 HF 后端同样保持 `ENABLE_TTS=false`；TTS 只在本地开发环境启用
- 生产静态资源基础路径默认是 `/umamusume-agent/`

发布前请在仓库设置中启用 GitHub Pages：

1. `Settings -> Pages`
2. `Build and deployment -> Source` 选择 `GitHub Actions`

如果以后更换 HF Space 或仓库名，只需要更新工作流里的：

- `VITE_API_BASE_URL`
- `VITE_BASE_PATH`

## 界面说明

- 单角色页：角色列表、人格预览、参考音色、历史操作、聊天窗口和事件输入区
- 导演页：场景与演员选择、共享时间线、环境状态、场景历史和事件输入区
- 顶部工具栏：单角色/导演模式、流式开关和可用时的 TTS 开关

## 项目结构

```
frontend/
|- public/                    # 静态资源
|- src/
|   |- components/            # UI 组件
|   |- services/              # API 请求封装
|   |   |- api.js
|   |- stores/                # Pinia 状态管理
|   |   |- chatStore.js       # 单角色对话、历史与 TTS Job 轮询
|   |   |- directorStore.js   # 多人场景、恢复与 TTS Job 轮询
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
- 合成任务和临时音频保存在后端内存/`outputs/tts_jobs/`，到期会清理
- 页面刷新后会用缓存的 `job_id` 重新查询播放地址；音频本身不会写入 `localStorage`
