import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const source = readFileSync(new URL('../client.js', import.meta.url), 'utf8');

function mountClient(execute) {
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
    uiConversation: { events: { register() {} } },
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

test('browser controls report transport, unknown command and domain errors', async () => {
  for (const [result, message] of [
    [{ ok: false, error: { message: 'Connection closed' } }, /Connection closed/],
    [{ ok: true, value: undefined }, /赛马娘单聊/],
    [{ ok: true, value: { result: { kind: 'error', text: '请先停止或等待当前回复完成。' } } }, /请先停止/],
  ]) {
    await assert.rejects(mountClient(async () => result).execute('clear'), message);
  }
});
