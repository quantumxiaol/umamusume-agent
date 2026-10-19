import { build } from 'esbuild';
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const check = process.argv.includes('--check');
const banner = '// Generated from harness/src by npm run build. Do not edit.\n';
const common = { absWorkingDir: root, write: false, target: 'es2022', charset: 'utf8', logLevel: 'warning', legalComments: 'inline' };
const server = await build({ ...common, entryPoints: ['index', 'model', 'worker', 'preset', 'state', 'protocol'].map(name => `harness/src/${name}.ts`),
  outdir: 'harness', platform: 'node', format: 'esm', banner: { js: banner.trimEnd() } });
const browser = await build({ ...common, entryPoints: ['harness/src/client.tsx'], bundle: true, platform: 'browser', format: 'cjs',
  outfile: 'harness/client.js', external: ['react'], minify: true,
  // Preserve dependency template contents using escaped strings in generated code.
  supported: { 'template-literal': false },
  banner: { js: banner + 'window.__ModuleLoader__.load({ id: "dsh-umamusume-agent", factory: (require) => {\nconst module = { exports: {} };' },
  footer: { js: 'return module.exports;\n} });' } });
for (const output of [...server.outputFiles, ...browser.outputFiles]) {
  if (check) {
    const current = await readFile(output.path, 'utf8').catch(() => '');
    if (current !== output.text) throw new Error(`Stale generated file: ${output.path}. Run npm run build and include the generated JS.`);
  } else await writeFile(output.path, output.contents);
}
console.log(check ? 'Committed JS matches the TS/TSX build.' : `Built ${server.outputFiles.length + browser.outputFiles.length} Harness entries.`);
