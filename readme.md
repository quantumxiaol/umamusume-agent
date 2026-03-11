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
- 纯文本模式：`text_only=true` 时输出自然文本，不使用“动作/对白”标签，并且不会触发 TTS
- 语音合成：IndexTTS MCP 工具 `tts_synthesize` / `tts_batch_file`
- 前端 UI：角色选择、提示词预览、音色试听、多轮对话、语音播放

## 环境准备

### Python

建议使用 conda 或 uv：

```bash
conda create -n umamusume-agent python=3.12
conda activate umamusume-agent
pip install -r requirements.txt
```

或

```bash
uv venv --python 3.12
source .venv/bin/activate
uv lock
uv sync
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

现在用到的只有LLM的API，剩下的未来可能会用。

## 数据准备

在使用了[umamusume-agent-prompt](https://github.com/quantumxiaol/umamusume-agent-prompt)和[umamusume-voice-data](https://github.com/quantumxiaol/umamusume-voice-data)获取到数据后，把数据移动到`result-prompts/`和`result-voices/`下。

### 1) 检查现有数据

```bash
python scripts/check_status.py --show-voice-only
```

### 2) 构建角色目录

```bash
python build_character.py
python build_character.py --workers 2
```

会在 `characters/<角色>/` 下生成：
- `config.json`
- `prompt.md`
- `reference.*`
- `reference_jp.txt` / `reference_zh.txt`

当前音频筛选有些问题，可以自己选择没有鼻音、静音少、语调正常的语句并重命名。

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

### 构建过程

```text
result-prompts/ + result-voices/ -> build_character.py -> characters/<角色名>/
```

简要流程：
- 拉取/整理角色 prompt 与音频数据，放入 `result-prompts/` 和 `result-voices/`。
- 可先用 `scripts/check_status.py` 检查缺失情况。
- 运行 `python build_character.py` 生成角色目录与配置文件。

### 文件组织形式

- 原始输入：`result-prompts/`、`result-voices/`
- 构建输出：`characters/`
- 运行产物：`outputs/`
- 其它资源：`resources/`（来源于旧项目，还未使用）

## 启动服务

### 1) 启动 IndexTTS MCP（可选，仅在需要语音时）

```bash
python mcp_service/server.py --http --host 127.0.0.1 --port 8890
```

### 2) 启动对话服务（本项目）

```bash
uvicorn umamusume_agent.server.dialogue_server:app --host 0.0.0.0 --port 1111
```

## CLI 客户端测试

```bash
python -m src.umamusume_agent.client.cli -u http://127.0.0.1:1111 -c "爱慕织姬" --stream --voice
```

## 前端

```bash
cd frontend
pnpm install
pnpm run dev
```

浏览器打开：`http://localhost:5173/`

## 接口简述

- `POST /load_character`：加载角色并创建会话
- `POST /chat`：非流式对话
- `POST /chat_stream`：流式对话（SSE）
- `GET /characters`：可用角色列表
- `GET /audio?path=...`：音频文件访问

### 对话请求参数（`/chat` 与 `/chat_stream`）

- `session_id`：会话 ID（由 `/load_character` 返回）
- `message`：用户输入文本
- `generate_voice`：是否生成语音（默认 `false`）
- `text_only`：是否纯文本模式（默认 `false`）

参数组合说明：
- `generate_voice=false, text_only=false`：结构化文本回复（通常含“动作/对白”标签），不生成语音。
- `generate_voice=true, text_only=false`：结构化文本回复，并触发 TTS 生成语音。
- `text_only=true`：纯文本回复（无“动作/对白”标签），并强制不生成语音（即使 `generate_voice=true` 也会忽略）。

### 会话生命周期与内存控制

- 会话通过内存字典管理（`session_id -> session`）。
- 每条消息会刷新会话活跃时间；超出 `DIALOGUE_SESSION_TTL_SECONDS` 的空闲会话会被清理。
- 服务启动后会有后台任务按 `DIALOGUE_SESSION_CLEANUP_INTERVAL_SECONDS` 周期扫描并删除过期会话。
- 对话历史会按 `DIALOGUE_SESSION_HISTORY_MAX_MESSAGES` 自动裁剪，避免单会话无限增长。
- 会话过期或被删除后，再用旧 `session_id` 调用 `/chat` 会返回 `404`，需要重新 `/load_character` 获取新会话。

## 无前端：纯文本模式如何选定角色

核心逻辑：角色是通过 `POST /load_character` 绑定到 `session_id` 的。后续 `/chat` 只需要传这个 `session_id`。

### 1) 查看可用角色（可选）

```bash
curl -s http://127.0.0.1:1111/characters
```

### 2) 选定角色并创建会话

```bash
curl -s -X POST http://127.0.0.1:1111/load_character \
  -H "Content-Type: application/json" \
  -d '{"character_name":"爱慕织姬"}'
```

返回里拿到 `session_id`，例如：

```json
{
  "session_id": "0f0f7f4f-xxxx-xxxx-xxxx-8b0b5f9a8d2b",
  "character_name": "爱慕织姬"
}
```

### 3) 用该会话进行纯文本对话

```bash
curl -s -X POST http://127.0.0.1:1111/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id":"0f0f7f4f-xxxx-xxxx-xxxx-8b0b5f9a8d2b",
    "message":"你好，今天训练安排是什么？",
    "text_only":true
  }'
```

如果想切换角色，重新调用一次 `/load_character` 获取新的 `session_id` 即可。

## 默认端口

| 服务 | 端口 | 说明 |
|------|------|------|
| 对话服务 | 1111 | 对话服务（文本 + 可选 TTS） |
| IndexTTS MCP | 8890 | 语音合成工具 |
| 前端 | 5173 | Vite 开发服务器 |

## 目录结构

```
./
|- build_character.py          # 构建角色配置
|- scripts/                    # 工具脚本
|   |- check_status.py         # 数据状态检查
|- characters/                 # 角色配置输出
|- result-prompts/             # 角色 prompt 原始结果
|- result-voices/              # 角色音频原始结果
|- outputs/                    # 对话语音输出
|- resources/                  # 资源与文档（部分为遗留）
|   |- docs/                   # RAG 文档（未使用）
|   |- prompt/                 # 旧提示词库（未使用）
|   |- results/                # 旧结果样例（未使用）
|- src/umamusume_agent/
|   |- builder/                # 本地构建封装（调用 build_character.py）
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
- 当 `text_only=false` 时，LLM 输出按“动作/对白”规范，服务端会优先抽取“对白”用于 TTS。

## 当前存在的问题

如果在MAC上使用MPS推理TTS，会非常慢，而且有内存泄漏的问题。
