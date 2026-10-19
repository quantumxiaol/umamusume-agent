// Generated from harness/src by npm run build. Do not edit.
import { State, PublicState, ConfigureRecord, PreparedRecord, CallRecord, ProgressRecord, modes } from "./protocol.js";
const PRESET = "umamusume";
const STATE_KEY = "umamusume";
const RECORD = "plugin:umamusume/";
const MODES = modes;
function initialState(enabled = false) {
  return {
    version: 1,
    enabled,
    character: null,
    mode: "dialogue",
    queue: [],
    history: [],
    checkpoint: null,
    checkpoints: [],
    tokenRatio: 0.5,
    turns: 0,
    lastInput: null,
    lastReply: null,
    retry: false,
    revision: 0,
    prepared: null,
    activity: null
  };
}
function reduceState(state, event) {
  if (event.type === "agent-preset/selected") return { ...state, enabled: event.data.agentPreset === PRESET };
  if (event.type === "assistant/message" && state.prepared && event.data.turn === state.prepared.turn && event.data.step === state.prepared.step && event.data.message.content.filter((block) => block.type === "text").map((block) => block.text).join("") === replyText(state.prepared.result.reply)) {
    const data = state.prepared.result;
    if (data.baseRevision !== state.revision) throw new Error("Umamusume turn revision mismatch");
    return {
      ...state,
      history: [...state.history.slice(0, data.replaceFrom), ...data.historyAppend],
      checkpoint: data.checkpoint,
      checkpoints: data.checkpoints,
      tokenRatio: data.tokenRatio,
      queue: data.regenerate ? state.queue : [],
      retry: false,
      turns: state.turns + (data.regenerate ? 0 : 1),
      prepared: null,
      activity: null,
      lastInput: data.input,
      lastReply: data.reply,
      revision: state.revision + 1
    };
  }
  if (event.type === "turn/end" && (state.prepared || state.activity)) return { ...state, prepared: null, activity: null };
  if (!("ignorable" in event) || !event.ignorable || !event.type.startsWith(RECORD)) return state;
  if (event.type === RECORD + "call") {
    const data = CallRecord.parse(event.data);
    return { ...state, activity: { label: data.label, phase: data.phase } };
  }
  if (event.type === RECORD + "progress") {
    const data = ProgressRecord.parse(event.data);
    return { ...state, activity: { label: data.label ?? "历史记忆整理", phase: data.phase } };
  }
  if (event.type === RECORD + "configure") {
    return { ...state, ...ConfigureRecord.parse(event.data).patch, revision: state.revision + 1 };
  }
  if (event.type === RECORD + "prepared") return { ...state, prepared: PreparedRecord.parse(event.data) };
  return state;
}
function replyText(reply) {
  const escape = (value) => value.replace(/[\\`*_{}\[\]<>#|]/g, "\\$&");
  return `**${escape(reply.characterName)}**

${reply.action && reply.action !== "无" ? `*${escape(reply.action)}*

` : ""}${escape(reply.dialogue)}`;
}
const validateState = (value) => State.parse(value);
function publicState(state, catalog) {
  return {
    enabled: state.enabled,
    character: state.character,
    mode: state.mode,
    queue: state.queue,
    turns: state.turns,
    retry: state.retry,
    lastReply: state.lastReply,
    memory: state.checkpoint?.summary ?? "",
    activity: state.activity,
    catalog
  };
}
function sumUsage(total, usage) {
  if (!usage) return total;
  const next = { inputTokens: 0, outputTokens: 0, ...total };
  for (const key of ["inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens", "reasoningTokens", "totalTokens"]) {
    const value = usage[key];
    if (value !== void 0 && Number.isFinite(value) && value >= 0) next[key] = (next[key] ?? 0) + value;
  }
  if (usage.totalTokens === void 0) {
    next.totalTokens = (next.totalTokens ?? 0) + (usage.inputTokens ?? 0) + (usage.outputTokens ?? 0) + (usage.cacheReadTokens ?? 0) + (usage.cacheWriteTokens ?? 0);
  }
  return next;
}
function openAIUsage(usage) {
  if (!usage) return void 0;
  const cached = usage.cacheReadTokens ?? 0;
  const prompt = (usage.inputTokens ?? 0) + cached + (usage.cacheWriteTokens ?? 0);
  return {
    prompt_tokens: prompt,
    completion_tokens: usage.outputTokens ?? 0,
    total_tokens: usage.totalTokens ?? prompt + (usage.outputTokens ?? 0),
    prompt_tokens_details: { cached_tokens: cached },
    completion_tokens_details: { reasoning_tokens: usage.reasoningTokens ?? 0 }
  };
}
export {
  MODES,
  PRESET,
  RECORD,
  STATE_KEY,
  initialState,
  openAIUsage,
  publicState,
  reduceState,
  replyText,
  State as stateSchema,
  sumUsage,
  validateState,
  PublicState as viewSchema
};
