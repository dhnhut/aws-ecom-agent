#!/usr/bin/env node
/**
 * Copy the ids produced by the infra/ CloudFormation stack into agentcore.json.
 *
 * Two AgentCore targets reference resources whose ids CloudFormation generates
 * at create time, so neither can be written statically:
 *
 *   - the `apiGateway` target references a REST API by id + stage;
 *   - the `connector` target references the Bedrock knowledge base by id.
 *
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
const API_TARGET_NAME = 'order-tracker';
const KB_TARGET_NAME = 'customer-support-kb';

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
for (const key of ['RestApiId', 'StageName', 'KnowledgeBaseId']) {
  if (!outputs[key]) fail(`stack outputs are missing ${key}`);
}

const config = JSON.parse(readFileSync(CONFIG_FILE, 'utf8'));
const gateway = config.agentCoreGateways?.find(g => g.name === GATEWAY_NAME);
if (!gateway) fail(`gateway "${GATEWAY_NAME}" not found in agentcore.json`);

function target(name, check, describe) {
  const found = gateway.targets?.find(t => t.name === name);
  if (!check(found)) fail(`target "${name}" (${describe}) not found on "${GATEWAY_NAME}"`);
  return found;
}

const before = JSON.stringify(config);

// ── REST API id + stage ──────────────────────────────────────────────────────
const apiTarget = target(API_TARGET_NAME, t => t?.apiGateway, 'type apiGateway');
apiTarget.apiGateway.restApiId = outputs.RestApiId;
apiTarget.apiGateway.stage = outputs.StageName;

// ── Knowledge base id ────────────────────────────────────────────────────────
// `agentcore add gateway-target --connector bedrock-knowledge-bases` writes the
// id into every connector configuration it generates — today Retrieve and
// AgenticRetrieveStream, at different depths. Rewriting every knowledgeBaseId
// key under the target keeps this working if the CLI adds or reshapes one.
const kbTarget = target(
  KB_TARGET_NAME,
  t => t?.connectorId === 'bedrock-knowledge-bases',
  'connectorId bedrock-knowledge-bases'
);

let rewritten = 0;
(function rewrite(node) {
  if (Array.isArray(node)) return node.forEach(rewrite);
  if (node === null || typeof node !== 'object') return;
  for (const [key, value] of Object.entries(node)) {
    if (key === 'knowledgeBaseId' && typeof value === 'string') {
      node[key] = outputs.KnowledgeBaseId;
      rewritten += 1;
    } else {
      rewrite(value);
    }
  }
})(kbTarget.configurations);

if (rewritten === 0) fail(`target "${KB_TARGET_NAME}" has no knowledgeBaseId to set`);

if (JSON.stringify(config) === before) {
  console.log(
    `sync-gateway-target: already up to date (restApiId=${outputs.RestApiId}, ` +
      `stage=${outputs.StageName}, knowledgeBaseId=${outputs.KnowledgeBaseId})`
  );
  process.exit(0);
}

// agentcore.json is 2-space pretty-printed with no trailing newline (the CLI's own
// convention); match it so the diff stays limited to the fields we changed.
writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2));
console.log(
  `sync-gateway-target: set restApiId=${outputs.RestApiId}, stage=${outputs.StageName}, ` +
    `knowledgeBaseId=${outputs.KnowledgeBaseId}`
);
