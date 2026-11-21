// Pre-generate JavaScript from the frozen TypeScript classifier/planner (phase 3, route J).
//
// Uses only the running Node's built-in module.stripTypeScriptTypes (mode "strip": types are
// replaced by whitespace, no code is generated or transformed). The .ts originals are read
// only and kept. Output: <out>/<arm>/{delivery-reconciliation.js, delivery-reconciliation-execution.js,
// package.json {"type":"module"}} so that the planner's existing import specifier
// "./delivery-reconciliation.js" resolves to the generated file without any rewriting.
// A manifest records tool, Node version and binary hash, options, module-path handling and all
// input/output hashes. Any failure exits non-zero and writes a FAILED manifest; there is no
// fallback to the TypeScript route.
//
// Usage: node strip_ts.mjs <arms_dir> <out_dir> <arm> [<arm> ...]
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { stripTypeScriptTypes } from 'node:module';

const FILES = ['delivery-reconciliation.ts', 'delivery-reconciliation-execution.ts'];
const OPTIONS = { mode: 'strip' };
const PACKAGE_JSON = '{"type":"module"}\n';
const sha = (buf) => crypto.createHash('sha256').update(buf).digest('hex');

const [armsDir, outDir, ...arms] = process.argv.slice(2);
if (!armsDir || !outDir || arms.length === 0) {
  console.error('usage: node strip_ts.mjs <arms_dir> <out_dir> <arm> [<arm> ...]');
  process.exit(2);
}
if (fs.existsSync(outDir)) {
  console.error(`refusing to overwrite existing output directory ${outDir}`);
  process.exit(3);
}
const manifest = {
  tool: 'strip_ts.mjs', tool_sha256: sha(fs.readFileSync(new URL(import.meta.url))),
  api: 'node:module.stripTypeScriptTypes', options: OPTIONS,
  node_version: process.version, node_execpath: process.execPath,
  node_binary_sha256: sha(fs.readFileSync(process.execPath)),
  module_path_handling: 'output .js files named like the specifiers already used by the source ("./delivery-reconciliation.js"); a package.json {"type":"module"} in each arm directory makes .js load as ES modules; no specifier is rewritten',
  package_json: PACKAGE_JSON, package_json_sha256: sha(Buffer.from(PACKAGE_JSON)),
  arms: {}, status: 'IN_PROGRESS', utc: new Date().toISOString(),
};
fs.mkdirSync(outDir);
try {
  for (const arm of arms) {
    const src = path.join(armsDir, arm);
    const dst = path.join(outDir, arm);
    fs.mkdirSync(dst);
    const entry = { inputs: {}, outputs: {} };
    for (const name of FILES) {
      const raw = fs.readFileSync(path.join(src, name));
      const js = stripTypeScriptTypes(raw.toString('utf8'), OPTIONS);
      if (typeof js !== 'string' || js.length !== raw.toString('utf8').length) {
        throw new Error(`${arm}/${name}: strip output length differs from input (mode strip must preserve positions)`);
      }
      const outName = name.replace(/\.ts$/, '.js');
      fs.writeFileSync(path.join(dst, outName), js, { flag: 'wx' });
      entry.inputs[name] = sha(raw);
      entry.outputs[outName] = sha(Buffer.from(js, 'utf8'));
    }
    fs.writeFileSync(path.join(dst, 'package.json'), PACKAGE_JSON, { flag: 'wx' });
    entry.outputs['package.json'] = manifest.package_json_sha256;
    manifest.arms[arm] = entry;
  }
  manifest.status = 'COMPLETED';
} catch (error) {
  manifest.status = 'FAILED';
  manifest.error = String(error && error.stack || error);
  fs.writeFileSync(path.join(outDir, 'strip-manifest.json'), JSON.stringify(manifest, null, 1) + '\n', { flag: 'wx' });
  console.error(manifest.error);
  process.exit(1);
}
fs.writeFileSync(path.join(outDir, 'strip-manifest.json'), JSON.stringify(manifest, null, 1) + '\n', { flag: 'wx' });
console.log(JSON.stringify({ status: manifest.status, arms: Object.keys(manifest.arms), node: process.version }));
