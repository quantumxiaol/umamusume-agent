# GitHub Pages + Hugging Face Space 部署

本项目的生产形态分为两部分：

- GitHub Pages 发布 `frontend/` 静态前端。
- Hugging Face Docker Space 通过根目录 `app.py`、`Dockerfile` 和
  `docker-entrypoint.sh` 运行 FastAPI 后端。

当前生产配置不启用 TTS：

```text
GitHub Pages: VITE_ENABLE_TTS=false
Hugging Face: ENABLE_TTS=false
```

后端依赖外部 OpenAI 兼容 LLM API，所以 HF Space 本身只需 CPU 运行 API
和会话服务；不在 Space 内运行角色大模型。

## 当前项目地址

- GitHub：[quantumxiaol/umamusume-agent](https://github.com/quantumxiaol/umamusume-agent)
- Hugging Face：[quantumxiaol/umamusume-agent](https://huggingface.co/spaces/quantumxiaol/umamusume-agent)
- GitHub Pages：`https://quantumxiaol.github.io/umamusume-agent/`
- HF API：`https://quantumxiaol-umamusume-agent.hf.space`

Pages 工作流在 `.github/workflows/deploy-pages.yml`。当 `frontend/**` 或工作流本身
推送到 `main` 时自动发布，也可以在 GitHub Actions 中手动运行。

## Fork 后自部署

下文假设部署者在 GitHub 和 Hugging Face 都使用 `username`，并分别拥有：

- GitHub Fork：`username/umamusume-agent`
- HF Space：`username/umamusume-agent`
- Pages 预期地址：`https://username.github.io/umamusume-agent/`
- HF API 通常为：`https://username-umamusume-agent.hf.space`

HF 子域名可能经过规范化，应以 Space 页面实际显示的地址为准。

首次部署顺序：

1. 准备 LLM API。
2. 复制并配置 HF 后端。
3. 确认 HF API 可用。
4. 用真实 HF API 地址构建 GitHub Pages。

## 1. 购买或开通 LLM API

项目需要支持 OpenAI Chat Completions 协议的模型服务。API 额度与普通
聊天网页会员是两套计费体系。价格、赠送额度和模型 ID 会变化，以厂商
官方控制台为准。

### 阿里云百炼 / Qwen（推荐）

1. 注册阿里云账号并开通大模型服务平台百炼。
2. 领取试用额度或充值。
3. 按[阿里云官方文档](https://help.aliyun.com/zh/model-studio/get-api-key)
   创建 API Key。
4. 确认 Key 所在地域的 OpenAI 兼容 Base URL 和可用模型 ID。

中国内地常见示例：

```text
ROLEPLAY_LLM_MODEL_NAME=qwen-plus
ROLEPLAY_LLM_MODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
ROLEPLAY_LLM_MODEL_API_KEY=sk-xxxxxxxx
```

API Key 和 Base URL 必须属于同一地域，其他地域不应直接照抄示例地址。

### DeepSeek 官方 API

1. 登录 [DeepSeek 开放平台](https://platform.deepseek.com/)。
2. 充值并创建 API Key。
3. 从 [DeepSeek 官方快速开始文档](https://api-docs.deepseek.com/zh-cn/)
   复制当前有效的对话模型 ID。

```text
ROLEPLAY_LLM_MODEL_NAME=<官方文档当前的对话模型 ID>
ROLEPLAY_LLM_MODEL_BASE_URL=https://api.deepseek.com
ROLEPLAY_LLM_MODEL_API_KEY=sk-xxxxxxxx
```

`LLM_JSON_OUTPUT_MODE=auto` 会在供应商不支持 `response_format=json_object` 时
自动降级为 prompt-only JSON。

## 2. 部署 Hugging Face 后端

### 创建 Space

建议从原 Space 选择 **Duplicate this Space**，所有者选 `username`，并保留：

- SDK：Docker
- App port：`7860`
- Visibility：Public，或其他允许应用被公开 Pages 访问的类型

如果新建 Space，HF 仓库 `README.md` 顶部应保留：

```yaml
---
title: Umamusume Agent
sdk: docker
app_port: 7860
---
```

### Variables 与 Secrets

打开 `Space -> Settings -> Variables and secrets`。HF 会把 Variables 和 Secrets
注入 Docker 运行时环境。Variables 适合非敏感配置，Secrets 适合 API Key。
复制 Space 时原作者的 Secrets 不会被复制，必须自己重新填写。参见
[HF Spaces 官方说明](https://huggingface.co/docs/hub/spaces-overview#managing-secrets-and-environment-variables)。

| HF 位置 | 名称 | 值 | 是否必需 |
| --- | --- | --- | --- |
| Variable | `ROLEPLAY_LLM_MODEL_NAME` | 选择的模型 ID | 是 |
| Variable | `ROLEPLAY_LLM_MODEL_BASE_URL` | 厂商官方 OpenAI 兼容 Base URL | 是 |
| Secret | `ROLEPLAY_LLM_MODEL_API_KEY` | 真实 LLM API Key | 是 |
| Variable | `ENABLE_TTS` | `false` | 建议显式配置 |
| Secret | `API_ACCESS_KEY` | 自定义随机字符串 | 可选 |

其他参数使用 `.env.template` 默认值。完整配置说明见
[`configuration.md`](configuration.md)。

当前生产方案不启用 TTS，不要在 HF 填写本地
`127.0.0.1:8002` Fish Speech 地址。

### 验证后端

```bash
curl https://username-umamusume-agent.hf.space/capabilities
curl https://username-umamusume-agent.hf.space/characters
```

如果设置了 `API_ACCESS_KEY`：

```bash
curl -H "X-API-Key: <YOUR_API_ACCESS_KEY>" \
  https://username-umamusume-agent.hf.space/capabilities
```

HF 免费硬件可能休眠，首次请求需要等待唤醒。Space 内存和本地文件也不是
永久存储；导演模式使用当前浏览器的公开场景快照完成灾难恢复。

## 3. 部署 GitHub Pages 前端

1. 在 GitHub Fork 中打开 `Settings -> Pages`，Source 选 **GitHub Actions**。
2. Fork 如果停用 Actions，先在 `Actions` 页启用。
3. 编辑 `.github/workflows/deploy-pages.yml` 的 `Build frontend -> env`：

```yaml
VITE_API_BASE_URL: https://username-umamusume-agent.hf.space
VITE_API_ACCESS_KEY: ${{ vars.VITE_API_ACCESS_KEY }}
VITE_ENABLE_TTS: "false"
VITE_BASE_PATH: /umamusume-agent/
```

`VITE_BASE_PATH` 必须使用真实 GitHub 仓库名。如果仓库改名为 `my-uma`，则：

```text
VITE_BASE_PATH=/my-uma/
Pages=https://username.github.io/my-uma/
```

### 可选 API 软门槛

默认不需要 GitHub Secret。如果 HF 后端设置了 `API_ACCESS_KEY`，进入
`Settings -> Secrets and variables -> Actions -> Variables` 新增：

```text
Name:  VITE_API_ACCESS_KEY
Value: 与 HF Secret API_ACCESS_KEY 完全相同
```

GitHub Pages 是静态前端，所有 `VITE_*` 值都会进入浏览器可读的 JavaScript。
`VITE_API_ACCESS_KEY` 只是防止无意请求的软门槛，不是真正认证。

**绝对不要把 `ROLEPLAY_LLM_MODEL_API_KEY` 放入 GitHub Pages Secret、Variable、
`.env` 或任何 `VITE_*` 变量。**

提交后进入 `Actions -> Deploy Frontend to GitHub Pages -> Run workflow`。以后
`frontend/**` 推送到 `main` 会自动重新发布。GitHub Actions Variables 的说明见
[GitHub 官方文档](https://docs.github.com/actions/concepts/workflows-and-actions/variables)。

## 4. GitHub 与 HF 代码同步

两个仓库是独立部署：

- 推送 GitHub `main` 只会更新 Pages，不会更新 HF。
- 后端代码推送到 HF Space 仓库后，Space 才会重建。
- 前后端协议同时改动时，两边都要发布。

可以给本地 GitHub 克隆增加 HF remote：

```bash
git remote add hf https://huggingface.co/spaces/username/umamusume-agent
git push origin main
git push hf main
```

推送 HF 时使用具有 Space 写权限的 Hugging Face User Access Token。不要将 Token
写入 remote URL 或提交到仓库。同时应确保 HF 分支的 `README.md` 仍保留
`sdk: docker` 和 `app_port: 7860` 元数据，不要在两边历史分叉时盲目强制推送。

当前项目没有 GitHub → HF 自动同步工作流，默认不需要 GitHub `HF_TOKEN`
Secret。只有自己增加同步 Action 时，才应把 HF Token 保存为 GitHub Actions Secret。

## 发布先后顺序

- 首次部署：先 HF，再 Pages，因为 Pages 构建需要真实 HF URL。
- 已有环境进行向后兼容升级：可以先 Pages，再 HF。
- 不兼容协议变更：应使用临时环境验证并协调发布，不要假设两个平台能原子更新。

## 常见错误

- `Missing required environment variable`：HF 三个 `ROLEPLAY_LLM_*` 配置没有填全。
- LLM `401` / `invalid api key`：Key 错误，或 Base URL 与 Key 地域不一致。
- Pages 仍请求原作者 HF：`VITE_API_BASE_URL` 未改，或 Pages Action 没有重新运行。
- Pages 空白或资源 `404`：`VITE_BASE_PATH` 与 GitHub 仓库名不一致。
- Pages API `401`：HF `API_ACCESS_KEY` 与 GitHub `VITE_API_ACCESS_KEY` 不一致。
- 首次请求超时、稍后恢复：HF 免费 Space 可能正在唤醒。

## 生产 TTS

当前 GitHub Pages 和 HF 都显式禁用 TTS。如果未来在生产开启，至少还需要：

- `ENABLE_TTS=true`
- HF 容器能通过 HTTP/HTTPS 访问的 `FISHSPEECH_BASE_URL`
- 可选 `FISHSPEECH_API_KEY`
- 如不复用角色对话模型，配置 `TTS_TRANSLATION_LLM_*`
- GitHub Pages 构建改为 `VITE_ENABLE_TTS=true`

详细链路和安全边界见 [`tts_pipeline.md`](tts_pipeline.md)。
