# 对话与历史协议

本文档记录单角色页面的剧情事件、结构化回复、SSE 和历史兼容行为。
多角色场景见 [`director_mode_v1.md`](director_mode_v1.md)，系统依赖边界见
[`dialogue_architecture.md`](dialogue_architecture.md)。

## 能力协商

`GET /capabilities` 用于 GitHub Pages 前端与 HF 后端分开升级时协商能力。

- `dialogue_events >= 1`：支持说话者和剧情事件类型。
- `context_event_batch >= 1`：支持“加入”多条事件后只生成一次回复。
- `director_mode >= 1`：支持多角色导演页面。
- `tts_jobs >= 1`：后端启用异步 TTS 任务。

旧后端没有 `/capabilities` 时，前端保留基本单角色对话。

## 剧情事件 v1

原有 `/chat` 和 `/chat_stream` 端点保持不变。剧情请求可选增加：

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

当前单角色页面提供：

- `dialogue`：训练员对白。
- `action`：训练员动作。
- `scene_event`：时间、天气、地点等环境变化。

“加入”只把事件放入浏览器待发送列表，不请求 LLM。“发送”将前面事件作为
`context_events` 按顺序提交，最后一条作为本轮 `message`，整组只生成一次
角色回复。

事件会把 `actor`、`event_type`、`target_actor_ids` 和 `event_schema_version`
写入 JSONL、浏览器缓存和导出数据。LLM 上下文使用 `【训练员对白】`、
`【训练员动作】`和 `【环境变化】` 等明确标签。

## JSON 回复 v2

角色模型默认被要求只输出：

```json
{
  "action": "角色自己的动作、神态或心理；没有则写无",
  "dialogue": "角色自己说出的对白"
}
```

`POST /chat` 返回：

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

关闭 JSON 主链路（`LLM_JSON_OUTPUT_MODE=disabled`）后会退回旧“动作：/对白：”两行
文本协议，仅用于兼容和调试。

## SSE 流式行为

JSON 模式不会将半截 JSON token 直接发给前端。后端等待完整内容，解析、
校验后发送：

```text
event: structured_reply
data: {"action":"...","dialogue":"...","message":{...}}

event: done
data: {}
```

`disabled` 模式保留旧 token stream。

## `response_format` 能力降级

`LLM_JSON_OUTPUT_MODE=auto` 优先尝试：

```json
{"type":"json_object"}
```

如果上游明确返回 `response_format` / `json_object` unsupported、unknown 或
unrecognized 类 400/422 错误，本轮自动重试 prompt-only JSON，并在当前进程记住
该 Base URL + model 不支持 JSON mode。

API Key、模型名和 Base URL 等普通错误不会被当成能力降级。

## 解析失败与重生成

默认失败链路：

1. 正常 JSON 请求。
2. 解析失败后进行有界 prompt-only JSON 修复。
3. 修复仍失败时，移除失败输出，基于最近训练员事件重生成。
4. 仍无可用 `dialogue` 才返回角色中性安全提示：
   `抱歉，刚才有点没听清，可以再说一次吗？`

安全提示以 `source_format=parse_error` 写入历史，不包含任何角色名、角色自称或
特定训练员称呼。

`parse_error` 不会提交 TTS。在导演模式中，还会停止本轮剩余角色回应，
避免其他角色把安全提示当作真实剧情继续接话。

单角色页面支持重生成上一轮/编辑上一句。导演模式支持重生成最后一条
角色回复，详细 revision 行为见 [`director_mode_v1.md`](director_mode_v1.md)。

## 历史与导入导出

- Assistant 记录保存 `schema_version=2`、`content`、`action`、`dialogue` 和
  `source_format`。
- LLM 历史不直接堆叠 raw JSON，而是压缩成稳定自然语言标签。
- `/history/import` 可导入 v2 JSON，也兼容旧 `role/content`、旧两行文本和项目
  Markdown 导出。
- `replace_current=true` 且 `messages=[]` 会清空当前 session 上下文。
- JSON 是权威恢复格式；Markdown 主要供人阅读，末尾附带 v2 JSON block。

浏览器按自己生成的 `user_uuid` 隔离对话和场景。这是浏览器实例隔离，
不是账号认证。

## TTS 边界

只有校验成功、当前新生成的角色 `dialogue` 能提交 TTS。训练员输入、环境事件、
旁白、动作字段、`parse_error` 和 TTS 关闭期间的旧回复都不会被补合成。

详细翻译、MCP、Fish Speech、异步任务和缓存边界见
[`tts_pipeline.md`](tts_pipeline.md)。
