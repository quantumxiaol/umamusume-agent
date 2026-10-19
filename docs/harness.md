# DeepSeek Harness 插件

在 Harness 的“新会话”中选择 **赛马娘单聊**，使用自己配置的模型/API 与角色对话。角色卡、结构化回复校验、修复重试与历史压缩仍由本项目的 Python 核心执行；插件只负责模型调用、界面和持久化的适配。

所有生成请求（包括 JSON 修复和记忆压缩）都经过 Harness 当前选定的提供商。插件不会调用本项目的公开 HF 后端，不读取项目 `.env`，也不要求填写第二份 API Key。Python 通过私有标准输入/输出请求 Harness 调用模型，本地无需启动 FastAPI 或前端服务器。

## 运行要求

- DeepSeek Harness **0.2.1-rc.2**（已验证版本）；manifest 允许兼容的 0.2.x，后续版本需重新验证 API。
- 已在 Harness 中配置可用的模型与自己的 API。模型需要支持 system/user/assistant 文本消息、流式调用，并能按提示返回 JSON。
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)。首次对话会准备 Python 3.12 和锁定的依赖，之后复用缓存。`harness/requirements.txt` 由仓库 `uv.lock` 自动导出，包含基础依赖的精确版本与哈希；不安装可选的 TTS/LangChain extras。也可以指定已有 Python，见下文。
- 使用 CLI 安装时需要 Git、pnpm，以及 Harness CLI 的运行环境（本项目使用 Node.js 24 和 pnpm 11.20.0 验证）。Node.js 不会自动提供 pnpm；`dsh plugin add` 需要能在 PATH 中找到它。Harness 自动安装包依赖，用户无需手工编译 TypeScript，不下载音频模型。

## 本地测试

Desktop 用户可以直接在左侧 **插件 → 安装第三方插件** 中填入本仓库的绝对路径，安装完成后点 **立即启用**。例如本机开发目录：

```text
/Users/quantumxiaol/Desktop/dev/umamusume-agent
```

若界面提示重启，重启 Desktop 后新建“赛马娘单聊”会话。这种安装方式直接沿用桌面实例已配置的模型。其他机器请换成自己的目录。

如果已有正在使用的 Harness profile，安装到同一个 profile，然后重启对应 Harness 实例：

```sh
# 将 my-profile 换成实际使用的 profile 名
dsh plugin --profile my-profile add /absolute/path/to/umamusume-agent
```

也可以先建立独立的 Web profile 体验同一套 Harness 界面。以下第一条仅用于**首次创建**，不要用它重置已有 profile：

```sh
dsh --profile uma --from-default-profile web --dump-config
dsh plugin --profile uma add /absolute/path/to/umamusume-agent
dsh --profile uma
```

新 profile 需要配置自己的模型；安装插件不会复制其他 profile 的 API 配置。浏览器打开终端输出的本地地址即可。源码版 Harness 将 `dsh` 替换为 `pnpm dsh`。

macOS 已安装 Desktop 但终端找不到 `dsh` 时，可在当前终端定义：

```sh
dsh() { "/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh" "$@"; }
```

插件安装的是所选 profile，不会自动修改另一个正在运行的桌面 profile。确认启动时的 profile 与安装时一致。

## 从 Git 仓库安装

将本次新增文件提交并推送到 GitHub 后，用户可以直接安装包含这些文件的 commit：

```sh
dsh plugin --profile my-profile add 'github:quantumxiaol/umamusume-agent#<commit-sha>'
```

也可以用分支名替换 `<commit-sha>`。尚未推送的本地修改不会出现在 Git 安装中；本地验收请用上节的目录安装。

Desktop 的“安装第三方插件”输入框也接受同一个 `github:quantumxiaol/umamusume-agent#<commit-sha>` 地址，安装后点“立即启用”。

根目录 `package.json` 声明了 `dsh.bundle`。开发源码位于 `harness/src/`，服务端使用严格 TypeScript，界面使用 TSX；`harness/*.js` 是随仓库提交的构建产物。Git 安装直接加载这些文件，没有 `prepare` 或其他安装构建脚本。CI 检查产物与源码一致，不能只上传 artifact 而遗漏 Git 中的 JS。无需 npm 发布，也无需为构建授权。`files` 白名单只打包 Python 源码、角色 JSON 和插件运行文件；Git 克隆阶段若不需要音频，可设置 `GIT_LFS_SKIP_SMUDGE=1`。

交付 tarball 也支持：

```sh
mkdir -p dist
npm run typecheck
npm run build
npm run check:package
npm pack --ignore-scripts --pack-destination dist
dsh plugin --profile my-profile add ./dist/dsh-umamusume-agent-0.1.2.tgz
```

卸载：`dsh plugin --profile my-profile remove dsh-umamusume-agent`，然后重启。

### 更新到 0.1.1

0.1.1 修复了前端调用命令时出现的 `cannot get property "remote" without inject`。如果通过本地目录安装，更新该目录的源码后完全退出并重新打开 Harness；如果通过 Git 或 tarball 安装，重新安装包含修复的 commit 或 `dsh-umamusume-agent-0.1.1.tgz`，然后重启。旧的安装副本不会自动包含本地代码修改。

### 更新到 0.1.2

0.1.2 将接入层迁至 TS/TSX，增加跨进程消息和状态校验，并将 Python 依赖统一为 `uv.lock` 的导出结果。原有角色核心与会话格式保持兼容。首次使用此版本可能需要准备锁文件指定的依赖；输入框上方显示准备状态，完成过程与轨迹显示“检查所选模型 / 准备本地 Python 环境 / 载入角色与历史记忆 / 角色回复”的分段耗时。Python 阶段包含 uv 环境准备、进程启动及核心模块导入，不能把这一段全部视作网络耗时。

## 使用流程

1. 新建会话，把“标准模式”切换为“赛马娘单聊”。已有标准模式聊天不转换为角色会话。
2. 在输入框上方选择角色；在 Harness 原生模型选择器中选自己的模型。
3. 选择“训练员对白 / 训练员动作 / 环境事件”，使用原生输入框发送。
4. 多个事件可先加入“事件队列”，再发送最后一句；整组触发一次角色回复。每次输入合计最多 10,000 字符。
5. 点击 Harness 原生停止按钮取消。已完成的历史和摘要保留，取消的轮次不进入角色记忆。
6. “重生成上一轮”重新执行上一次成功输入，成功后替换角色记忆中的最后一轮。Harness 审计记录仍保留旧回复，并追加新版；排队的新事件保留给下一次正常发送。
7. 关闭并重新打开同一会话可以继续；角色、输入类型、队列、成功历史和记忆摘要从 Harness 日志恢复。

开始对话后更换角色需新建会话。当前控件仅在角色预设中出现，普通 Harness 对话不走 Python 角色服务。

回复会经过完整 `action/dialogue` JSON 校验后显示，所以当前版本不是逐字显示角色对白。上游仍使用流式 API，支持取消、截断检测与压缩摘要流；生成期间显示调用状态，避免把不完整 JSON 当作角色台词。

“对话”的完成过程可展开查看每次生成、修复或压缩的用量；“轨迹”中有“角色回复 / 历史记忆压缩”记录，包含核心实际构造的请求。主循环的通用请求视图不代表 Python 内层调用的角色提示词。总用量汇总所有成功完成的内层请求；缓存输入只计算一次，推理 token 已包含在输出中。上游未返回的用量无法补算，取消或异常中断时的实际计费以提供商为准。

## 可选配置

在所选 profile 的 `cordis.patch.yml` 中添加或更新这条配置，然后重启：

```yaml
- id: umamusume-runtime
  config:
    # 可选：已有 Python 3.12，且安装了 harness/requirements.txt
    pythonExecutable: /absolute/path/to/python
    # 留空 pythonExecutable 时使用 uv，也可指定 uvExecutable 的绝对路径
    defaultCharacter: rice_shower
    contextWindow: 128000
    maxOutputTokens: 6144
    turnTimeoutMs: 1800000
```

`pythonExecutable` 默认留空，自动使用 `~/.local/bin/uv` 或 PATH 中的 `uv`。`charactersDirectory` 可指向自定义角色库，默认使用插件内附的 `characters/`。上下文容量取配置值与 Harness 提供商报告值中的较小值；提供商未报告时使用配置值，请按真实模型容量设置。输出预算和压缩水位随容量调整，JSON/长度修复沿用核心重试规则。配置覆盖遵循 Harness 的整条 `config` 替换语义。

本仓库开发者可将 `pythonExecutable` 设为仓库 `.venv/bin/python`，免去首次下载。Windows 使用实际的 `python.exe` 路径。

## 当前边界与验收

本版提供单角色文字对话、输入类型与事件队列、重生成、长期记忆、停止、恢复、原生用量和调用轨迹。多角色导演/Stage、TTS、附件与网站历史导入导出尚未接入；原网站仍照常使用。

建议验收：切换角色并聊天 → 加入环境事件 → 停止一轮 → 继续 → 重生成 → 重启 Harness 后继续。真实供应商的 JSON 遵从性、缓存报告与错误格式还需用自己的模型验证。

开发与 CI（Node 24，pnpm 11.20.0，Python 3.12，uv 0.9.8；不调用付费 API）：

```sh
# 若尚未安装 pnpm：npm install --global pnpm@11.20.0
node --version
pnpm --version
npm ci --ignore-scripts
npm run check:python-lock
npm run typecheck
npm run build
npm run check:build
# Python 已准备好时；可使用 uv sync --locked --no-dev 建立的环境
UMA_TEST_PYTHON=/absolute/path/to/python npm test
npm run check:package
# 验证用户默认的 uv 启动路径，首次可能下载依赖
npm run test:runtime
# 打包 → 安装到临时 profile → 离线对话/取消/恢复 → 退出并清理
UMA_TEST_PYTHON=/absolute/path/to/python npm run test:package
```

修改 TS/TSX 后运行 `npm run build`，同时提交源码与 `harness/*.js`。修改 Python 依赖时更新根目录 `uv.lock`，再执行 `npm run python:export`；不要手改 `harness/requirements.txt`。导出使用 `uv export --locked --no-dev --no-emit-project`，不更新锁文件。插件默认以这份导出文件准备隔离环境，不依赖安装目录下可写的 `.venv`。自定义 `pythonExecutable` 则由部署者负责按同一依赖清单准备环境。

未指定 `UMA_TEST_PYTHON` 时，单元测试查找仓库 `.venv/bin/python`；不存在则跳过 Python 集成项。CI 显式准备锁定环境，不跳过这些测试。测试覆盖前端依赖声明与控件 RPC、JSON 修复、长度截断、缺失终止标记、流式摘要、缓存计数、取消、记忆恢复、重生成、协议校验与启动阶段。`harness/tests/mock-provider.js` 与 `host-smoke.js` 仅用于隔离 `DSH_HOME` 中的宿主集成测试，不进入安装包，不会覆盖用户模型。真实宿主测试当前在 Linux CI 运行，开发机已验证 macOS；Windows 未完成验收。

`.github/workflows/check-harness.yml` 显式准备 Node 24 和 pnpm 11.20.0，再用 npm 安装 `package-lock.json` 锁定的工具与官方 Harness 类型，检查 Python 导出、执行 `tsc`、检查已提交 JS、构建、测试，再验装和上传 `.tgz`。仓库依赖仍由 npm 管理；pnpm 专供 Harness 的插件安装流程使用，`test:package` 会在打包前检查它并打印版本。类型检查与构建分开，转译成功不代表类型检查通过。Git 安装不需要开发依赖或这套构建过程。

实现入口：`harness/src/index.ts`（服务与会话事务）、`model.ts`（模型协议）、`worker.ts`（子进程与取消）、`client.tsx`（原生界面）、`protocol.ts`（共享类型及运行时校验）；Python 接入层仍为 `src/umamusume_agent/harness/worker.py`。请编辑源码，不要直接修改生成的 JS。

Harness 接口参考：[第一个插件](https://deepseek-harness.github.io/deepseek-harness/develop/basic/)、[打包与安装](https://deepseek-harness.github.io/deepseek-harness/develop/basic/publish)。
