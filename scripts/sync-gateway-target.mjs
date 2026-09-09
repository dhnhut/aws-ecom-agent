#!/usr/bin/env node
/**
 * Copy the REST API id produced by the infra/ CloudFormation stack into
 * agentcore.json.
 *
 * The AgentCore `apiGateway` target type references a REST API by id, and API
 * Gateway generates that id at create time — so it cannot be known statically.
 * This script closes that loop: deploy the stack first, then run this, then
 * `agentcore deploy`. It is idempotent and rewrites the file only on a change.
 *
 * Input is infra/cfn-outputs.json, written by scripts/deploy.sh from
 * `aws cloudformation describe-stacks --query "Stacks[0].Outputs"`.
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const OUTPUTS_FILE = join(REPO_ROOT, 'infra', 'cfn-outputs.json');
const CONFIG_FILE = join(REPO_ROOT, 'agentcore', 'agentcore.json');
const GATEWAY_NAME = 'EcomCS-gateway';
const TARGET_NAME = 'order-tracker';

function fail(message) {
  console.error(`sync-gateway-target: ${message}`);
  process.exit(1);
}

let outputs;
try {
  // CloudFormation returns outputs as [{OutputKey, OutputValue}, ...]; flatten
  // to a plain lookup so the rest of the script reads by name.
  outputs = Object.fromEntries(
    JSON.parse(readFileSync(OUTPUTS_FILE, 'utf8')).map(o => [o.OutputKey, o.OutputValue])
  );
} catch (error) {
  fail(`could not read ${OUTPUTS_FILE} (deploy the infra/ stack first): ${error.message}`);
}
if (!outputs.RestApiId || !outputs.StageName) {
  fail(`stack outputs are missing RestApiId/StageName`);
}

const config = JSON.parse(readFileSync(CONFIG_FILE, 'utf8'));
const gateway = config.agentCoreGateways?.find(g => g.name === GATEWAY_NAME);
if (!gateway) fail(`gateway "${GATEWAY_NAME}" not found in agentcore.json`);

const target = gateway.targets?.find(t => t.name === TARGET_NAME);
if (!target?.apiGateway) fail(`target "${TARGET_NAME}" (type apiGateway) not found on "${GATEWAY_NAME}"`);

if (target.apiGateway.restApiId === outputs.RestApiId && target.apiGateway.stage === outputs.StageName) {
  console.log(`sync-gateway-target: already up to date (restApiId=${outputs.RestApiId}, stage=${outputs.StageName})`);
  process.exit(0);
}

target.apiGateway.restApiId = outputs.RestApiId;
target.apiGateway.stage = outputs.StageName;

// agentcore.json is 2-space pretty-printed with no trailing newline (the CLI's own
// convention); match it so the diff stays limited to the fields we changed.
writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2));
console.log(`sync-gateway-target: set restApiId=${outputs.RestApiId}, stage=${outputs.StageName}`);
