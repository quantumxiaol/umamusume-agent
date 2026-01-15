# Umamusume Agent

赛马娘角色人格模拟与对话

<img src=resources/png/example.png>

## 目的

构建一个赛马娘角色对话 Agent：
- 角色人格来自 prompt（本地 `result-prompts/`）
- 角色音色来自 voice 数据（本地 `result-voices/`）
- 对话支持流式/非流式
- 回复自动调用 IndexTTS MCP 合成语音，保存在 `outputs/<角色>_<时间戳>/reply_###.wav`

角色人格可以通过项目[umamusume-agent-prompt](https://github.com/quantumxiaol/umamusume-agent-prompt)获取，角色音色可以通过项目[umamusume-voice-data](https://github.com/quantumxiaol/umamusume-voice-data)在bilibili wiki上爬取，角色音色当前是通过[index-TTS mcp](https://github.com/quantumxiaol/index-tts)实现的。

## 功能概览

- 角色管理：从 `characters/` 加载角色配置
- 对话服务：`/load_character`、`/chat`、`/chat_stream`
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

### 1) 启动 IndexTTS MCP（在 index-tts 项目）

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

## 默认端口

| 服务 | 端口 | 说明 |
|------|------|------|
| 对话服务 | 1111 | 对话 + TTS 结果输出 |
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
- LLM 输出需按“动作/对白”规范，服务端会优先抽取“对白”用于 TTS。

## 当前存在的问题

如果在MAC上使用MPS推理TTS，会非常慢，而且有内存泄漏的问题。
