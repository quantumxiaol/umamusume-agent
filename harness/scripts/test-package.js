import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { mkdtemp, readFile, writeFile, rm, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const root = fileURLToPath(new URL('../../', import.meta.url));
const cli = process.env.UMA_TEST_DSH || join(root, 'node_modules/@deepseek-ai/dsh/lib/bin.js');
const home = await mkdtemp(join(tmpdir(), 'uma-harness-test-package-'));
const resultFile = join(home, 'result.json');
const python = process.env.UMA_TEST_PYTHON || join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const env = { ...process.env, DSH_HOME: home, UMA_SMOKE_RESULT: resultFile,
  npm_config_cache: process.env.npm_config_cache || join(home, 'npm-cache'), NO_COLOR: '1' };
// Never inherit model credentials into the isolated host test.
for (const key of Object.keys(env)) if (/(?:API_KEY|APIKEY|ACCESS_TOKEN|AUTH_TOKEN|SECRET)/i.test(key)) delete env[key];
const profile = 'package-test';
let server, serverOutput = '';
function run(command, args, cwd = home, timeout = 180000) {
  return new Promise((resolveRun, reject) => {
    const child = spawn(command, args, { cwd, env, stdio: ['ignore', 'pipe', 'pipe'], timeout });
    let output = '';
    child.stdout.on('data', data => { output += data; });
    child.stderr.on('data', data => { output += data; });
    child.on('error', reject);
    child.on('close', code => code === 0 ? resolveRun(output) : reject(new Error(`${command} exited ${code}:\n${output.slice(-8000)}`)));
  });
}
const dsh = args => run(process.execPath, ['--expose-internals', cli, ...args]);
try {
  const packDir = join(home, 'pack');
  await mkdir(packDir);
  const pack = JSON.parse(await run(process.platform === 'win32' ? 'npm.cmd' : 'npm',
    ['pack', '--ignore-scripts', '--json', '--pack-destination', packDir], root))[0];
  await dsh(['--profile', profile, '--from-default-profile', 'web', '--dump-config']);
  await dsh(['plugin', '--profile', profile, 'add', join(packDir, pack.filename)]);
  // Quote paths as YAML JSON strings; the test files resolve their host peers through the root manifest.
  const patch = join(home, 'smoke.yml');
  await writeFile(patch, `
- id: umamusume-runtime
  config:
    pythonExecutable: ${JSON.stringify(resolve(python))}
- id: agent-default-model
  config: { provider: uma-test, model: mock }
- id: session-title-llm
  disabled: true
- id: session-telemetry-otel
  disabled: true
- id: desktop-product-telemetry
  disabled: true
- insert:
    - id: uma-test-model
      name: ${JSON.stringify(join(root, 'harness/tests/mock-provider.js'))}
    - id: uma-smoke-tests
      name: ${JSON.stringify(join(root, 'harness/tests/host-smoke.js'))}
`);
  const socket = createServer();
  await new Promise((resolvePort, reject) => { socket.once('error', reject); socket.listen(0, '127.0.0.1', resolvePort); });
  const port = socket.address().port;
  await new Promise(resolveClose => socket.close(resolveClose));
  server = spawn(process.execPath, ['--expose-internals', cli, '--profile', profile, '--patch', patch,
    '--host', '127.0.0.1', '--port', String(port), '--no-open'], { cwd: home, env, stdio: ['ignore', 'pipe', 'pipe'] });
  let serverError;
  server.on('error', error => { serverError = error; });
  for (const stream of [server.stdout, server.stderr]) stream.on('data', data => { serverOutput = (serverOutput + data).slice(-12000); });
  const deadline = Date.now() + 90000;
  let result;
  while (Date.now() < deadline) {
    try { result = JSON.parse(await readFile(resultFile, 'utf8')); break; }
    catch (error) { if (error.code !== 'ENOENT' && !(error instanceof SyntaxError)) throw error; }
    if (serverError) throw serverError;
    if (server.exitCode !== null) throw new Error(`Harness exited ${server.exitCode}`);
    await delay(100);
  }
  assert.ok(result, 'Timed out waiting for installed-bundle smoke checks');
  assert.equal(result.ok, true, result.error);
  console.log(`Installed ${pack.filename}: ${result.checks.join(', ')}`);
} catch (error) {
  // Ephemeral host log can contain its localhost access token; redact before reporting.
  console.error(serverOutput.replace(/token=[\w-]+/g, 'token=[redacted]'));
  throw error;
} finally {
  if (server && server.exitCode === null) {
    const closed = new Promise(resolveClose => server.once('close', resolveClose));
    server.kill('SIGTERM');
    const kill = setTimeout(() => server.kill('SIGKILL'), 3000);
    kill.unref();
    await closed;
    clearTimeout(kill);
  }
  await rm(home, { recursive: true, force: true });
}
