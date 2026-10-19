import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { createInterface } from 'node:readline';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));

export function workerCommand(config) {
  if (config.pythonExecutable) return [config.pythonExecutable, ['-u', '-m', 'umamusume_agent.harness.worker']];
  const uv = config.uvExecutable || (existsSync(join(homedir(), '.local/bin/uv')) ? join(homedir(), '.local/bin/uv') : 'uv');
  return [uv, ['run', '--isolated', '--no-project', '--python', '3.12', '--with-requirements', join(root, 'harness/requirements.txt'),
    'python', '-u', '-m', 'umamusume_agent.harness.worker']];
}

/** Private stdio channel. No HTTP listener, provider credentials, or shared Python state. */
export async function runWorker({ config, input, signal, onModel, onProgress = () => {} }) {
  signal.throwIfAborted();
  const [command, args] = workerCommand(config);
  const env = { ...process.env, PYTHONUNBUFFERED: '1', UMAMUSUME_SKIP_ENV_FILES: '1',
    PYTHONPATH: join(root, 'src'), ROLEPLAY_LLM_MODEL_API_KEY: '', ROLEPLAY_LLM_MODEL_BASE_URL: 'harness://local', ENABLE_TTS: 'false' };
  for (const key of Object.keys(env)) {
    if (/(?:API_KEY|APIKEY|ACCESS_TOKEN|AUTH_TOKEN|SECRET)/i.test(key)) delete env[key];
  }
  // A package checkout's environment and PYTHONPATH must never select a different core.
  delete env.VIRTUAL_ENV;
  const scratchDirectory = await mkdtemp(join(tmpdir(), 'umamusume-harness-'));
  const child = spawn(command, args, { cwd: root, env, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  child.on('close', () => { void rm(scratchDirectory, { recursive: true, force: true }).catch(() => {}); });
  let stderr = '', settled = false, killTimer;
  child.stderr.setEncoding('utf8');
  child.stderr.on('data', data => { stderr = (stderr + data).slice(-6000); });
  const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
  const write = value => { if (!child.stdin.destroyed) child.stdin.write(JSON.stringify(value) + '\n'); };
  const stop = () => {
    if (child.exitCode !== null) return;
    child.kill('SIGTERM');
    killTimer = setTimeout(() => child.kill('SIGKILL'), 2000);
    killTimer.unref();
  };
  return new Promise((resolve, reject) => {
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener('abort', abort);
      lines.close();
      child.stdin.end();
      stop();
      error ? reject(error) : resolve(value);
    };
    const abort = () => finish(signal.reason instanceof Error ? signal.reason : new Error('Roleplay cancelled'));
    signal.addEventListener('abort', abort, { once: true });
    child.on('error', error => finish(new Error(`无法启动本地 Python：${error.message}。请安装 uv，或配置 pythonExecutable。`)));
    child.on('exit', code => {
      clearTimeout(killTimer);
      if (!settled) finish(new Error(`本地角色服务退出 (${code})：${stderr || 'no diagnostic output'}`));
    });
    child.stdin.on('error', error => { if (!settled) finish(error); });
    lines.on('line', line => {
      if (settled) return;
      let event;
      try { event = JSON.parse(line); } catch { finish(new Error('Invalid Python bridge protocol')); return; }
      if (event.type === 'result') finish(null, event.result);
      else if (event.type === 'error') finish(new Error(event.message));
      else if (event.type === 'progress') {
        try { onProgress(event.data); } catch (error) { finish(error); }
      }
      else if (event.type === 'model') {
        Promise.resolve().then(() => onModel(event.call, chunk => write({ type: 'chunk', id: event.id, chunk })))
          .then(response => write({ type: 'completion', id: event.id, response }),
            error => write({ type: 'model_error', id: event.id, message: error.message }));
      }
    });
    if (signal.aborted) abort();
    else write({ ...input, scratchDirectory, charactersDirectory: config.charactersDirectory || join(root, 'characters') });
  });
}
