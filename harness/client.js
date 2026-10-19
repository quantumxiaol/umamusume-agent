/* Prebuilt Harness browser entry. Plain JavaScript: git installs need no prepare script. */
window.__ModuleLoader__.load({
  id: 'dsh-umamusume-agent',
  factory: require => {
    const React = require('react');
    const h = React.createElement;
    const { useState } = React;
    const modes = [['dialogue', '训练员对白'], ['action', '训练员动作'], ['scene_event', '环境事件']];
    const control = { border: '1px solid var(--dsw-alias-border-l2, #d9dde4)', borderRadius: 8,
      padding: '5px 9px', background: 'var(--dsw-alias-bg-layer-1, white)', color: 'inherit', font: 'inherit' };

    function RoleplayDock({ useProjection, useSession, execute }) {
      const state = useProjection('umamusume');
      const running = useSession(snapshot => snapshot.running);
      const [error, setError] = useState('');
      const [busy, setBusy] = useState(false);
      const [queueText, setQueueText] = useState('');
      const [expanded, setExpanded] = useState(false);
      if (!state?.enabled) return null;
      const locked = !!running || busy;
      const act = async command => {
        setError(''); setBusy(true);
        try { await execute(command); return true; }
        catch (failure) { setError(failure.message); return false; }
        finally { setBusy(false); }
      };
      const row = { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 };
      return h('section', { 'aria-label': '赛马娘对话设置', style: { padding: '10px 12px', fontSize: 13,
        width: '100%', maxWidth: 820, boxSizing: 'border-box', margin: '0 auto 8px', borderRadius: 12,
        border: '1px solid var(--dsw-alias-border-l2, #ddd)', color: 'var(--dsw-alias-label-primary, #202124)' } },
        h('div', { style: row },
          h('strong', null, '🐎 赛马娘单聊'),
          h('select', { 'aria-label': '角色', style: control, value: state.character || '', disabled: locked || state.turns > 0,
            title: state.turns ? '更换角色请新建会话' : '选择对话角色',
            onChange: event => act('character ' + event.target.value) },
          ...(state.catalog || []).map(character => h('option', { key: character.id, value: character.id }, character.name))),
          h('select', { 'aria-label': '输入类型', style: control, value: state.mode, disabled: locked,
            onChange: event => act('mode ' + event.target.value) },
          ...modes.map(([value, name]) => h('option', { key: value, value }, name))),
          h('button', { type: 'button', style: control, onClick: () => setExpanded(!expanded), 'aria-expanded': expanded },
            `事件队列${state.queue.length ? ` · ${state.queue.length}` : ''}`),
          h('button', { type: 'button', style: control, disabled: locked || !state.turns,
            onClick: () => act('retry') }, '重生成上一轮')),
        state.activity && running ? h('p', { role: 'status', style: { margin: '8px 0 0' } }, `${state.activity.label}…`) : null,
        expanded ? h('div', { style: { marginTop: 10 } },
          h('textarea', { 'aria-label': '待加入的剧情事件', placeholder: '先加入动作或环境变化，再用下方输入框发送，整组只触发一次回复。',
            value: queueText, maxLength: 10000, disabled: locked, rows: 2,
            style: { ...control, boxSizing: 'border-box', width: '100%', resize: 'vertical' }, onChange: e => setQueueText(e.target.value) }),
          h('div', { style: { ...row, marginTop: 6 } },
            h('button', { type: 'button', style: control, disabled: locked || !queueText.trim(), onClick: async () => {
              if (await act('queue ' + JSON.stringify({ content: queueText, event_type: state.mode }))) setQueueText('');
            } }, '加入队列'),
            h('button', { type: 'button', style: control, disabled: locked || !state.queue.length, onClick: () => act('clear') }, '清空队列')),
          state.queue.length ? h('ol', { style: { marginBottom: 0, paddingLeft: 20 } }, ...state.queue.map((event, index) =>
            h('li', { key: index, style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' } },
              `${modes.find(([key]) => key === event.event_type)?.[1] || '事件'}：${event.content}`))) : null) : null,
        state.memory ? h('details', { style: { marginTop: 8 } }, h('summary', null, '历史记忆'),
          h('div', { style: { whiteSpace: 'pre-wrap', maxHeight: 260, overflow: 'auto' } }, state.memory)) : null,
        error ? h('p', { role: 'alert', style: { color: '#c43b3b', margin: '8px 0 0' } }, error) : null,
        h('div', { style: { opacity: .65, fontSize: 12, marginTop: 7 } }, '使用下方选定的模型与 API · 回复通过结构校验后显示 · 配音暂未接入'));
    }

    function CallView({ node }) {
      const data = node.data;
      const usage = data.usage;
      return h('details', { style: { fontSize: 12, opacity: .8, margin: '4px 0' } },
        h('summary', null, `${data.label} · ${data.phase === 'start' ? '生成中' : data.phase === 'error' ? '失败' : '完成'}`
          + (data.durationMs === undefined ? '' : ` · ${(data.durationMs / 1000).toFixed(1)}s`)),
        data.message ? h('p', null, data.message) : null,
        usage ? h('p', null, `输入 ${(usage.inputTokens || 0) + (usage.cacheReadTokens || 0) + (usage.cacheWriteTokens || 0)} · 缓存命中 ${usage.cacheReadTokens || 0} · 输出 ${usage.outputTokens || 0}`) : null);
    }

    const callDefinition = {
      kind: 'umamusume-call', target: 'chat',
      match: event => event.type === 'plugin:umamusume/call'
        ? { id: event.data.id, role: 'start' } : null,
      start: (_context, match) => ({ ...match.event.data, seq: match.event.seq }),
      update: (context, match) => ({ ...context.state, ...match.event.data }),
      buildViewNode: context => context.state === undefined ? null : ({ key: context.key, kind: 'umamusume-call',
        id: context.id, target: 'chat', anchorSeq: context.state.seq, visibility: 'visible', groupTree: 'process',
        location: context.start?.location ?? { kind: 'unresolved' }, data: context.state }),
    };

    const trajectoryDefinition = {
      ...callDefinition, kind: 'umamusume-call-trace', target: 'trajectory',
      start: (_context, match) => ({ ...match.event.data, seq: match.event.seq, time: match.event.time }),
      buildViewNode: context => {
        const data = context.state;
        if (!data) return null;
        const text = `${data.label} · ${data.phase}\n` + JSON.stringify({
          provider: data.provider, model: data.model, durationMs: data.durationMs,
          usage: data.usage, error: data.message, request: data.request,
        }, null, 2);
        return { key: context.key, kind: context.kind, id: context.id, target: 'trajectory', anchorSeq: data.seq,
          location: context.start?.location ?? { kind: 'unresolved' },
          data: { kind: 'node', node: { kind: 'context', seq: data.seq, time: data.time,
            source: { kind: 'umamusume', form: 'snapshot' }, producer: { role: 'inject', label: '赛马娘模型调用' },
            form: 'snapshot', content: [{ type: 'text', text }] } } };
      },
    };

    function apply(ctx) {
      ctx.uiConversation.events.register(callDefinition);
      ctx.uiConversation.events.register(trajectoryDefinition);
      ctx.slots.inject('conversation.chat.node', () => ctx.slots.register({
        name: 'conversation.chat.node', key: 'umamusume-call',
      }, CallView));
      ctx.slots.inject('conversation.input.dock', () => ctx.slots.register({
        name: 'conversation.input.dock', id: 'umamusume', order: 20,
        inject: sessionId => ({ execute: async command => {
          const result = await ctx.remote.commands.execute(sessionId, '/uma ' + command, []);
          if (!result.ok) throw new Error(result.error.message);
          if (!result.value) throw new Error('请先选择“赛马娘单聊”会话预设。');
          if (result.value.result.kind === 'error') throw new Error(result.value.result.text);
        } }),
      }, RoleplayDock));
    }
    return { apply, inject: ['slots', 'uiConversation', 'remote', 'remote.commands'] };
  },
});
