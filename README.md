---
title: Umamusume Agent
emoji: 🐎
colorFrom: pink
colorTo: blue
sdk: docker
app_port: 7860
short_description: FastAPI backend for Umamusume roleplay chat.
---

# Umamusume Agent

赛马娘角色人格模拟与对话

<img src=resources/png/example.png>

## 目的

构建一个赛马娘角色对话 Agent：
- 角色人格来自 prompt（本地 `result-prompts/`）
- 角色音色来自 voice 数据（本地 `result-voices/`）
- 对话支持流式/非流式
- 默认仅文本回复；当 `generate_voice=true` 时才会调用 IndexTTS MCP 合成语音，音频保存在 `outputs/<角色>_<时间戳>/reply_###.wav`

角色人格可以通过项目[umamusume-agent-prompt](https://github.com/quantumxiaol/umamusume-agent-prompt)获取，角色音色可以通过项目[umamusume-voice-data](https://github.com/quantumxiaol/umamusume-voice-data)在bilibili wiki上爬取，角色音色当前是通过[index-TTS mcp](https://github.com/quantumxiaol/index-tts)实现的。

## 功能概览

- 角色管理：从 `characters/` 加载角色配置
- 对话服务：`/load_character`、`/chat`、`/chat_stream`
- 历史落盘：按 `user_uuid/角色/时间戳/session` 写入 `jsonl` 对话日志（暂时不依赖数据库）
- 对话格式：无论是否启用 TTS，回复都规范为“动作 + 对白”两行；`text_only=true` 仅用于禁用语音合成
- 语音合成：IndexTTS MCP 工具 `tts_synthesize` / `tts_batch_file`
- 前端 UI：角色选择、提示词预览、音色试听、多轮对话、语音播放

## 环境准备

### Python

推荐使用 `uv`（依赖分层更清晰）：

```bash
# 方案 A：使用 uv venv
uv venv --python 3.12
source .venv/bin/activate
uv lock
uv sync

# 方案 B：使用 conda 环境
conda create -n umamusume-agent python=3.12
conda activate umamusume-agent
uv lock
uv sync
```

`uv sync` 默认安装精简版依赖：`dialogue_server` 主链路必需包（角色构建依赖已剥离至 `umamusume-character-build` 项目）。

如需启用其余可选能力，统一安装 `extras`：

```bash
uv sync --extra extras
```

### .env

```bash
cat .env.template > .env
```

- 主要配置：`ROLEPLAY_LLM_MODEL_NAME/BASE_URL/API_KEY`
- 若使用 Qwen，可直接填兼容 OpenAI 的 Base URL
- 代理可选：如未启用代理，建议注释 `HTTP_PROXY/HTTPS_PROXY`
- 会话治理配置：
  - `DIALOGUE_SESSION_TTL_SECONDS`（默认 `3600`，会话空闲超时秒数，`<=0` 表示不启用 TTL）
  - `DIALOGUE_SESSION_HISTORY_MAX_MESSAGES`（默认 `40`，单会话最大历史消息数，`<=0` 表示不裁剪）
  - `DIALOGUE_SESSION_CLEANUP_INTERVAL_SECONDS`（默认 `60`，后台清理扫描间隔秒数）
  - `DIALOGUE_HISTORY_DIRECTORY`（默认 `./outputs/dialogues`，对话历史 `jsonl` 落盘目录）
  - `DIALOGUE_PREFIX_CACHE_ENABLED`（默认 `true`，是否启用前缀缓存注入）
  - `DIALOGUE_PREFIX_CACHE_MIN_CHARS`（默认 `1000`，System Prompt 最小字符数阈值）

当前主链路主要依赖 `ROLEPLAY_LLM_*` 与 `INDEXTTS_MCP_*`；RAG/Web/旧模块需要安装 `extras` 后再配置。

## 数据准备（角色导入）

**注意：** 本项目现已纯粹专注于对话交互阶段（Runtime）。
角色的自动化构建、音频筛选与卡片生成功能，已正式剥离至独立的构建项目 `umamusume-character-build`。

您只需要将构建好或打包好的角色文件夹（内含 `config.json`、`prompt.md`、参考音频等）提取并直接放入本项目的 `characters/` 目录下即可。

## 角色说明与文件组织

### 角色说明

角色配置以目录形式存放，服务从 `characters/<角色名>/` 读取。示例：

```text
characters/
  admire_vega/
    config.json
    prompt.md
    reference.mp3
    reference_jp.txt
    reference_zh.txt
```

文件含义：
- `config.json`：角色基础信息与 `system_prompt`，并包含 TTS 的 `voice_config`（引用音频与文本路径）。
- `prompt.md`：角色人格提示词，便于快速查看与校对。
- `reference.mp3`：TTS 参考音频（格式可能为 mp3/wav）。
- `reference_jp.txt`：日文参考文本，对应参考音频内容。
- `reference_zh.txt`：中文参考文本，便于检索与对照。

### 文件组织形式

- 数据来源：`characters/`（外部导入角色卡片）
- 运行产物：`outputs/`
- 其它资源：`resources/`（来源于旧项目，还未使用）

## 后端使用说明（最新）

### 1) 可选：启动 IndexTTS MCP（仅在需要语音时）

```bash
python mcp_service/server.py --http --host 127.0.0.1 --port 8890
```

### 2) 启动对话服务

```bash
uvicorn umamusume_agent.server.dialogue_server:app --host 0.0.0.0 --port 1111
```

### 3) 服务自检

```bash
curl -s http://127.0.0.1:1111/
```

## 部署说明（GitHub Pages + HF Space）

当前仓库已补齐一套最小部署骨架：

- GitHub Pages：发布 `frontend/` 静态站点
- Hugging Face Space：使用仓库根目录的 `app.py` 与 `Dockerfile` 运行 FastAPI
- 前端生产环境默认直连 `https://quantumxiaol-umamusume-agent.hf.space`
- 前后端默认关闭 TTS，当前部署仅保留文本对话
- 后端支持可选 `X-API-Key` 软门槛与内存级请求限流

### GitHub Pages

工作流文件：`.github/workflows/deploy-pages.yml`

默认生产环境变量：

- `VITE_API_BASE_URL=https://quantumxiaol-umamusume-agent.hf.space`
- `VITE_BASE_PATH=/umamusume-agent/`

Pages 目标地址将是：

- `https://quantumxiaol.github.io/umamusume-agent/`

### Hugging Face Space

推荐使用 `Docker` Space，并在 Space Secrets 中至少配置：

- `ROLEPLAY_LLM_MODEL_NAME`
- `ROLEPLAY_LLM_MODEL_BASE_URL`
- `ROLEPLAY_LLM_MODEL_API_KEY`

可选保护项：

- `API_ACCESS_KEY`
- `API_RATE_LIMIT_ENABLED`
- `API_RATE_LIMIT_WINDOW_SECONDS`
- `API_RATE_LIMIT_MAX_REQUESTS`
- `API_CHAT_RATE_LIMIT_MAX_REQUESTS`

容器启动时会先执行 `docker-entrypoint.sh`，从仓库内的 `.env.template` 生成 `.env`，并用运行时环境变量替换占位符。

如果 Space 上不启用 TTS，可以不配置 `INDEXTTS_MCP_*`，前端默认也是关闭语音生成。

## 前端使用说明（最新）

```bash
cd frontend
pnpm install
pnpm run dev
```

浏览器打开：`http://localhost:5173/`

前端功能与当前后端已同步：
- 角色切换会重新调用 `/load_character`，并展示 `已恢复历史 N 条`。
- 聊天窗口会显示当前用户与该角色的历史对话，不会在切角色时直接丢失历史能力。
- 点击 `查看历史` 会调用 `/history` 刷新该角色历史。
- 点击 `清空本角色历史` 会调用 `DELETE /history` 清理该角色历史。

## 项目结构

```text
|- src/umamusume_agent/
|   |- character/              # 角色模型与管理
|   |- client/                 # CLI 客户端
|   |- crawler/                # 旧爬虫（未使用）
|   |- rag/                    # 旧 RAG（未使用）
|   |- search/                 # 旧搜索（未使用）
|   |- server/                 # 对话服务
|   |- tts/                    # MCP 客户端
|   |- web/                    # 旧 Web MCP（未使用）
|- frontend/                   # 前端 UI
|- tests/                      # 测试脚本
|- umamusume_characters.json   # 角色中英文映射
|- README.md                   # 项目说明 / HF Space 配置
```

## 保留模块说明

- `src/umamusume_agent/crawler/`、`rag/`、`search/`、`web/` 为早期功能保留，当前对话流程未使用。
- `resources/docs/`、`resources/prompt/`、`resources/results/` 为旧版本数据与样例，当前不参与主流程。
- 未来可能还是需要 RAG + Web，取决于未来的 LLM 训练时是否构建了相应知识；如果基础模型本身知识足够，这部分可能就不再需要。
