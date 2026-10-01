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

角色选择列表由后端按 `config.json` 的 `name_en` 忽略大小写进行 A–Z 排序，前端仍显示中文名。
缺少英文名时使用角色目录名排序；英文名相同时用目录名确定顺序。本地、HF 和 GitHub Pages 共用此规则。

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
│   │   ├── compaction.py          # 高低水位、完整轮次保留与分段记忆编排
│   │   ├── compaction_runtime.py  # 摘要独立输出预算、流式调用与截断重试
│   │   ├── memory.py              # Checkpoint 校验与模型历史视图
│   │   ├── token_budget.py        # token 估算与实际用量校准
│   │   ├── history.py             # JSONL 读取、恢复与导入
│   │   ├── history_order.py       # 历史、重置与摘要的时间归一化和稳定排序
│   │   ├── models.py              # Actor、事件与 Runtime 数据模型
│   │   ├── protocol.py            # action/dialogue 协议与兼容
│   │   ├── runtime.py             # LLM 调用、修复与重生成
│   │   ├── service.py             # 完整单角色轮次
│   │   └── session.py             # DialogueSession
│   ├── director/                  # 多角色导演场景
│   │   ├── context.py             # 导演/角色独立 PromptThread
│   │   ├── compaction.py          # 场景公共摘要、全线程水位与分段压缩
│   │   ├── memory.py              # 摘要校验、共享前缀和恢复边界
│   │   ├── history.py             # 导演 JSONL 和 revision 恢复
│   │   ├── models.py              # 场景、计划、事件和快照
│   │   ├── recovery.py            # JSONL 回放与浏览器快照校验恢复
│   │   ├── runtime.py             # DirectorRuntime 与计划校验
│   │   ├── service.py             # 会话生命周期、调度与重生成
│   │   ├── session.py             # SceneSession
│   │   ├── templates.py           # 场景预设仓库
│   │   └── timeline.py            # 共享事件流与场景状态
│   ├── server/
│   │   ├── dialogue_server.py     # 保持兼容的 Uvicorn / HF 启动入口
│   │   ├── app.py                 # 应用工厂、路由挂载与启动/退出生命周期
│   │   ├── services.py            # 每个应用独立的依赖装配与测试注入点
│   │   ├── middleware.py          # API Key 与应用独立的限流状态
│   │   ├── schemas.py             # 单角色 HTTP 请求模型
│   │   ├── sessions.py            # 单角色会话注册、恢复与过期清理
│   │   ├── http_utils.py          # HTTP 错误和浏览器 UUID 转换
│   │   ├── dialogue_routes.py     # 单聊、会话、角色与历史 API
│   │   ├── dialogue_turns.py      # 会话锁、压缩进度与可取消 SSE 任务
│   │   ├── streaming.py           # 旧两行协议的 token 流式响应
│   │   ├── tts_routes.py          # 音频、TTS Job API 与单聊配音适配
│   │   ├── director_routes.py     # /director API 与 SSE
│   │   └── stage_routes.py        # /stage API
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
│   │   │   ├── MemoryCheckpoint.vue # 单角色时间线中的折叠摘要与触发位置
│   │   │   └── LanguageSelector.vue
│   │   ├── i18n/                 # 简中、繁中、日文和英文文本
│   │   ├── services/api.js       # 单聊、导演、历史与 TTS Job API
│   │   ├── services/historyCache.js # 单角色全文及记忆的 IndexedDB 原子缓存
│   │   ├── services/memoryTimeline.js # 展示行映射，不修改原始对话
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
│   ├── dialogue_memory.md         # 长历史压缩预算、缓存、恢复与兼容性
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

浏览器中的对话和导演场景将原文及摘要存入 IndexedDB，作为 HF 临时容器之外的恢复副本；
localStorage 保留浏览器身份、场景索引和偏好，旧历史缓存成功迁移后才移除。
音频 Blob/Base64 不写入浏览器历史。
