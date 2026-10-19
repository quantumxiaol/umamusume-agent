import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const cache = mkdtempSync(join(tmpdir(), 'uma-pack-check-'));
try {
  const [pack] = JSON.parse(execFileSync(process.platform === 'win32' ? 'npm.cmd' : 'npm',
    ['pack', '--dry-run', '--json', '--ignore-scripts'], { cwd: root, encoding: 'utf8',
      env: { ...process.env, npm_config_cache: cache } }));
  const paths = new Set(pack.files.map(file => file.path));
  for (const path of ['package.json', 'harness/index.js', 'harness/client.js', 'harness/preset.js',
    'harness/cordis.patch.yml', 'harness/requirements.txt', 'src/umamusume_agent/harness/worker.py',
    'src/umamusume_agent/dialogue/runtime.py', 'docs/harness.md']) assert.ok(paths.has(path), `Missing ${path}`);
  const characters = readdirSync(join(root, 'characters'), { withFileTypes: true }).filter(item => item.isDirectory());
  for (const character of characters) assert.ok(paths.has(`characters/${character.name}/config.json`));
  for (const path of paths) {
    assert.ok(!/(^|\/)(\.env[^/]*|node_modules|__pycache__|\.git|outputs|\.venv|tests|frontend)(\/|$)/.test(path), `Unexpected ${path}`);
    assert.ok(!/\.(mp3|wav|png|pyc|log|tgz)$/.test(path), `Unexpected asset ${path}`);
  }
  assert.ok(pack.size < 5_000_000, 'Text-only plugin package unexpectedly exceeds 5 MB');
  console.log(`Package OK: ${paths.size} files, ${characters.length} characters, ${(pack.size / 1024).toFixed(0)} KiB compressed`);
} finally { rmSync(cache, { recursive: true, force: true }); }
