// Generated from harness/src by npm run build. Do not edit.
import { Service } from "@deepseek-ai/cordis";
import Schema from "@deepseek-ai/schemastery";
import { z } from "zod";
import { createUserMessage, isAgentLoopRequest } from "@deepseek-ai/dsh-llm";
import { appendPluginRecord } from "@deepseek-ai/dsh-session";
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { QueueItem, Mode, errorMessage } from "./protocol.js";
import { initialState, reduceState, stateSchema, viewSchema, publicState, PRESET, STATE_KEY, replyText, sumUsage } from "./state.js";
import { executeModelCall } from "./model.js";
import { runWorker } from "./worker.js";
function catalogAt(directory) {
  return readdirSync(directory, { withFileTypes: true }).filter((entry) => entry.isDirectory()).flatMap((entry) => {
    try {
      const data = z.object({ name_zh: z.string().optional(), name_en: z.string().optional() }).parse(JSON.parse(readFileSync(join(directory, entry.name, "config.json"), "utf8")));
      return [{ id: entry.name, name: data.name_zh || entry.name, sort: data.name_en || entry.name }];
    } catch (error) {
      if (error instanceof Error && "code" in error && error.code === "ENOENT") return [];
      throw error;
    }
  }).sort((a, b) => a.sort.toLowerCase().localeCompare(b.sort.toLowerCase())).map(({ id, name }) => ({ id, name }));
}
function inputText(message) {
  if (message.content.some((block) => block.type !== "text")) throw new Error("赛马娘单聊第一版仅支持文字，请移除附件。");
  return message.content.filter((block) => block.type === "text").map((block) => block.text).join("\n");
}
class Umamusume extends Service {
  static inject = ["llm", "agents", "sessionProjections"];
  static Config = Schema.object({
    pythonExecutable: Schema.string().default("").description("Existing Python 3.12 with harness/requirements.txt installed; otherwise use uv."),
    uvExecutable: Schema.string().default(""),
    charactersDirectory: Schema.string().default(""),
    defaultCharacter: Schema.string().default("rice_shower"),
    contextWindow: Schema.number().min(8192).max(2e6).default(128e3),
    maxOutputTokens: Schema.number().min(256).max(32768).default(6144),
    turnTimeoutMs: Schema.number().min(1e4).max(72e5).default(18e5)
  });
  config;
  catalog;
  pending = /* @__PURE__ */ new Map();
  active = /* @__PURE__ */ new Set();
  constructor(ctx, config) {
    super(ctx, "umamusume");
    this.config = config;
    this.catalog = catalogAt(config.charactersDirectory || fileURLToPath(new URL("../characters/", import.meta.url)));
    if (!this.catalog.some((row) => row.id === config.defaultCharacter)) throw new Error(`Unknown default character: ${config.defaultCharacter}`);
    ctx.sessionProjections.register({
      key: STATE_KEY,
      stateVersion: 1,
      stateSchema,
      init: (header) => ({ ...initialState(header.agentPreset === PRESET), character: config.defaultCharacter }),
      apply: reduceState,
      wire: { viewSchema, view: (state) => publicState(state, this.catalog) }
    });
    ctx.on("agent/pre-step", async (payload, next) => {
      if (!this.state(payload.agent).enabled) return next();
      const messages = payload.messages.filter((message) => message.role === "user" && message.source.kind === "user");
      if (!messages.length) throw new Error("赛马娘单聊需要用户文字输入。");
      const texts = messages.map(inputText);
      const state = this.state(payload.agent);
      const regenerate = texts.length === 1 && texts[0] === "/uma retry";
      if (regenerate && !state.lastInput) throw new Error("没有可以重新生成的轮次。");
      this.pending.set(payload.agent.id, {
        turn: payload.turn,
        step: payload.step,
        regenerate,
        input: {
          message: texts.at(-1),
          mode: state.mode,
          queue: [...state.queue, ...texts.slice(0, -1).map((content) => ({ content, event_type: state.mode }))]
        }
      });
      return next();
    });
    ctx.on("llm/stream", (request, next) => {
      if (!isAgentLoopRequest(request) || !request.sessionId) return next();
      const agent = ctx.agents.get(request.sessionId);
      if (!agent || !this.state(agent).enabled) return next();
      return this.streamTurn(agent, request);
    });
    ctx.on("agent/disposed", ({ agent }) => this.pending.delete(agent.id));
    ctx.effect(() => () => {
      for (const controller of this.active) controller.abort(new Error("Umamusume plugin unloaded"));
      this.pending.clear();
    });
  }
  state(agent) {
    const state = this.ctx.sessionProjections.stateOf(agent.session, STATE_KEY);
    if (!state) throw new Error("Umamusume session projection unavailable");
    return state;
  }
  record(agent, type, data) {
    return appendPluginRecord(agent.session, type, data);
  }
  configure({ agent, rawInput }) {
    const state = this.state(agent);
    if (!state.enabled) return { kind: "error", text: "请新建会话并选择“赛马娘单聊”。" };
    if (agent.status !== "idle") return { kind: "error", text: "请先停止或等待当前回复完成。" };
    const [, verb = "", argument = ""] = /^(\S+)(?:\s+([\s\S]*))?$/.exec(rawInput.trim()) ?? [];
    let patch, text;
    if (verb === "character") {
      if (state.history.length) return { kind: "error", text: "本会话已开始；更换角色请新建会话。" };
      const row = this.catalog.find((item) => item.id === argument || item.name === argument);
      if (!row) return { kind: "error", text: "角色不存在，请从角色列表选择。" };
      patch = { character: row.id };
      text = `已选择 ${row.name}`;
    } else if (verb === "mode") {
      const mode = Mode.safeParse(argument);
      if (!mode.success) return { kind: "error", text: "输入类型必须为 dialogue、action 或 scene_event。" };
      patch = { mode: mode.data };
      text = "输入类型已更新";
    } else if (verb === "queue") {
      let item;
      try {
        item = QueueItem.parse(JSON.parse(argument));
      } catch {
        return { kind: "error", text: "队列格式错误。" };
      }
      if (!item.content.trim() || state.queue.length >= 20 || [...state.queue, item].reduce((n, v) => n + v.content.length, 0) > 1e4) {
        return { kind: "error", text: "队列最多 20 条、合计 10,000 字符，内容不能为空。" };
      }
      const speaker = item.event_type === "scene_event" ? { actor_id: "environment", actor_type: "narrator", display_name: "环境" } : { actor_id: "player", actor_type: "trainer", display_name: "训练员" };
      patch = { queue: [...state.queue, { content: item.content, event_type: item.event_type, speaker }] };
      text = "事件已加入队列；下一次发送时一起交给角色。";
    } else if (verb === "clear") {
      patch = { queue: [] };
      text = "事件队列已清空";
    } else if (verb === "retry") {
      if (!state.lastInput) return { kind: "error", text: "没有可以重新生成的轮次。" };
      agent.followup(createUserMessage({ content: [{ type: "text", text: "/uma retry" }], source: { kind: "user" } }));
      return { kind: "success", text: "正在重新生成；原回复保留在 Harness 记录中，角色记忆将在成功后更新。" };
    } else return { kind: "success", text: "/uma character rice_shower · /uma mode dialogue|action|scene_event · /uma retry · /uma clear" };
    this.record(agent, "plugin:umamusume/configure", { version: 1, patch });
    return { kind: "success", text };
  }
  async *streamTurn(agent, route) {
    const pending = this.pending.get(agent.id);
    if (!pending) throw new Error("Roleplay input was not admitted; please resend.");
    const state = this.state(agent);
    const controller = new AbortController();
    this.active.add(controller);
    const signal = AbortSignal.any([...route.signal ? [route.signal] : [], controller.signal, AbortSignal.timeout(this.config.turnTimeoutMs)]);
    let usage;
    let callNumber = 0;
    try {
      const modelInfoStarted = Date.now();
      this.record(agent, "plugin:umamusume/progress", { version: 1, ...pendingPosition(pending), stage: "model-info", phase: "start", label: "检查所选模型" });
      const info = await this.ctx.llm.resolveModelInfo(route.provider, route.model, signal);
      this.record(agent, "plugin:umamusume/progress", { version: 1, ...pendingPosition(pending), stage: "model-info", phase: "end", label: "检查所选模型", durationMs: Date.now() - modelInfoStarted });
      const contextWindow = Math.min(this.config.contextWindow, info.context?.contextWindow ?? this.config.contextWindow);
      const result = await runWorker({
        config: this.config,
        signal,
        input: {
          sessionId: agent.id,
          state: { ...state, retry: pending.regenerate, prepared: null },
          input: pending.input,
          options: { model: route.model, contextWindow, maxOutputTokens: this.config.maxOutputTokens }
        },
        onProgress: (data) => {
          this.record(agent, "plugin:umamusume/progress", { ...data, ...pendingPosition(pending), version: 1 });
        },
        onModel: async (call, onChunk) => {
          const id = randomUUID(), started = Date.now();
          const label = call.stream ? "历史记忆压缩" : ++callNumber === 1 ? "角色回复" : "JSON 修复或重试";
          this.record(agent, "plugin:umamusume/call", {
            version: 1,
            ...pendingPosition(pending),
            id,
            phase: "start",
            label,
            provider: route.provider,
            model: route.model,
            request: call,
            started
          });
          try {
            const output = await executeModelCall({ llm: this.ctx.llm, call, route, signal, onChunk });
            usage = sumUsage(usage, output.usage);
            this.record(agent, "plugin:umamusume/call", {
              version: 1,
              ...pendingPosition(pending),
              id,
              phase: "end",
              label,
              durationMs: Date.now() - started,
              usage: output.usage ?? null
            });
            return output.response;
          } catch (error) {
            this.record(agent, "plugin:umamusume/call", {
              version: 1,
              ...pendingPosition(pending),
              id,
              phase: "error",
              label,
              durationMs: Date.now() - started,
              message: errorMessage(error)
            });
            throw error;
          }
        }
      });
      signal.throwIfAborted();
      if (result.baseRevision !== this.state(agent).revision) throw new Error("角色状态已变化，请重新发送。");
      this.record(agent, "plugin:umamusume/prepared", { version: 1, ...pendingPosition(pending), result });
      const text = replyText(result.reply);
      yield { type: "block-start", index: 0, blockType: "text" };
      yield { type: "text-delta", index: 0, text };
      yield { type: "block-end", index: 0, block: { type: "text", text } };
      if (usage) yield { type: "usage", usage };
      yield { type: "finish", reason: { kind: "stop" } };
    } finally {
      controller.abort(new Error("Roleplay turn finished"));
      this.active.delete(controller);
    }
  }
}
function pendingPosition(pending) {
  return { turn: pending.turn, step: pending.step };
}
export {
  Umamusume as default
};
