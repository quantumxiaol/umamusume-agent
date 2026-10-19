/** Load only in an isolated DSH_HOME with mock-provider.js. Never shipped. */
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { createUserMessage } from '@deepseek-ai/dsh-llm';

export const inject = ['agents', 'agentPresets', 'umamusume'];

export function apply(ctx) {
  if (!process.env.UMA_SMOKE_RESULT || !process.env.DSH_HOME?.includes('uma-harness-test')) {
    throw new Error('Host smoke requires isolated test home and UMA_SMOKE_RESULT');
  }
  ctx.effect(() => {
    const timer = setTimeout(() => { void run(ctx).then(
      result => writeFile(process.env.UMA_SMOKE_RESULT, JSON.stringify({ ok: true, ...result })),
      error => writeFile(process.env.UMA_SMOKE_RESULT, JSON.stringify({ ok: false, error: error.stack })),
    ); }, 0);
    return () => clearTimeout(timer);
  });
}

async function run(ctx) {
  const id = 'session-uma-smoke-' + randomUUID();
  const setup = async aCtx => { await ctx.agentPresets.mount(aCtx, 'umamusume'); };
  const agentOptions = { provider: 'uma-test', model: 'mock' };
  let handle = await ctx.agents.create({ sessionId: id, agentOptions,
    meta: { agentPreset: 'umamusume', cwd: process.env.DSH_HOME }, setup });
  let agent = handle.agent;
  const state = () => ctx.umamusume.state(agent);
  const configure = rawInput => {
    const result = ctx.umamusume.configure({ agent, rawInput });
    assert.equal(result.kind, 'success', result.text);
  };
  const send = async text => {
    agent.followup(createUserMessage({ content: [{ type: 'text', text }], source: { kind: 'user' } }));
    await agent.whenIdle();
  };
  try {
    configure('character special_week');
    configure('mode action');
    configure('queue ' + JSON.stringify({ content: '训练场下起小雨。', event_type: 'scene_event' }));
    await send('递给你一把伞');
    assert.equal(state().turns, 1);
    assert.equal(state().queue.length, 0);
    assert.equal(state().character, 'special_week');
    assert.equal(state().history.length, 3);
    assert.ok(state().history[0].content.includes('小雨'));
    configure('retry');
    await agent.whenIdle();
    assert.equal(state().history.length, 3);
    assert.equal(state().turns, 1);
    const history = structuredClone(state().history);
    const cancel = send('__uma_cancel_test__');
    const deadline = Date.now() + 15000;
    while (state().activity?.phase !== 'start') {
      if (Date.now() > deadline) throw new Error('Timed out waiting for cancellable model call');
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    agent.cancel({ kind: 'user' });
    await cancel;
    assert.deepEqual(state().history, history);
    assert.equal(state().prepared, null);
    await handle.dispose();
    handle = await ctx.agents.resume({ resumeSessionId: id, agentOptions, setup });
    agent = handle.agent;
    assert.deepEqual(state().history, history);
    assert.equal(state().mode, 'action');
    await send('谢谢，继续一起训练吧。');
    assert.equal(state().turns, 2);
    assert.equal(state().history.length, 5);
    return { sessionId: id, checks: ['character', 'event queue', 'regenerate', 'cancel', 'durable resume', 'continue'] };
  } finally { await handle.dispose(); }
}
