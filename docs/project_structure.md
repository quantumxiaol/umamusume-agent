# 角色数据与项目结构

## 角色数据

本项目专注对话 Runtime。角色自动构建、音频筛选和卡片生成已移到独立构建项目
`umamusume-character-build`。

将构建好的角色目录放入 `characters/`：

```text
characters/
  admire_vega/
    config.json
    prompt.md
    reference.mp3
    reference_jp.txt
    reference_zh.txt
```

| 文件 | 用途 |
| --- | --- |
| `config.json` | 角色基础信息、system prompt 和 TTS `voice_config` |
| `prompt.md` | 便于阅读和校对的人格提示词 |
| `reference.mp3` / `.wav` | TTS 参考音频 |
| `reference_jp.txt` | 参考音频的日文文本 |
| `reference_zh.txt` | 检索和对照用中文文本 |

角色人格来源可参考
[umamusume-agent-prompt](https://github.com/quantumxiaol/umamusume-agent-prompt)，
音色数据工具可参考
[umamusume-voice-data](https://github.com/quantumxiaol/umamusume-voice-data)。

## 项目结构

```text
.
├── src/umamusume_agent/
│   ├── character/                 # CharacterConfig 与角色卡加载
│   ├── dialogue/                  # 单角色对话核心
│   │   ├── context.py             # Prompt、前缀缓存与约束再注入
│   │   ├── history.py             # JSONL 读取、恢复与导入
│   │   ├── models.py              # Actor、事件与 Runtime 数据模型
│   │   ├── protocol.py            # action/dialogue 协议与兼容
│   │   ├── runtime.py             # LLM 调用、修复与重生成
│   │   ├── service.py             # 完整单角色轮次
│   │   └── session.py             # DialogueSession
│   ├── director/                  # 多角色导演场景
│   │   ├── context.py             # 导演/角色独立 PromptThread
│   │   ├── history.py             # 导演 JSONL 和 revision 恢复
│   │   ├── models.py              # 场景、计划、事件和快照
│   │   ├── runtime.py             # DirectorRuntime 与计划校验
│   │   ├── service.py             # 调度、顺序生成与恢复
│   │   ├── session.py             # SceneSession
│   │   ├── templates.py           # 场景预设仓库
│   │   └── timeline.py            # 共享事件流与场景状态
│   ├── server/
│   │   ├── dialogue_server.py     # FastAPI 入口、中间件和路由装配
│   │   └── director_routes.py     # /director API 与 SSE
│   ├── tts/                       # 异步日语配音链路
│   │   ├── agent.py               # 中文对白→日语配音文本
│   │   ├── audio_utils.py          # 可选本地音频处理工具
│   │   ├── engine.py               # 保留的 CosyVoice 本地引擎
│   │   ├── fish_client.py         # Fish Speech HTTP 客户端
│   │   ├── jobs.py                # 任务、并发、取消和 TTL
│   │   ├── mcp_client.py          # TTS MCP 及 IndexTTS 兼容客户端
│   │   ├── mcp_server.py          # 项目内 TTS MCP Server
│   │   ├── models.py              # TTS 协议模型
│   │   ├── service.py             # Dialogue/Director 到 MCP 适配
│   │   └── text_optimizer.py      # 保留的旧文本优化工具
│   └── client/                    # CLI 客户端
├── frontend/                      # Vue + Pinia 前端
│   ├── src/
│   │   ├── components/
│   │   │   ├── DirectorMode.vue  # 场景选择、共享时间线与重生成 UI
│   │   │   └── LanguageSelector.vue
│   │   ├── i18n/                 # 简中、繁中、日文和英文文本
│   │   ├── services/api.js       # 单聊、导演、历史与 TTS Job API
│   │   ├── stores/chatStore.js   # 单角色状态、历史、事件队列与语音轮询
│   │   ├── stores/directorStore.js # 导演场景、恢复、revision 与语音轮询
│   │   ├── App.vue               # 单角色/导演模式入口
│   │   └── main.js
│   ├── .env.template             # 前端构建变量模板
│   └── vite.config.js
├── scenes/                         # 场景预设
├── characters/                     # 外部导入的角色数据
├── docs/
│   ├── configuration.md           # 完整环境变量
│   ├── deployment.md              # GitHub Pages + HF 自部署
│   ├── dialogue_architecture.md   # 单角色 Runtime 依赖边界
│   ├── dialogue_protocol.md       # 事件、JSON、SSE 与历史协议
│   ├── director_mode_v1.md        # 多角色调度和前缀缓存
│   ├── project_structure.md       # 本文档
│   └── tts_pipeline.md            # TTS Agent、MCP 与 Fish Speech
├── tests/                          # 后端回归测试
├── outputs/                        # JSONL 副本与临时 TTS 音频
├── resources/                      # README 预览资源
├── .github/workflows/              # GitHub Pages 工作流
├── app.py                          # HF / Uvicorn 启动入口
├── Dockerfile                      # HF Docker Space
├── .env.template                   # 后端配置模板
├── umamusume_characters.json       # 角色中英文名称映射
└── README.md                       # 项目入口
```

## 数据与运行产物

- `characters/`：外部导入的角色卡、Prompt 和参考音频。
- `scenes/`：公园、赛马场、河边、教室和训练场等预设。
- `outputs/dialogues/`：单角色 JSONL 历史。
- `outputs/director/`：导演场景 JSONL 快速恢复副本。
- `outputs/tts_jobs/`：有 TTL 的临时音频。
- `resources/`：项目文档和预览资源。

浏览器中的对话和导演场景使用 `localStorage` 作为 HF 临时容器之外的恢复副本；
音频 Blob/Base64 不写入浏览器历史。
