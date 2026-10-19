/** Harness owns durable state; Python only proposes a completed turn. */
export const PRESET = 'umamusume';
export const STATE_KEY = 'umamusume';
export const RECORD = 'plugin:umamusume/';
export const MODES = ['dialogue', 'action', 'scene_event'];

export function initialState(enabled = false) {
  return { version: 1, enabled, character: null, mode: 'dialogue', queue: [], history: [],
    checkpoint: null, checkpoints: [], tokenRatio: 0.5, turns: 0, lastInput: null, lastReply: null,
    retry: false, revision: 0, prepared: null, activity: null };
}

export function reduceState(state, event) {
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
  if (!event.ignorable || !event.type.startsWith(RECORD)) return state;
  const data = event.data;
  if (data.version !== 1) throw new Error('Unsupported Umamusume state version; update the plugin.');
  if (event.type === RECORD + 'call') return { ...state, activity: { label: data.label, phase: data.phase } };
  if (event.type === RECORD + 'progress') return { ...state, activity: { label: '历史记忆整理', phase: data.phase } };
  if (event.type === RECORD + 'configure') {
    return { ...state, ...data.patch, revision: state.revision + 1 };
  }
  if (event.type === RECORD + 'prepared') return { ...state, prepared: data };
  return state;
}

export function replyText(reply) {
  // Escape Markdown syntax in model content while retaining paragraph breaks.
  const escape = value => String(value).replace(/[\\`*_{}\[\]<>#|]/g, '\\$&');
  return `**${escape(reply.characterName)}**\n\n${reply.action && reply.action !== '无' ? `*${escape(reply.action)}*\n\n` : ''}${escape(reply.dialogue)}`;
}

export function validateState(value) {
  if (!value || value.version !== 1 || !Array.isArray(value.history) || !Array.isArray(value.queue)
      || !MODES.includes(value.mode) || !Number.isSafeInteger(value.revision)) {
    throw new Error('Invalid or unsupported Umamusume session state');
  }
  return value;
}

export function publicState(state, catalog) {
  return { enabled: state.enabled, character: state.character, mode: state.mode, queue: state.queue,
    turns: state.turns, retry: state.retry, lastReply: state.lastReply,
    memory: state.checkpoint?.summary ?? '', activity: state.activity, catalog };
}

export function sumUsage(total, usage) {
  if (!usage) return total;
  const next = { ...total };
  for (const key of ['inputTokens', 'outputTokens', 'cacheReadTokens', 'cacheWriteTokens', 'reasoningTokens', 'totalTokens']) {
    if (Number.isFinite(usage[key]) && usage[key] >= 0) next[key] = (next[key] ?? 0) + usage[key];
  }
  if (usage.totalTokens === undefined) {
    next.totalTokens = (next.totalTokens ?? 0) + (usage.inputTokens ?? 0) + (usage.outputTokens ?? 0)
      + (usage.cacheReadTokens ?? 0) + (usage.cacheWriteTokens ?? 0);
  }
  return next;
}

export function openAIUsage(usage) {
  if (!usage) return undefined;
  const cached = usage.cacheReadTokens ?? 0;
  const prompt = (usage.inputTokens ?? 0) + cached + (usage.cacheWriteTokens ?? 0);
  return { prompt_tokens: prompt, completion_tokens: usage.outputTokens ?? 0,
    total_tokens: usage.totalTokens ?? prompt + (usage.outputTokens ?? 0),
    prompt_tokens_details: { cached_tokens: cached },
    completion_tokens_details: { reasoning_tokens: usage.reasoningTokens ?? 0 } };
}
