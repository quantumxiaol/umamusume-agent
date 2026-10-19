import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const root = fileURLToPath(new URL('../../', import.meta.url));
// Export the project's base dependencies, excluding all optional TTS/LangChain extras.
// --locked also rejects a pyproject/lock mismatch instead of silently resolving new versions.
const output = execFileSync('uv', ['export', '--locked', '--no-dev', '--no-emit-project', '--no-header', '--no-annotate'],
  { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] });
const expected = '# Generated from uv.lock by npm run python:export. Do not edit.\n' + output;
const path = join(root, 'harness/requirements.txt');
if (process.argv.includes('--check')) {
  if (readFileSync(path, 'utf8') !== expected) throw new Error('Python requirements differ from uv.lock; run npm run python:export.');
  console.log('Python requirements match uv.lock.');
} else {
  writeFileSync(path, expected);
  console.log('Exported locked base Python dependencies (no optional audio extras).');
}
