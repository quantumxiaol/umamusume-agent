// Generated from harness/src by npm run build. Do not edit.
import { z } from "zod";
const modes = ["dialogue", "action", "scene_event"];
const Mode = z.enum(modes);
const count = z.number().int().nonnegative();
const actor = z.object({
  actor_id: z.string(),
  actor_type: z.enum(["trainer", "umamusume", "npc", "narrator", "director"]),
  display_name: z.string(),
  character_id: z.string().nullable().optional(),
  role_in_scene: z.string().nullable().optional()
});
const QueueItem = z.object({ content: z.string().min(1).max(1e4), event_type: Mode, speaker: actor.optional() });
const TurnInput = z.object({ message: z.string(), mode: Mode, queue: z.array(QueueItem), historyStart: count.optional() });
const checkpoint = z.object({
  schema_version: z.literal(1),
  checkpoint_id: z.string(),
  revision: z.number().int().positive(),
  user_uuid: z.string(),
  character_id: z.string(),
  covered_messages: z.number().int().positive(),
  trigger_message_count: z.number().int().positive().nullable().optional(),
  source_digest: z.string().regex(/^[a-f0-9]{64}$/),
  prompt_digest: z.string().regex(/^[a-f0-9]{64}$/),
  summary: z.string().min(1),
  created_at: z.string(),
  token_ratio: z.number().min(0.1).max(1.5)
});
const historyMessage = z.object({ role: z.enum(["user", "assistant"]), content: z.string() }).passthrough();
const Reply = z.object({ characterName: z.string(), action: z.string(), dialogue: z.string() }).passthrough();
const WorkerResult = z.object({
  version: z.literal(1),
  baseRevision: count,
  replaceFrom: count,
  historyAppend: z.array(historyMessage),
  regenerate: z.boolean(),
  checkpoint: checkpoint.nullable(),
  checkpoints: z.array(checkpoint),
  tokenRatio: z.number().positive(),
  input: TurnInput.extend({ historyStart: count }),
  reply: Reply
});
const position = { turn: count, step: count };
const Prepared = z.object({ ...position, result: WorkerResult });
const activity = z.object({ label: z.string(), phase: z.string() });
const State = z.object({
  version: z.literal(1),
  enabled: z.boolean(),
  character: z.string().nullable(),
  mode: Mode,
  queue: z.array(QueueItem),
  history: z.array(historyMessage),
  checkpoint: checkpoint.nullable(),
  checkpoints: z.array(checkpoint),
  tokenRatio: z.number().positive(),
  turns: count,
  lastInput: TurnInput.extend({ historyStart: count }).nullable(),
  lastReply: Reply.nullable(),
  retry: z.boolean(),
  revision: count,
  prepared: Prepared.nullable(),
  activity: activity.nullable()
});
const Catalog = z.array(z.object({ id: z.string(), name: z.string() }));
const PublicState = State.pick({
  enabled: true,
  character: true,
  mode: true,
  queue: true,
  turns: true,
  retry: true,
  lastReply: true,
  activity: true
}).extend({ memory: z.string(), catalog: Catalog });
const ModelCall = z.object({
  messages: z.array(z.object({ role: z.enum(["system", "user", "assistant"]), content: z.string() })).min(1),
  model: z.string().optional(),
  max_tokens: z.number().int().positive().optional(),
  temperature: z.number().optional(),
  stream: z.boolean().optional()
}).passthrough();
const usage = z.object({
  inputTokens: count,
  outputTokens: count,
  totalTokens: count.optional(),
  cacheReadTokens: count.optional(),
  cacheWriteTokens: count.optional(),
  reasoningTokens: count.optional()
});
const CallRecord = z.object({
  version: z.literal(1),
  ...position,
  id: z.string(),
  label: z.string(),
  phase: z.enum(["start", "end", "error"]),
  provider: z.string().optional(),
  model: z.string().optional(),
  request: ModelCall.optional(),
  started: z.number().optional(),
  durationMs: z.number().nonnegative().optional(),
  usage: usage.nullable().optional(),
  message: z.string().optional()
});
const Progress = z.object({
  phase: z.string(),
  label: z.string().optional(),
  stage: z.string().optional(),
  message: z.string().optional(),
  durationMs: z.number().nonnegative().optional()
}).passthrough();
const ProgressRecord = Progress.extend({ version: z.literal(1), ...position });
const ConfigureRecord = z.object({ version: z.literal(1), patch: State.pick({ character: true, mode: true, queue: true }).partial() });
const PreparedRecord = Prepared.extend({ version: z.literal(1) });
const WorkerEvent = z.discriminatedUnion("type", [
  z.object({ type: z.literal("ready") }),
  z.object({ type: z.literal("model"), id: z.string(), call: ModelCall }),
  z.object({ type: z.literal("progress"), data: Progress }),
  z.object({ type: z.literal("result"), result: WorkerResult }),
  z.object({ type: z.literal("error"), message: z.string() })
]);
function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}
export {
  CallRecord,
  Catalog,
  ConfigureRecord,
  Mode,
  ModelCall,
  Prepared,
  PreparedRecord,
  Progress,
  ProgressRecord,
  PublicState,
  QueueItem,
  Reply,
  State,
  TurnInput,
  WorkerEvent,
  WorkerResult,
  errorMessage,
  modes
};
