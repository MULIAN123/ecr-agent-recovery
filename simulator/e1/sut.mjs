// Thin transport wrapper around the exact frozen TypeScript interpreter.
import fs from 'node:fs';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const inputBytes = fs.readFileSync(config.input);
const snapshot = JSON.parse(inputBytes);
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const before = JSON.stringify(snapshot);
const access = config.private_paths.map(path => {
  try { fs.readFileSync(path); return {path, observed:'ALLOWED'}; }
  catch (error) { return {path, observed:error.code==='EACCES' ? 'DENIED':'ERROR', code:error.code}; }
});
if (access.some(row => row.observed!=='DENIED')) throw new Error('Private input access boundary failed');
const {reconcileDeliveryState} = await import(pathToFileURL(config.module).href);
const result = reconcileDeliveryState(snapshot);
console.log(JSON.stringify({run_id:config.run_id, scenario_id:config.scenario_id,
  arm:config.arm, pid:process.pid, uid:process.getuid(), gid:process.getgid(),
  groups:process.getgroups(), node:process.version, input_sha256:hash(inputBytes),
  snapshot_before_sha256:hash(before), snapshot_after_sha256:hash(JSON.stringify(snapshot)),
  access, result, scope:'STATIC_EVIDENCE_CORE_NO_EXTERNAL_EXECUTOR'}));
