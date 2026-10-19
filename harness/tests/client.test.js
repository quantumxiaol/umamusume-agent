import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const source = readFileSync(new URL('../client.js', import.meta.url), 'utf8');

function mountClient(execute, definitions = []) {
  let plugin;
  runInNewContext(source, { window: { __ModuleLoader__: { load: entry => {
    plugin = entry.factory(() => ({ createElement() {}, useState() {} }));
  } } } });
  let dock;
  const services = {
    slots: {
      inject: (_name, install) => install(),
      register: entry => { if (entry.id === 'umamusume') dock = entry; },
    },
    uiConversation: { events: { register: definition => definitions.push(definition) } },
    remote: { commands: { execute } },
  };
  // Cordis requires each service actually accessed, including the remote root.
  // A remote.commands dependency does not grant access to ctx.remote.
  const ctx = new Proxy(services, { get(target, name) {
    if (!plugin.inject.includes(name)) throw new Error(`cannot get property "${name}" without inject`);
    return target[name];
  } });
  plugin.apply(ctx);
  return dock.inject('session-under-test');
}

test('browser controls can reach RPC with their declared services', async () => {
  const calls = [];
  const controls = mountClient(async (...args) => {
    calls.push(args);
    return { ok: true, value: { commandId: 'test', result: { kind: 'success' } } };
  });
  const commands = ['character rice_shower', 'mode action',
    'queue {"content":"在吗？","event_type":"dialogue"}', 'clear', 'retry'];
  for (const command of commands) await controls.execute(command);
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), commands.map(command => ['session-under-test', '/uma ' + command, []]));
});

test('built browser entry renders startup timings and retains model request details', () => {
  const definitions = [];
  mountClient(async () => {}, definitions);
  for (const definition of definitions) {
    const record = { type: 'plugin:umamusume/progress', seq: 4, time: 1000,
      data: { version: 1, turn: 1, step: 1, stage: 'python-startup', phase: 'start', label: '准备本地 Python 环境' } };
    const identity = definition.match(record);
    assert.ok(identity);
    const start = { event: record, role: 'start', location: { kind: 'unresolved' } };
    const context = { ...identity, key: 'startup', kind: definition.kind, start, state: definition.start({}, start) };
    context.state = definition.update(context, { event: { ...record, seq: 5, data: { ...record.data, phase: 'end', durationMs: 425 } } });
    const node = definition.buildViewNode(context);
    const display = JSON.stringify(node.data);
    assert.match(display, /425/);
    assert.match(display, /准备本地 Python 环境/);
    assert.equal(node.anchorSeq, 4);

    const call = { type: 'plugin:umamusume/call', seq: 6, time: 1500,
      data: { version: 1, turn: 1, step: 1, id: 'model-call', phase: 'start', label: '角色回复',
        request: { messages: [{ role: 'user', content: '核心实际提示词' }] } } };
    const callStart = { ...start, event: call };
    const callContext = { ...context, start: callStart, state: definition.start({}, callStart) };
    callContext.state = definition.update(callContext, { event: { ...call, seq: 7,
      data: { version: 1, turn: 1, step: 1, id: 'model-call', phase: 'end', label: '角色回复', durationMs: 100 } } });
    assert.match(JSON.stringify(definition.buildViewNode(callContext).data), /核心实际提示词/);
  }
});

test('browser controls report transport, unknown command and domain errors', async () => {
  for (const [result, message] of [
    [{ ok: false, error: { message: 'Connection closed' } }, /Connection closed/],
    [{ ok: true, value: undefined }, /赛马娘单聊/],
    [{ ok: true, value: { result: { kind: 'error', text: '请先停止或等待当前回复完成。' } } }, /请先停止/],
  ]) {
    await assert.rejects(mountClient(async () => result).execute('clear'), message);
  }
});
