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
- 对话支持非流式请求与 SSE 流式请求；JSON 模式下不会把半截 JSON token 直接推给前端
- 默认仅文本回复；当 `generate_voice=true` 时才会调用 IndexTTS MCP 合成语音，音频保存在 `outputs/<角色>_<时间戳>/reply_###.wav`

角色人格可以通过项目[umamusume-agent-prompt](https://github.com/quantumxiaol/umamusume-agent-prompt)获取，角色音色可以通过项目[umamusume-voice-data](https://github.com/quantumxiaol/umamusume-voice-data)在bilibili wiki上爬取，角色音色当前是通过[index-TTS mcp](https://github.com/quantumxiaol/index-tts)实现的。

## 功能概览

- 角色管理：从 `characters/` 加载角色配置
- 对话服务：`/load_character`、`/chat`、`/chat_stream`
- 剧情事件：单角色页面可发送训练员对白、训练员动作或环境事件，角色会基于明确的说话者与事件类型回应
- 导演模式：选择预制场景和 1～3 位角色，由导演 LLM 更新环境并安排最多两位角色顺序回应
- 历史落盘：按 `user_uuid/角色/时间戳/session` 写入 `jsonl` 对话日志；assistant 消息使用 v2 结构字段保存
- 对话格式：后端主协议为 `{"action":"...","dialogue":"..."}` JSON；API 响应返回 `action`、`dialogue`、`message`，不再返回旧两行 `reply`
- 语音合成：IndexTTS MCP 工具 `tts_synthesize` / `tts_batch_file`
- 前端 UI：角色选择、提示词预览、音色试听、多轮对话、语音播放

## 仓库

Github：[https://github.com/quantumxiaol/umamusume-agent](https://github.com/quantumxiaol/umamusume-agent)

HuggingFace：[https://huggingface.co/spaces/quantumxiaol/umamusume-agent/tree/main](https://huggingface.co/spaces/quantumxiaol/umamusume-agent/tree/main)

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
- JSON 回复协议配置：
  - `LLM_JSON_ENABLED`（默认 `true`，启用 JSON 回复主链路）
  - `LLM_JSON_OUTPUT_MODE`（默认 `auto`，可选 `auto/response_format/prompt_only/disabled`）
  - `LLM_JSON_RETRY_WITHOUT_RESPONSE_FORMAT_ON_ERROR`（默认 `true`，auto 模式下遇到明确不支持 `response_format/json_object` 时本轮降级）
  - `LLM_JSON_PARSE_LOOSE_JSON`（默认 `true`，允许解析代码块或嵌入文本中的 JSON object）
  - `LLM_JSON_MAX_RETRIES`（默认 `1`，JSON 解析失败后的 prompt-only 修复次数）
  - `LLM_JSON_REGENERATE_ON_PARSE_FAILURE`（默认 `true`，修复仍失败时基于最近训练员发言重新生成）
  - `LLM_JSON_MAX_REGENERATE_ATTEMPTS`（默认 `1`，最终安全降级前的重新生成次数）
  - `LLM_JSON_TEMPERATURE` / `LLM_JSON_MAX_TOKENS`（JSON 回复请求参数）
- 会话治理配置：
  - `DIALOGUE_SESSION_TTL_SECONDS`（默认 `3600`，会话空闲超时秒数，`<=0` 表示不启用 TTL）
  - `DIALOGUE_SESSION_HISTORY_MAX_MESSAGES`（默认 `0`，单会话最大历史消息数，`<=0` 表示不裁剪；DeepSeek 自动上下文缓存建议保持不裁剪）
  - `DIALOGUE_SESSION_CLEANUP_INTERVAL_SECONDS`（默认 `60`，后台清理扫描间隔秒数）
  - `DIALOGUE_HISTORY_DIRECTORY`（默认 `./outputs/dialogues`，对话历史 `jsonl` 落盘目录）
  - `DIALOGUE_PREFIX_CACHE_ENABLED`（默认 `true`，是否启用前缀缓存注入）
  - `DIALOGUE_PREFIX_CACHE_MIN_CHARS`（默认 `1000`，System Prompt 最小字符数阈值）
  - `DIALOGUE_HIDDEN_FORMAT_REINJECTION_ENABLED`（默认 `true`，是否启用后端隐藏格式约束再注入；不写入历史、不导出到前端）
  - `DIALOGUE_HIDDEN_FORMAT_REINJECTION_INTERVAL_MESSAGES`（默认 `100`，每隔多少条 user/assistant 历史消息插入一次隐藏格式约束；约等于每 50 轮对话提醒一次）
  - `DIRECTOR_MAX_PARTICIPANTS`（默认 `3`，导演场景最多选择角色数）
  - `DIRECTOR_MAX_SPEAKERS_PER_TURN`（默认 `2`，每轮最多顺序回应角色数）
  - `DIRECTOR_LLM_TEMPERATURE` / `DIRECTOR_LLM_MAX_TOKENS`（导演计划 JSON 的生成参数）
  - `DIRECTOR_ROLE_REINJECTION_INTERVAL_REPLIES`（默认 `25`，按导演或单个角色自己的回复次数重新注入约束）
  - `SCENE_TEMPLATES_DIRECTORY` / `DIRECTOR_HISTORY_DIRECTORY`（预制场景与独立场景历史目录）
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

## 后端使用说明

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

## 对话协议与流式行为

### 可选剧情事件协议（v1）

`GET /capabilities` 用于前后端独立部署时协商能力。`dialogue_events >= 1`
表示后端支持剧情事件；旧后端没有该接口时，前端会自动隐藏消息类型选择并继续发送旧请求。
`context_event_batch >= 1` 表示支持“加入”队列后一次生成回复。

原有 `/chat` 和 `/chat_stream` 请求不变。只有选择消息类型后，前端才追加以下可选字段：

```json
{
  "session_id": "...",
  "message": "夜幕降临，窗外开始下雨。",
  "speaker": {
    "actor_id": "narrator",
    "actor_type": "narrator",
    "display_name": "环境",
    "role_in_scene": "environment"
  },
  "event_type": "scene_event"
}
```

当前单角色页面提供三种输入：

- `dialogue`：训练员对白。
- `action`：训练员动作。
- `scene_event`：时间、天气、地点等环境变化；当前赛马娘会观察并主动回应。

输入框的“加入”按钮只把当前事件暂存在浏览器中，不会请求 LLM。“发送”会把待发送
事件作为 `context_events` 按顺序提交，最后一个事件作为本轮 `message`，整组只生成一次
赛马娘回复。待发送列表支持逐条删除或全部清除。

启用事件协议后，请求与角色回复会把 `actor`、`event_type`、
`target_actor_ids`、`event_schema_version` 写入 JSONL 历史、浏览器缓存和导出文件。
构造 LLM 上下文时会渲染为 `【训练员对白】`、`【训练员动作】`、
`【环境变化】` 等明确标签。未携带事件字段的旧请求、旧响应和旧历史保持原样。

### JSON 回复协议

默认启用 JSON 回复主链路。模型被要求只输出：

```json
{"action":"角色动作、神态或心理描写；没有则写“无”","dialogue":"角色对训练员说的话"}
```

`/chat` 返回结构化字段：

```json
{
  "action": "光钻轻轻点头。",
  "dialogue": "训练员，我们开始今天的训练吧。",
  "message": {
    "schema_version": 2,
    "role": "assistant",
    "content": "训练员，我们开始今天的训练吧。",
    "action": "光钻轻轻点头。",
    "dialogue": "训练员，我们开始今天的训练吧。",
    "source_format": "json_v2"
  }
}
```

`/chat_stream` 在 JSON 模式下不会把模型生成中的半截 JSON 逐 token 发给前端，而是等后端完整解析并校验后发送：

```text
event: structured_reply
data: {"action":"...","dialogue":"...","message":{...}}

event: done
data: {}
```

如果关闭 JSON 主链路（`LLM_JSON_OUTPUT_MODE=disabled`），服务会退回旧两行文本协议，仅作为兼容/调试模式。

### response_format 降级

`LLM_JSON_OUTPUT_MODE=auto` 时，后端会优先尝试 `response_format={"type":"json_object"}`。如果上游返回明确的 `response_format/json_object unsupported/unknown/unrecognized` 类 400/422 错误，本轮会自动重试 prompt-only JSON，并在当前运行期记住该 base URL + model 不支持 `response_format`。API key、模型名、base URL 等普通错误不会被吞掉。

### 解析失败与自动重生成

安全提示 `光钻有点没听清，训练员可以再说一次吗？` 只会在 JSON 主链路无法得到可用 `dialogue` 时出现，例如上游返回空内容、不是 JSON object、JSON 缺少非空 `dialogue`，并且修复与重生成都失败。

默认失败处理顺序：

1. 正常 JSON 请求。
2. 解析失败后进行一次 prompt-only JSON 修复（`LLM_JSON_MAX_RETRIES=1`）。
3. 修复仍失败时，忽略失败输出，基于最近训练员发言重新生成一次（`LLM_JSON_REGENERATE_ON_PARSE_FAILURE=true`、`LLM_JSON_MAX_REGENERATE_ATTEMPTS=1`）。
4. 仍失败才返回安全提示，并以 `source_format=parse_error` 写入历史。

长上下文对话中如果频繁出现安全提示，优先尝试：缩短/裁剪历史、提高隐藏格式提醒频率、降低温度，或使用前端的“编辑上一句 / 重生成上一轮”从最近一轮重新生成。

### 历史与 TTS

- 历史文件中 assistant 消息保存 `schema_version=2`、`content`、`action`、`dialogue`、`source_format`。
- 传给 LLM 的历史上下文不会塞 raw JSON，而是压成自然语言：`角色动作：...` / `角色对白：...`。
- `/history/import` 可导入 v2 JSON，也兼容旧 `role/content`、旧“动作：/对白：”文本和 Markdown 导出；`replace_current=true` 且 `messages=[]` 会清空当前 session 上下文。
- TTS 只消费解析后的 `dialogue` 字段；`action` 不参与合成。

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

导演模式复用现有 `ROLEPLAY_LLM_*` 模型配置，不需要增加第二套模型密钥。详细协议、
范围和前缀复用约束见 [`docs/director_mode_v1.md`](docs/director_mode_v1.md)。

剧情事件升级建议先发布 Hugging Face 后端，再发布 GitHub Pages 前端。新后端兼容旧前端；
新前端也会通过 `/capabilities` 自动兼容尚未升级的旧后端，因此两个部署不要求同时完成。

## 前端使用说明（最新）

```bash
cd frontend
pnpm install
pnpm run dev
```

浏览器打开：`http://localhost:5173/`

前端功能与当前后端已同步：
- 角色切换会重新调用 `/load_character`，并展示 `已恢复历史 N 条`。
- 新后端启用剧情事件能力时，输入框上方可切换“训练员对白 / 训练员动作 / 环境事件”；旧后端下自动回退旧界面。
- 后端声明 `director_mode=1` 时，顶部可进入独立导演页面，选择预制场景和参加角色后开始共享场景。
- 聊天窗口会显示当前用户与该角色的历史对话，不会在切角色时直接丢失历史能力。
- 点击 `查看历史` 会调用 `/history` 刷新该角色历史。
- 对话会以 v2 结构同步写入当前浏览器的 `localStorage` 缓存；剧情事件使用独立的 `event_schema_version=1`，并兼容迁移旧 v1 `role/content` 缓存。
- 可将当前显示的对话复制或下载为 JSON/Markdown；JSON 是权威恢复格式，Markdown 末尾会附带 v2 JSON block；也可从 Markdown/JSON 文件手动导入历史，导入后会替换当前 session 上下文并同步到后端。
- 可点击 `重生成上一轮` 直接删除最近一条训练员发言及其后的回复，按原文重新生成。
- 可点击 `编辑上一句` 将最近一条训练员发言放回输入框，修改后发送；前端会先截断该轮之后的历史并同步后端，再重新生成。
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
