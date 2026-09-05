# 配置说明

后端配置模板是根目录 `.env.template`，前端配置模板是
`frontend/.env.template`。

## 本地配置

```bash
cp .env.template .env
cp frontend/.env.template frontend/.env
```

生产 HF Docker Space 不应把真实 Key 提交进 `.env`。`docker-entrypoint.sh` 会复制
`.env.template`，并使用 HF 运行时 Variables / Secrets 替换必需占位符。

## 必需 LLM 配置

| 变量 | 说明 |
| --- | --- |
| `ROLEPLAY_LLM_MODEL_NAME` | OpenAI 兼容服务的对话模型 ID |
| `ROLEPLAY_LLM_MODEL_BASE_URL` | OpenAI 兼容 Base URL |
| `ROLEPLAY_LLM_MODEL_API_KEY` | 供应商 API Key；生产只放 HF Secret |
| `ROLEPLAY_LLM_TIMEOUT_SECONDS` | 上游请求超时，默认 `60` |
| `ROLEPLAY_LLM_MAX_RETRIES` | SDK 级请求重试，默认 `2` |

Qwen 示例：

```text
ROLEPLAY_LLM_MODEL_NAME=qwen-plus
ROLEPLAY_LLM_MODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
ROLEPLAY_LLM_MODEL_API_KEY=sk-xxxxxxxx
```

模型 ID、Base URL 和 Key 地域必须与供应商控制台一致。购买与自部署步骤见
[`deployment.md`](deployment.md)。

## JSON 回复协议

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `LLM_JSON_ENABLED` | `true` | 启用结构化回复主链路 |
| `LLM_JSON_OUTPUT_MODE` | `auto` | `auto/response_format/prompt_only/disabled` |
| `LLM_JSON_RETRY_WITHOUT_RESPONSE_FORMAT_ON_ERROR` | `true` | 不支持 JSON mode 时降级 |
| `LLM_JSON_PARSE_LOOSE_JSON` | `true` | 允许从代码块或嵌入文本取出 JSON object |
| `LLM_JSON_MAX_RETRIES` | `1` | 空输出原样重试或非空 JSON 格式修复的次数 |
| `LLM_JSON_REGENERATE_ON_PARSE_FAILURE` | `true` | 修复失败后重生成 |
| `LLM_JSON_MAX_REGENERATE_ATTEMPTS` | `1` | 最终安全降级前的重生成次数 |
| `LLM_JSON_TEMPERATURE` | `0.35` | 角色 JSON 回复温度 |
| `LLM_JSON_MAX_TOKENS` | `6144` | 角色 JSON 回复的初始输出预算 |
| `LLM_JSON_LENGTH_RETRY_ATTEMPTS` | `2` | `finish_reason=length` 时用原始消息翻倍预算重试的次数 |
| `LLM_JSON_MAX_DYNAMIC_TOKENS` | `12288` | 动态扩大输出预算的硬上限 |

截断输出不会进入 JSON repair 上下文。运行时会丢弃它，保持原始 messages 不变并将
`max_tokens` 翻倍；只有 `finish_reason=stop` 且内容完整但无法解析时，才进行格式修复。
对于空字符串或纯空白输出，使用原始 messages 和原输出模式重试，不追加空 assistant
或格式修复指令，也不额外增加重试预算。普通角色与导演均采用这一区分。

详细协议和失败链路见 [`dialogue_protocol.md`](dialogue_protocol.md)。

## 模型请求诊断

`LLM_REQUEST_DIAGNOSTICS_ENABLED=true`（默认）为 JSON 生成链路记录 `LLM request`
和 `LLM result` 日志，不记录提示词、角色回复正文或 API Key。

请求日志包含 `purpose`、`session_id`、`turn_index`、`actor_id`、`call_id`、
`attempt`、`retry_reason`、`length_retries` 和实际传入的输出模式、温度、输出上限等参数。
未显式指定的思考模式或强度记为 `provider_default`，不推测供应商实际默认值。
`attempt` 是应用层生成/修复次数，`length_retries` 单独记录额度重试；SDK 内部的网络
重试不会分别产生这些日志。

`system_count`、`system_hash`、`messages_hash` 和 `parameter_hash` 用于比较请求变化。
`previous_call_id` 指向同会话、同用途、同角色的前一次请求；`common_prefix_messages`
和 `common_prefix_chars` 表示完整相同消息的公共前缀，`previous_messages_preserved`
表示此前的全部消息是否仍是当前消息的前缀。这里的字符数和哈希是应用层诊断，
不等于供应商编码后的 token 数或缓存键。只保留最近 128 个线程的指纹，进程重启后清空。

前缀检查仅比较当前进程内的请求，不将比较基线写入会话历史或浏览器快照，也不做
跨重启恢复校验。进程重启或线程指纹被淘汰后的首次请求没有比较基线，
`previous_call_id`、`common_prefix_messages`、`common_prefix_chars`、
`previous_messages_preserved` 和 `parameters_changed` 均为 `null`，表示尚不能比较，
不代表前缀损坏。日志中的哈希仅供诊断，不是可恢复的检查点。

结果日志通过 `call_id` 关联请求，通过上游 `request_id` 关联原有 `LLM usage` 日志，
并记录 `content_chars`、`content_empty`、`content_whitespace_only` 和结束原因。

## DeepSeek 最近用量

仅当 `ROLEPLAY_LLM_MODEL_BASE_URL` 的主机名属于 DeepSeek 官方域名时，后端启用
`GET /usage/recent?user_uuid=...`。统计直接累加 Chat Completions 响应中的
`prompt_cache_hit_tokens`、`prompt_cache_miss_tokens`、`completion_tokens` 和
`reasoning_tokens`，不会请求 `/user/balance`，也不会把 API Key 或账户余额发给前端。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `LLM_USAGE_MAX_EVENTS` | `10000` | 当前进程最多保留的模型响应记录数 |
| `LLM_USAGE_RECENT_OPERATIONS` | `6` | 前端可查看的最近回合数量 |
| `LLM_USAGE_TIMEZONE` | `Asia/Shanghai` | “今日”统计的时区 |

账本按浏览器 `user_uuid` 隔离，并且只存在于当前后端进程内存。HF Space 休眠、重启、
重新构建或切换实例后会从零开始；这是“最近用量”而不是供应商账单的永久副本。前端只在
页面初始化和一次对话/导演回合完成后读取一次本地统计，没有定时轮询 DeepSeek。

## 单角色会话

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DIALOGUE_SESSION_TTL_SECONDS` | `36000` | 空闲会话 TTL，`<=0` 禁用 |
| `DIALOGUE_SESSION_HISTORY_MAX_MESSAGES` | `0` | 消息上限，`<=0` 不裁剪 |
| `DIALOGUE_SESSION_CLEANUP_INTERVAL_SECONDS` | `60` | 后台扫描周期 |
| `DIALOGUE_HISTORY_DIRECTORY` | `./outputs/dialogues` | JSONL 目录 |
| `DIALOGUE_PREFIX_CACHE_ENABLED` | `true` | 启用可用的前缀缓存标记 |
| `DIALOGUE_PREFIX_CACHE_MIN_CHARS` | `1000` | System Prompt 标记阈值 |
| `DIALOGUE_HIDDEN_FORMAT_REINJECTION_ENABLED` | `true` | 后端隐藏格式约束再注入 |
| `DIALOGUE_HIDDEN_FORMAT_REINJECTION_INTERVAL_MESSAGES` | `100` | 每多少条 user/assistant 消息再注入 |

`DIALOGUE_SESSION_HISTORY_MAX_MESSAGES=0` 有利于保留稳定前缀，但长期会话会增加上下文
成本，应根据供应商上下文上限调整。
格式提醒附在达到间隔后的第一条 user 消息上，之后始终保留在同一条历史消息中。
不会增加中途 system 消息，也不会将提醒移到每轮最新消息上。

## 导演模式

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DIRECTOR_MAX_PARTICIPANTS` | `3` | 场景最多参加角色数 |
| `DIRECTOR_MAX_SPEAKERS_PER_TURN` | `2` | 每轮最多顺序回应角色数 |
| `DIRECTOR_LLM_TEMPERATURE` | `0.2` | 导演计划温度 |
| `DIRECTOR_LLM_MAX_TOKENS` | `6144` | 导演计划 JSON 的初始输出预算 |
| `DIRECTOR_JSON_REPAIR_ATTEMPTS` | `1` | 导演计划 JSON 修复次数 |
| `DIRECTOR_ROLE_REINJECTION_INTERVAL_REPLIES` | `25` | 按导演/单角色自己的回复数再注入 |
| `DIRECTOR_SESSION_TTL_SECONDS` | `3600` | 内存场景 TTL |
| `DIRECTOR_MAX_STAGE_ACTIONS_PER_TURN` | `4` | Stage Director 每轮最多返回的舞台动作数 |
| `STAGE_DIRECTOR_LLM_THINKING_MODE` | `disabled` | 仅控制 `/stage/*` Director 的思考模式；`disabled` 可降低动作规划延迟，不影响普通 `/director/*` |
| `SCENE_TEMPLATES_DIRECTORY` | `./scenes` | 场景预设目录 |
| `DIRECTOR_HISTORY_DIRECTORY` | `./outputs/director` | 导演 JSONL 副本目录 |

详细上下文和缓存边界见 [`director_mode_v1.md`](director_mode_v1.md)。
普通导演及场景角色的周期提醒写入当轮 user 消息的 `backend_reminder` 字段，
初始 system 保持固定。完整后端历史恢复会重建同样的消息分组；浏览器快照仍允许用
公开剧情重建可继续的上下文，不要求复原已丢失的内部导演计划或保留旧供应商缓存。
消息精确重建以提示词配置和角色卡未改变为前提。后端历史同时保留通过校验且未被
修改的原始 JSON 回复；浏览器公开快照不包含该内部字段。每个普通导演/场景角色
线程首次接收完整场景状态，后续只追加变化字段，不变时省略；所有历史仍完整保留。

## API 保护与限流

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `API_ACCESS_KEY` | 空 | 可选 `X-API-Key` 软门槛 |
| `API_RATE_LIMIT_ENABLED` | `true` | 启用内存限流 |
| `API_RATE_LIMIT_WINDOW_SECONDS` | `60` | 时间窗口 |
| `API_RATE_LIMIT_MAX_REQUESTS` | `60` | 普通 API 每 IP 窗口上限 |
| `API_CHAT_RATE_LIMIT_MAX_REQUESTS` | `12` | 对话 API 每 IP 窗口上限 |

`API_ACCESS_KEY` 会被 Pages 前端以 `VITE_API_ACCESS_KEY` 发送，因而无法对浏览器用户
保密，只适合轻量防护。

## TTS 与 Fish Speech

| 变量 | 说明 |
| --- | --- |
| `ENABLE_TTS` | 后端 TTS 总开关 |
| `TTS_MCP_URL` / `TTS_MCP_TRANSPORT` | 对话后端到项目内 TTS MCP |
| `TTS_MCP_AUTOSTART` | Docker 启动时是否自动启动 MCP |
| `TTS_MAX_CONCURRENT_JOBS` / `TTS_JOB_TTL_SECONDS` | 合成并发与临时任务 TTL |
| `TTS_TRANSLATION_LLM_*` | 中文对白到日文配音文本；留空复用 `ROLEPLAY_LLM_*` |
| `TTS_TRANSLATION_MAX_TOKENS` | 默认 `1024` |
| `TTS_TRANSLATION_CONTENT_RETRIES` | 空内容/截断重试，默认 `2` |
| `TTS_TRANSLATION_REPAIR_ATTEMPTS` | 损坏 JSON 修复，默认 `2` |
| `TTS_TRANSLATION_PREFIX_CACHE_ENABLED` | 为可用供应商标记稳定前缀 |
| `TTS_TRANSLATION_THREAD_TTL_SECONDS` / `TTS_TRANSLATION_MAX_THREADS` | 翻译线程内存治理 |
| `FISHSPEECH_BASE_URL` / `FISHSPEECH_API_KEY` | 外部 Fish Speech HTTP 服务 |
| `FISHSPEECH_TIMEOUT_SECONDS` | 慢合成超时，默认 `900` |

Fish Speech 还支持音频格式、speaker 前缀、token 上限、top-p、温度和重复惩罚等
`FISHSPEECH_*` 参数，以 `.env.template` 为权威列表。完整链路见
[`tts_pipeline.md`](tts_pipeline.md)。

## 前端

`frontend/.env`：

| 变量 | 本地默认 | 说明 |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `http://127.0.0.1:1111` | FastAPI 地址 |
| `VITE_API_ACCESS_KEY` | 空 | 与后端 `API_ACCESS_KEY` 匹配 |
| `VITE_ENABLE_TTS` | `true` | 是否显示 TTS 开关 |
| `VITE_BASE_PATH` | `/` | Vite 资源基础路径 |

GitHub Pages 构建使用 `.github/workflows/deploy-pages.yml` 中的值。所有 `VITE_*`
都是浏览器可读配置，不得存放 LLM API Key。

## 可选依赖

主链路直接使用 OpenAI 兼容 SDK，不依赖 LangChain。为以后的工具调用保留：

```bash
uv sync --extra langchain-mcp
```

如需进程内 CosyVoice 辅助模块：

```bash
uv sync --extra local-tts
```
