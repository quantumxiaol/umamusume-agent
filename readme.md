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

```
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

- 数据来源：`characters/` (外部导入角色卡片)
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

## CLI 使用说明（最新）

```bash
# 交互模式（流式）
python -m src.umamusume_agent.client.cli -u http://127.0.0.1:1111 -c "爱慕织姬" --stream

# 交互模式（流式 + 语音）
python -m src.umamusume_agent.client.cli -u http://127.0.0.1:1111 -c "爱慕织姬" --stream --voice

# 调试模式（查看流式分块与解析细节）
python -m src.umamusume_agent.client.cli -u http://127.0.0.1:1111 -c "爱慕织姬" --stream --debug

# 固定用户 UUID（跨设备/多端共享记忆）
python -m src.umamusume_agent.client.cli -u http://127.0.0.1:1111 -c "爱慕织姬" --user-uuid "your-stable-uuid"
```

说明：
- CLI 默认根据本机用户名生成稳定 `user_uuid`。
- 会话创建后会显示 `已恢复历史: N 条`，代表按 `user_uuid + 角色` 回放到当前会话的消息条数。

CLI 交互命令：
- `history`：查看当前角色历史
- `history all`：查看该用户所有角色历史
- `history <角色名>`：查看指定角色历史
- `clear_history`：清空当前角色历史（会二次确认）
- `clear_history <角色名>`：清空指定角色历史
- `character <角色名>`：切换角色（会加载该角色历史）

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

## FastAPI 接口调用说明

服务地址示例：`http://127.0.0.1:1111`

### 接口总览

| 方法 | 路径 | 作用 |
|------|------|------|
| `GET` | `/` | 健康检查 |
| `POST` | `/load_character` | 加载角色并创建会话 |
| `POST` | `/chat` | 非流式对话 |
| `POST` | `/chat_stream` | 流式对话（SSE） |
| `GET` | `/sessions` | 查看活跃会话 |
| `DELETE` | `/session/{session_id}` | 删除会话 |
| `GET` | `/history` | 查看历史（按用户，支持按角色过滤） |
| `DELETE` | `/history` | 清空指定用户+角色历史 |
| `GET` | `/characters` | 角色列表 |
| `GET`/`HEAD` | `/audio` | 音频文件访问/探测 |

### 1) 健康检查 `GET /`

- 作用：检查服务是否存活
- 返回：`service`、`version`、`status`

```bash
curl -s http://127.0.0.1:1111/
```

### 2) 加载角色并建会话 `POST /load_character`

- 作用：加载角色配置，并创建一个新的 `session_id`；后续对话都通过该 `session_id` 绑定角色

请求 JSON：

| 字段 | 类型 | 必填 | 默认值 | 作用 |
|------|------|------|--------|------|
| `character_name` | string | 是 | - | 角色名（与 `characters/` 中配置匹配） |
| `force_rebuild` | bool | 否 | `false` | 强制重建角色配置缓存 |
| `user_uuid` | string | 否 | `null` | 用户唯一标识；不传则服务端自动生成 |

返回关键字段：

| 字段 | 作用 |
|------|------|
| `session_id` | 对话会话 ID，后续 `/chat` / `/chat_stream` 必填 |
| `user_uuid` | 本次会话绑定的用户 UUID |
| `character_name` / `character_name_jp` | 当前角色信息 |
| `system_prompt` / `personality` | 当前角色提示词与人格配置 |
| `created_at` | 会话创建时间 |
| `restored_history_messages` | 本次会话启动时恢复到内存的历史消息条数（按同 `user_uuid + 角色` 聚合） |
| `output_dir` | 本会话音频输出目录 |
| `history_file` | 本会话历史日志 `jsonl` 路径 |
| `voice_preview_url` | 角色参考音频访问 URL（用于试听） |

```bash
curl -s -X POST http://127.0.0.1:1111/load_character \
  -H "Content-Type: application/json" \
  -d '{
    "character_name": "爱慕织姬",
    "user_uuid": "0a028a76-f436-44d3-bbc9-567704e1e6e1"
  }'
```

### 3) 非流式对话 `POST /chat`

- 作用：一次性返回完整回复（固定为“动作 + 对白”两行）

请求 JSON：

| 字段 | 类型 | 必填 | 默认值 | 作用 |
|------|------|------|--------|------|
| `session_id` | string | 是 | - | 会话 ID |
| `message` | string | 是 | - | 用户输入 |
| `generate_voice` | bool | 否 | `false` | 是否生成 TTS 音频 |
| `text_only` | bool | 否 | `false` | 兼容字段；`true` 时仅禁用语音生成，不改变“动作/对白”输出格式 |

参数行为：

- `generate_voice=false`：仅返回文本回复
- `generate_voice=true`：返回文本回复，并在 `voice` 字段返回语音信息
- `text_only=true`：强制不生成语音（即使 `generate_voice=true`）

```bash
curl -s -X POST http://127.0.0.1:1111/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "替换成你的session_id",
    "message": "你叫什么名字？",
    "generate_voice": false
  }'
```

### 4) 流式对话 `POST /chat_stream`（SSE）

- 作用：按 token 流式返回文本，结束后发 `done` 事件
- 请求 JSON 与 `/chat` 相同

SSE 事件：

- 默认事件（无 `event:`）：token 文本分块
- `event: done`：文本生成结束（`data: {}`）
- `event: voice_pending`：已开始后台语音合成（`data` 为 JSON）
- `event: error`：流式过程错误

```bash
curl -N -X POST http://127.0.0.1:1111/chat_stream \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{
    "session_id": "替换成你的session_id",
    "message": "今天训练计划是什么？",
    "generate_voice": false
  }'
```

### 5) 会话管理

- `GET /sessions`：列出当前活跃会话（含 `session_id`、`user_uuid`、`message_count`、`history_file` 等）
- `DELETE /session/{session_id}`：主动删除会话

```bash
curl -s http://127.0.0.1:1111/sessions
curl -s -X DELETE http://127.0.0.1:1111/session/替换成你的session_id
```

### 6) 历史管理 `GET /history` / `DELETE /history`

- 作用：按 `user_uuid + 角色` 查看/清理历史消息
- 说明：`character_name` 可传中文名/英文名/目录名，服务端会做别名匹配

`GET /history` 参数：

| 字段 | 类型 | 必填 | 默认值 | 作用 |
|------|------|------|--------|------|
| `user_uuid` | string(UUID) | 是 | - | 用户唯一标识 |
| `character_name` | string | 否 | `null` | 角色过滤条件；不传则返回该用户全部角色历史 |
| `limit` | int | 否 | `200` | 返回消息条数上限；`0` 表示返回全部 |

`GET /history` 返回关键字段：

| 字段 | 作用 |
|------|------|
| `total_messages` | 匹配条件下历史总条数 |
| `returned_messages` | 本次实际返回条数 |
| `messages` | 历史消息数组（含 `role/content/timestamp/session_id/message_index`） |
| `characters` | 角色维度统计（`character_name_en/message_count/last_message_at`） |

`DELETE /history` 参数：

| 字段 | 类型 | 必填 | 作用 |
|------|------|------|------|
| `user_uuid` | string(UUID) | 是 | 用户唯一标识 |
| `character_name` | string | 是 | 要清空的角色名（支持别名） |

`DELETE /history` 返回关键字段：`deleted_files`、`deleted_messages`、`cleared_active_sessions`

```bash
# 查看某个角色历史（最近 200 条）
curl -s "http://127.0.0.1:1111/history?user_uuid=0a028a76-f436-44d3-bbc9-567704e1e6e1&character_name=爱慕织姬&limit=200"

# 查看该用户全部角色历史
curl -s "http://127.0.0.1:1111/history?user_uuid=0a028a76-f436-44d3-bbc9-567704e1e6e1&limit=200"

# 清空该用户与某个角色的历史
curl -s -X DELETE "http://127.0.0.1:1111/history?user_uuid=0a028a76-f436-44d3-bbc9-567704e1e6e1&character_name=爱慕织姬"
```

### 7) 角色列表 `GET /characters`

- 作用：查看可用角色名，供 `/load_character` 选择

```bash
curl -s http://127.0.0.1:1111/characters
```

### 8) 音频访问 `GET /audio` / `HEAD /audio`

- 作用：访问或探测角色参考音频、TTS 生成音频
- 参数：`path`（绝对路径或相对路径均可）
- 说明：服务端会校验路径，仅允许 `outputs/` 与 `characters/` 下文件

```bash
curl -I "http://127.0.0.1:1111/audio?path=/absolute/path/to/reply_001.wav"
curl -L "http://127.0.0.1:1111/audio?path=/absolute/path/to/reply_001.wav" -o reply_001.wav
```

### 9) 无前端最小调用流程

1. `GET /characters` 找角色名
2. `POST /load_character` 拿 `session_id`
3. 使用该 `session_id` 调 `/chat` 或 `/chat_stream`
4. 需要切角色时，重新调用一次 `/load_character`

### 会话生命周期与历史落盘

- 会话内存管理：`session_id -> session`
- 记忆恢复：每次 `/load_character` 会按 `user_uuid + character_name_en` 聚合历史 `message` 记录并回放到新会话
- 空闲超时：`DIALOGUE_SESSION_TTL_SECONDS`（默认 `3600` 秒）
- 后台清理间隔：`DIALOGUE_SESSION_CLEANUP_INTERVAL_SECONDS`（默认 `60` 秒）
- 历史裁剪：`DIALOGUE_SESSION_HISTORY_MAX_MESSAGES`（默认 `40`）
- 历史文件：`<DIALOGUE_HISTORY_DIRECTORY>/<user_uuid>/<角色>_<时间戳>_<session前8位>/history.jsonl`
- 历史字段包含：`session_id`、`user_uuid`、`character_name_en`、`timestamp`、`event`、`content` 等

## 默认端口

| 服务 | 端口 | 说明 |
|------|------|------|
| 对话服务 | 1111 | 对话服务（文本 + 可选 TTS） |
| IndexTTS MCP | 8890 | 语音合成工具 |
| 前端 | 5173 | Vite 开发服务器 |

## 目录结构

```
./
|- scripts/                    # 工具脚本
|   |- check_status.py         # 数据状态检查
|- characters/                 # 导入的角色卡片目录
|- outputs/                    # 对话语音输出
|- resources/                  # 资源与文档（部分为遗留）
|   |- docs/                   # RAG 文档（未使用）
|   |- prompt/                 # 旧提示词库（未使用）
|   |- results/                # 旧结果样例（未使用）
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
|- readme.md                   # 项目说明
```

## 保留模块说明

- `src/umamusume_agent/crawler/`、`rag/`、`search/`、`web/` 为早期功能保留，当前对话流程未使用。
- `resources/docs/`、`resources/prompt/`、`resources/results/` 为旧版本数据与样例，当前不参与主流程。
- 未来可能还是需要rag+web，取决于未来的LLM训练时是否构建了响应的知识，如果LLM进一步发展扩充pre train的语料，有可能他的知识就足够了，也就不需要RAG+Web画蛇添足了。

## 备注

- 语音合成是异步后台进行：流式对话结束后会显示 `voice_pending`，音频落地后可播放。
- 无论 `text_only` 是否为 `true`，LLM 输出都按“动作/对白”规范；`text_only=true` 仅用于禁用语音生成。

## 当前存在的问题

如果在MAC上使用MPS推理TTS，会非常慢，而且有内存泄漏的问题。
