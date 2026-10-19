import { z } from 'zod';
import type { GenerateOptions } from '@deepseek-ai/dsh-llm';

export const modes = ['dialogue', 'action', 'scene_event'] as const;
export const Mode = z.enum(modes);
const count = z.number().int().nonnegative();
const actor = z.object({
  actor_id: z.string(), actor_type: z.enum(['trainer', 'umamusume', 'npc', 'narrator', 'director']),
  display_name: z.string(), character_id: z.string().nullable().optional(), role_in_scene: z.string().nullable().optional(),
});
export const QueueItem = z.object({ content: z.string().min(1).max(10000), event_type: Mode, speaker: actor.optional() });
export const TurnInput = z.object({ message: z.string(), mode: Mode, queue: z.array(QueueItem), historyStart: count.optional() });
const checkpoint = z.object({
  schema_version: z.literal(1), checkpoint_id: z.string(), revision: z.number().int().positive(),
  user_uuid: z.string(), character_id: z.string(), covered_messages: z.number().int().positive(),
  trigger_message_count: z.number().int().positive().nullable().optional(),
  source_digest: z.string().regex(/^[a-f0-9]{64}$/), prompt_digest: z.string().regex(/^[a-f0-9]{64}$/),
  summary: z.string().min(1), created_at: z.string(), token_ratio: z.number().min(.1).max(1.5),
});
const historyMessage = z.object({ role: z.enum(['user', 'assistant']), content: z.string() }).passthrough();
export const Reply = z.object({ characterName: z.string(), action: z.string(), dialogue: z.string() }).passthrough();
export const WorkerResult = z.object({
  version: z.literal(1), baseRevision: count, replaceFrom: count, historyAppend: z.array(historyMessage), regenerate: z.boolean(),
  checkpoint: checkpoint.nullable(), checkpoints: z.array(checkpoint), tokenRatio: z.number().positive(),
  input: TurnInput.extend({ historyStart: count }), reply: Reply,
});
const position = { turn: count, step: count };
export const Prepared = z.object({ ...position, result: WorkerResult });
const activity = z.object({ label: z.string(), phase: z.string() });
export const State = z.object({
  version: z.literal(1), enabled: z.boolean(), character: z.string().nullable(), mode: Mode, queue: z.array(QueueItem),
  history: z.array(historyMessage), checkpoint: checkpoint.nullable(), checkpoints: z.array(checkpoint),
  tokenRatio: z.number().positive(), turns: count, lastInput: TurnInput.extend({ historyStart: count }).nullable(),
  lastReply: Reply.nullable(), retry: z.boolean(), revision: count, prepared: Prepared.nullable(), activity: activity.nullable(),
});
export const Catalog = z.array(z.object({ id: z.string(), name: z.string() }));
export const PublicState = State.pick({ enabled: true, character: true, mode: true, queue: true,
  turns: true, retry: true, lastReply: true, activity: true }).extend({ memory: z.string(), catalog: Catalog });
export const ModelCall = z.object({
  messages: z.array(z.object({ role: z.enum(['system', 'user', 'assistant']), content: z.string() })).min(1),
  model: z.string().optional(), max_tokens: z.number().int().positive().optional(), temperature: z.number().optional(),
  stream: z.boolean().optional(),
}).passthrough();
const usage = z.object({ inputTokens: count, outputTokens: count, totalTokens: count.optional(),
  cacheReadTokens: count.optional(), cacheWriteTokens: count.optional(), reasoningTokens: count.optional() });
export const CallRecord = z.object({
  version: z.literal(1), ...position, id: z.string(), label: z.string(), phase: z.enum(['start', 'end', 'error']),
  provider: z.string().optional(), model: z.string().optional(), request: ModelCall.optional(), started: z.number().optional(),
  durationMs: z.number().nonnegative().optional(), usage: usage.nullable().optional(), message: z.string().optional(),
});
export const Progress = z.object({ phase: z.string(), label: z.string().optional(),
  stage: z.string().optional(), message: z.string().optional(), durationMs: z.number().nonnegative().optional() }).passthrough();
export const ProgressRecord = Progress.extend({ version: z.literal(1), ...position });
export const ConfigureRecord = z.object({ version: z.literal(1), patch: State.pick({ character: true, mode: true, queue: true }).partial() });
export const PreparedRecord = Prepared.extend({ version: z.literal(1) });
export const WorkerEvent = z.discriminatedUnion('type', [
  z.object({ type: z.literal('ready') }),
  z.object({ type: z.literal('model'), id: z.string(), call: ModelCall }),
  z.object({ type: z.literal('progress'), data: Progress }),
  z.object({ type: z.literal('result'), result: WorkerResult }),
  z.object({ type: z.literal('error'), message: z.string() }),
]);
export type State = z.infer<typeof State>;
export type PublicState = z.infer<typeof PublicState>;
export type ModelCall = z.infer<typeof ModelCall>;
export type WorkerResult = z.infer<typeof WorkerResult>;
export type Progress = z.infer<typeof Progress>;
export type Reply = z.infer<typeof Reply>;
export type Catalog = z.infer<typeof Catalog>;
export type TurnInput = z.infer<typeof TurnInput>;
export type CallRecord = z.infer<typeof CallRecord>;
export type ModelRoute = Pick<GenerateOptions, 'provider' | 'model' | 'reasoningEffort'>;
export interface WorkerInput {
  sessionId: string;
  state: State;
  input: TurnInput;
  options: { model: string; contextWindow: number; maxOutputTokens: number };
}
export interface WorkerConfig { pythonExecutable: string; uvExecutable: string; charactersDirectory: string }
export interface OpenAIUsage {
  prompt_tokens: number; completion_tokens: number; total_tokens: number;
  prompt_tokens_details: { cached_tokens: number }; completion_tokens_details: { reasoning_tokens: number };
}
export interface ModelResponse {
  id: string; model: string;
  choices: { message: { content: string }; finish_reason: 'stop' | 'length' }[];
  usage?: OpenAIUsage;
}
export interface ModelChunk { choices: { delta: { content?: string }; finish_reason: 'stop' | 'length' | null }[]; usage?: OpenAIUsage }
export function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error); }

declare module '@deepseek-ai/dsh-session-projection/types' {
  interface SessionProjectionStateMap { umamusume: State }
  interface SessionProjectionMap { umamusume: PublicState }
}
declare module '@deepseek-ai/dsh-session/types' {
  interface PluginRecordMap {
    'plugin:umamusume/call': CallRecord;
    'plugin:umamusume/progress': z.infer<typeof ProgressRecord>;
    'plugin:umamusume/configure': z.infer<typeof ConfigureRecord>;
    'plugin:umamusume/prepared': z.infer<typeof PreparedRecord>;
  }
}
