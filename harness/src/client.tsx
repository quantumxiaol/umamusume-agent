import React, { useState, type CSSProperties } from 'react';
import type { Context } from '@deepseek-ai/cordis';
import type { PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots';
import type { ConversationNodeDefinition, ConversationStartMatch } from '@deepseek-ai/dsh-client-ui-conversation/client';
import type { ChatNode } from '@deepseek-ai/dsh-client-ui-chat/client';
import type { TrajectoryConversationViewNode } from '@deepseek-ai/dsh-client-ui-trajectory/client';
import type { SessionEventLike } from '@deepseek-ai/dsh-api-session-controller/client';
import type {} from '@deepseek-ai/dsh-client-ui-renderer/client';
import type {} from '@deepseek-ai/dsh-client-ui-session/client';
import type {} from '@deepseek-ai/dsh-api-remotes/client';
import { CallRecord, ProgressRecord, errorMessage } from './protocol.js';

type Activity = CallRecord & { seq: number; time: number };
declare module '@deepseek-ai/dsh-client-ui-chat/client' {
  interface ChatNodeDataMap { 'umamusume-call': Activity }
}
const modes = [['dialogue', '训练员对白'], ['action', '训练员动作'], ['scene_event', '环境事件']] as const;
const control: CSSProperties = { border: '1px solid var(--dsw-alias-border-l2, #d9dde4)', borderRadius: 8,
  padding: '5px 9px', background: 'var(--dsw-alias-bg-layer-1, white)', color: 'inherit', font: 'inherit' };
const row: CSSProperties = { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 };
type Controls = { execute: (command: string) => Promise<void> };

function RoleplayDock({ useProjection, useSession, execute }: PropsRuntime<'conversation.input.dock'> & Controls) {
  const state = useProjection('umamusume');
  const running = useSession(snapshot => snapshot.running);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [queueText, setQueueText] = useState('');
  const [expanded, setExpanded] = useState(false);
  if (!state?.enabled) return null;
  const locked = !!running || busy;
  const act = async (command: string) => {
    setError(''); setBusy(true);
    try { await execute(command); return true; }
    catch (failure) { setError(errorMessage(failure)); return false; }
    finally { setBusy(false); }
  };
  return <section aria-label="赛马娘对话设置" style={{ padding: '10px 12px', fontSize: 13,
    width: '100%', maxWidth: 820, boxSizing: 'border-box', margin: '0 auto 8px', borderRadius: 12,
    border: '1px solid var(--dsw-alias-border-l2, #ddd)', color: 'var(--dsw-alias-label-primary, #202124)' }}>
    <div style={row}>
      <strong>🐎 赛马娘单聊</strong>
      <select aria-label="角色" style={control} value={state.character || ''} disabled={locked || state.turns > 0}
        title={state.turns ? '更换角色请新建会话' : '选择对话角色'} onChange={event => void act('character ' + event.target.value)}>
        {state.catalog.map(character => <option key={character.id} value={character.id}>{character.name}</option>)}
      </select>
      <select aria-label="输入类型" style={control} value={state.mode} disabled={locked} onChange={event => void act('mode ' + event.target.value)}>
        {modes.map(([value, name]) => <option key={value} value={value}>{name}</option>)}
      </select>
      <button type="button" style={control} onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>
        事件队列{state.queue.length ? ` · ${state.queue.length}` : ''}</button>
      <button type="button" style={control} disabled={locked || !state.turns} onClick={() => void act('retry')}>重生成上一轮</button>
    </div>
    {state.activity && running && <p role="status" style={{ margin: '8px 0 0' }}>{state.activity.label}…</p>}
    {expanded && <div style={{ marginTop: 10 }}>
      <textarea aria-label="待加入的剧情事件" placeholder="先加入动作或环境变化，再用下方输入框发送，整组只触发一次回复。"
        value={queueText} maxLength={10000} disabled={locked} rows={2}
        style={{ ...control, boxSizing: 'border-box', width: '100%', resize: 'vertical' }} onChange={e => setQueueText(e.target.value)} />
      <div style={{ ...row, marginTop: 6 }}>
        <button type="button" style={control} disabled={locked || !queueText.trim()} onClick={async () => {
          if (await act('queue ' + JSON.stringify({ content: queueText, event_type: state.mode }))) setQueueText('');
        }}>加入队列</button>
        <button type="button" style={control} disabled={locked || !state.queue.length} onClick={() => void act('clear')}>清空队列</button>
      </div>
      {!!state.queue.length && <ol style={{ marginBottom: 0, paddingLeft: 20 }}>{state.queue.map((event, index) =>
        <li key={index} style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
          {modes.find(([key]) => key === event.event_type)?.[1] || '事件'}：{event.content}</li>)}</ol>}
    </div>}
    {!!state.memory && <details style={{ marginTop: 8 }}><summary>历史记忆</summary>
      <div style={{ whiteSpace: 'pre-wrap', maxHeight: 260, overflow: 'auto' }}>{state.memory}</div></details>}
    {!!error && <p role="alert" style={{ color: '#c43b3b', margin: '8px 0 0' }}>{error}</p>}
    <div style={{ opacity: .65, fontSize: 12, marginTop: 7 }}>使用下方选定的模型与 API · 回复通过结构校验后显示 · 配音暂未接入</div>
  </section>;
}

function CallView({ node }: { node: ChatNode<'umamusume-call'> }) {
  const data = node.data, usage = data.usage;
  return <details style={{ fontSize: 12, opacity: .8, margin: '4px 0' }}>
    <summary>{data.label} · {data.phase === 'start' ? '进行中' : data.phase === 'error' ? '失败' : '完成'}
      {data.durationMs === undefined ? '' : ` · ${(data.durationMs / 1000).toFixed(1)}s`}</summary>
    {data.message && <p>{data.message}</p>}
    {usage && <p>输入 {usage.inputTokens + (usage.cacheReadTokens || 0) + (usage.cacheWriteTokens || 0)} · 缓存命中 {usage.cacheReadTokens || 0} · 输出 {usage.outputTokens}</p>}
  </details>;
}

function activityOf(event: SessionEventLike): CallRecord | null {
  // Experimental records deliberately do not augment Harness's released event union.
  const record: { type: string; data: unknown } = event;
  if (record.type === 'plugin:umamusume/call') return CallRecord.parse(record.data);
  if (record.type !== 'plugin:umamusume/progress') return null;
  const data = ProgressRecord.parse(record.data);
  if (!data.stage || !data.label || (data.phase !== 'start' && data.phase !== 'end' && data.phase !== 'error')) return null;
  return { version: 1, turn: data.turn, step: data.step, id: `${data.turn}:${data.step}:${data.stage}`,
    label: data.label, phase: data.phase, durationMs: data.durationMs, message: data.message };
}
function startActivity(match: ConversationStartMatch): Activity {
  const data = activityOf(match.event);
  if (!data) throw new Error('Missing roleplay activity');
  return { ...data, seq: match.event.seq, time: match.event.time };
}
const callDefinition: ConversationNodeDefinition<Activity> = {
  kind: 'umamusume-call', target: 'chat',
  match: event => { const data = activityOf(event); return data ? { id: data.id, role: 'start' } : null; },
  start: (_context, match) => startActivity(match),
  update: (context, match) => ({ ...context.state, ...activityOf(match.event) }),
  buildViewNode: context => context.state === undefined ? null : ({ key: context.key, kind: 'umamusume-call',
    id: context.id, target: 'chat', anchorSeq: context.state.seq, visibility: 'visible', groupTree: 'process',
    location: context.start?.location ?? { kind: 'unresolved' }, data: context.state } satisfies ChatNode<'umamusume-call'>),
};
const trajectoryDefinition: ConversationNodeDefinition<Activity> = {
  ...callDefinition, kind: 'umamusume-call-trace', target: 'trajectory',
  buildViewNode: context => {
    const data = context.state;
    if (!data) return null;
    const text = `${data.label} · ${data.phase}\n` + JSON.stringify({ provider: data.provider, model: data.model,
      durationMs: data.durationMs, usage: data.usage, error: data.message, request: data.request }, null, 2);
    return { key: context.key, kind: context.kind, id: context.id, target: 'trajectory', anchorSeq: data.seq,
      location: context.start?.location ?? { kind: 'unresolved' },
      data: { kind: 'node', node: { kind: 'context', seq: data.seq, time: data.time,
        source: { kind: 'umamusume', form: 'snapshot' }, producer: { role: 'inject', label: '赛马娘运行记录' },
        form: 'snapshot', content: [{ type: 'text', text }] } } } satisfies TrajectoryConversationViewNode;
  },
};

export const inject = ['slots', 'uiConversation', 'remote', 'remote.commands'];
export function apply(ctx: Context) {
  ctx.uiConversation.events.register(callDefinition);
  ctx.uiConversation.events.register(trajectoryDefinition);
  ctx.slots.inject('conversation.chat.node', () => ctx.slots.register({ name: 'conversation.chat.node', key: 'umamusume-call' }, CallView));
  ctx.slots.inject('conversation.input.dock', () => ctx.slots.register({
    name: 'conversation.input.dock', id: 'umamusume', order: 20,
    inject: sessionId => ({ execute: async (command: string) => {
      const result = await ctx.remote.commands.execute(sessionId, '/uma ' + command, []);
      if (!result.ok) throw new Error(result.error.message);
      if (!result.value) throw new Error('请先选择“赛马娘单聊”会话预设。');
      if (result.value.result.kind === 'error') throw new Error(result.value.result.text);
    } }),
  }, RoleplayDock));
}
