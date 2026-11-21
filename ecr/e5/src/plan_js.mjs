// Bridge to the pre-generated JavaScript planner of one arm (phase 3, route J).
// Same input and output contract as the frozen plan.mjs: reads {snapshot, projection} from stdin,
// prints {"node": <version>, "plan": <plan>} as one JSON line. No resolve hook and no fallback:
// if the arm directory lacks the generated .js files or package.json, it exits non-zero instead
// of loading the TypeScript originals. (The strip manifest and output hashes are checked by the
// runner before a batch starts, not by this bridge.)
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const armDir = process.argv[2];
const required = ['delivery-reconciliation.js', 'delivery-reconciliation-execution.js', 'package.json'];
for (const name of required) {
  if (!fs.existsSync(path.join(armDir, name))) {
    process.stderr.write(`plan_js: missing generated file ${name} in ${armDir}; refusing to fall back to TypeScript\n`);
    process.exit(4);
  }
}
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const planner = await import(pathToFileURL(path.join(armDir, 'delivery-reconciliation-execution.js')).href);
const plan = planner.planDeliveryReconciliationExecution({ snapshot: input.snapshot, projection: input.projection });
process.stdout.write(JSON.stringify({ node: process.version, plan }) + '\n');
