// Bridge to the frozen classifier + execution planner of one arm (ECR experiment 2).
// Reads {snapshot, projection} on stdin, prints the planner's plan as JSON.
// The planner imports "./delivery-reconciliation.js"; the frozen source is the
// sibling .ts file, so a resolve hook redirects that one specifier. No source
// byte is modified, and nothing else is read or written.
import fs from 'node:fs';
import {registerHooks} from 'node:module';
import {pathToFileURL} from 'node:url';

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === './delivery-reconciliation.js' && context.parentURL?.endsWith('/delivery-reconciliation-execution.ts')) {
      return nextResolve('./delivery-reconciliation.ts', context);
    }
    return nextResolve(specifier, context);
  },
});

const armDir = process.argv[2];
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const planner = await import(pathToFileURL(`${armDir}/delivery-reconciliation-execution.ts`).href);
const plan = planner.planDeliveryReconciliationExecution({snapshot: input.snapshot, projection: input.projection});
process.stdout.write(JSON.stringify({node: process.version, plan}) + '\n');
