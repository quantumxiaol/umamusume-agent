import type { SessionEvent } from '@deepseek-ai/dsh-session';
import type {} from '@deepseek-ai/dsh-agent-preset-registry';
import type { TokenUsage } from '@deepseek-ai/dsh-llm';
import { State, PublicState, ConfigureRecord, PreparedRecord, CallRecord, ProgressRecord, modes } from './protocol.js';
import type { Reply, Catalog, OpenAIUsage } from './protocol.js';
/** Harness owns durable state; Python only proposes a completed turn. */
export const PRESET = 'umamusume';
export const STATE_KEY = 'umamusume';
export const RECORD = 'plugin:umamusume/';
export const MODES = modes;

export function initialState(enabled = false): State {
  return { version: 1, enabled, character: null, mode: 'dialogue', queue: [], history: [],
    checkpoint: null, checkpoints: [], tokenRatio: 0.5, turns: 0, lastInput: null, lastReply: null,
    retry: false, revision: 0, prepared: null, activity: null };
}

export function reduceState(state: State, event: SessionEvent): State {
  if (event.type === 'agent-preset/selected') return { ...state, enabled: event.data.agentPreset === PRESET };
  // Commit only alongside the native assistant settlement. A cancelled attempt
  // or a crash before settlement never advances the Python conversation.
  if (event.type === 'assistant/message' && state.prepared
      && event.data.turn === state.prepared.turn && event.data.step === state.prepared.step
      && event.data.message.content.filter(block => block.type === 'text').map(block => block.text).join('') === replyText(state.prepared.result.reply)) {
    const data = state.prepared.result;
    if (data.baseRevision !== state.revision) throw new Error('Umamusume turn revision mismatch');
    return { ...state, history: [...state.history.slice(0, data.replaceFrom), ...data.historyAppend],
      checkpoint: data.checkpoint, checkpoints: data.checkpoints, tokenRatio: data.tokenRatio,
      queue: data.regenerate ? state.queue : [], retry: false, turns: state.turns + (data.regenerate ? 0 : 1), prepared: null, activity: null,
      lastInput: data.input, lastReply: data.reply, revision: state.revision + 1 };
  }
  if (event.type === 'turn/end' && (state.prepared || state.activity)) return { ...state, prepared: null, activity: null };
  if (!('ignorable' in event) || !event.ignorable || !event.type.startsWith(RECORD)) return state;
  if (event.type === RECORD + 'call') {
    const data = CallRecord.parse(event.data);
    return { ...state, activity: { label: data.label, phase: data.phase } };
  }
  if (event.type === RECORD + 'progress') {
    const data = ProgressRecord.parse(event.data);
    return { ...state, activity: { label: data.label ?? '历史记忆整理', phase: data.phase } };
  }
  if (event.type === RECORD + 'configure') {
    return { ...state, ...ConfigureRecord.parse(event.data).patch, revision: state.revision + 1 };
  }
  if (event.type === RECORD + 'prepared') return { ...state, prepared: PreparedRecord.parse(event.data) };
  return state;
}

export function replyText(reply: Reply) {
  // Escape Markdown syntax in model content while retaining paragraph breaks.
  const escape = (value: string) => value.replace(/[\\`*_{}\[\]<>#|]/g, '\\$&');
  return `**${escape(reply.characterName)}**\n\n${reply.action && reply.action !== '无' ? `*${escape(reply.action)}*\n\n` : ''}${escape(reply.dialogue)}`;
}

export const validateState = (value: unknown): State => State.parse(value);
export { State as stateSchema, PublicState as viewSchema };

export function publicState(state: State, catalog: Catalog): PublicState {
  return { enabled: state.enabled, character: state.character, mode: state.mode, queue: state.queue,
    turns: state.turns, retry: state.retry, lastReply: state.lastReply,
    memory: state.checkpoint?.summary ?? '', activity: state.activity, catalog };
}

export function sumUsage(total: TokenUsage | undefined, usage: TokenUsage | undefined): TokenUsage | undefined {
  if (!usage) return total;
  const next: TokenUsage = { inputTokens: 0, outputTokens: 0, ...total };
  for (const key of ['inputTokens', 'outputTokens', 'cacheReadTokens', 'cacheWriteTokens', 'reasoningTokens', 'totalTokens'] as const) {
    const value = usage[key];
    if (value !== undefined && Number.isFinite(value) && value >= 0) next[key] = (next[key] ?? 0) + value;
  }
  if (usage.totalTokens === undefined) {
    next.totalTokens = (next.totalTokens ?? 0) + (usage.inputTokens ?? 0) + (usage.outputTokens ?? 0)
      + (usage.cacheReadTokens ?? 0) + (usage.cacheWriteTokens ?? 0);
  }
  return next;
}

export function openAIUsage(usage: TokenUsage | undefined): OpenAIUsage | undefined {
  if (!usage) return undefined;
  const cached = usage.cacheReadTokens ?? 0;
  const prompt = (usage.inputTokens ?? 0) + cached + (usage.cacheWriteTokens ?? 0);
  return { prompt_tokens: prompt, completion_tokens: usage.outputTokens ?? 0,
    total_tokens: usage.totalTokens ?? prompt + (usage.outputTokens ?? 0),
    prompt_tokens_details: { cached_tokens: cached },
    completion_tokens_details: { reasoning_tokens: usage.reasoningTokens ?? 0 } };
}
