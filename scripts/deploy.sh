#!/usr/bin/env bash
#
# Full deploy, in dependency order.
#
# AgentCore calls API Gateway's GetExport synchronously when it creates the
# apiGateway target, and validates lambda:InvokeFunction synchronously when it
# creates the lambdaFunctionArn target. Both Lambdas and the deployed stage must
# therefore exist before `agentcore deploy` runs.
#
# Teardown is the reverse: `agentcore remove` + `agentcore deploy` to drop the
# gateway targets, then `aws s3 rm s3://<kb-bucket> --recursive` (CloudFormation
# cannot delete a bucket that still holds objects), then
# `aws cloudformation delete-stack --stack-name <STACK>`.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

STACK_NAME="EcomCS-CustomerSupportApi"
TEMPLATE="infra/template.yaml"
PACKAGED="infra/template.packaged.yaml"
OUTPUTS="infra/cfn-outputs.json"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region)}}"
BUCKET="ecomcs-cfn-artifacts-${ACCOUNT}-${REGION}"

# ── Artifact bucket ──────────────────────────────────────────────────────────
# `aws cloudformation package` needs somewhere to put the zipped Lambda source.
# Deterministic name, created once; this is the only thing the raw-CloudFormation
# path needs that a `cdk bootstrap`ed account would already have.
if ! aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null; then
  echo "==> Creating artifact bucket $BUCKET"
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION"
  fi
  aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
fi

# ── Package + deploy ─────────────────────────────────────────────────────────
# `package` zips each local Code: path, uploads it, and rewrites the template
# with the resulting S3 location. The packaged template is a build artifact.
echo "==> Packaging Lambda source from lambda/"
aws cloudformation package \
  --template-file "$TEMPLATE" \
  --s3-bucket "$BUCKET" \
  --s3-prefix "$STACK_NAME" \
  --output-template-file "$PACKAGED"

echo "==> Deploying $STACK_NAME (Lambdas + CustomerSupportGateway REST API)"
aws cloudformation deploy \
  --template-file "$PACKAGED" \
  --stack-name "$STACK_NAME" \
  --capabilities CAPABILITY_NAMED_IAM \
  --tags "agentcore:project-name=Ecom" \
  --no-fail-on-empty-changeset

# ── Knowledge base data source ───────────────────────────────────────────────
# CloudFormation creates the bucket and registers the data source, but it can
# neither put objects nor ingest them, so both steps live here. `s3 cp` is
# idempotent and start-ingestion-job re-reads the whole prefix, so re-running
# deploy.sh simply re-syncs the catalog.
KB_BUCKET="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='KnowledgeBaseBucketName'].OutputValue" --output text)"
KB_ID="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='KnowledgeBaseId'].OutputValue" --output text)"
KB_DATA_SOURCE_ID="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='KnowledgeBaseDataSourceId'].OutputValue" --output text)"

echo "==> Uploading data/product_catalog.txt to s3://$KB_BUCKET"
aws s3 cp data/product_catalog.txt "s3://$KB_BUCKET/product_catalog.txt"

echo "==> Syncing knowledge base $KB_ID (data source $KB_DATA_SOURCE_ID)"
INGESTION_JOB_ID="$(aws bedrock-agent start-ingestion-job \
  --knowledge-base-id "$KB_ID" \
  --data-source-id "$KB_DATA_SOURCE_ID" \
  --description "scripts/deploy.sh $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --query 'ingestionJob.ingestionJobId' --output text)"

# Poll rather than fire-and-forget: a failed ingestion leaves the knowledge base
# queryable but empty, which is far harder to spot later than a failed deploy.
while :; do
  INGESTION_STATUS="$(aws bedrock-agent get-ingestion-job \
    --knowledge-base-id "$KB_ID" \
    --data-source-id "$KB_DATA_SOURCE_ID" \
    --ingestion-job-id "$INGESTION_JOB_ID" \
    --query 'ingestionJob.status' --output text)"
  case "$INGESTION_STATUS" in
    COMPLETE) echo "    ingestion complete"; break ;;
    FAILED|STOPPED)
      echo "    ingestion $INGESTION_STATUS" >&2
      aws bedrock-agent get-ingestion-job \
        --knowledge-base-id "$KB_ID" \
        --data-source-id "$KB_DATA_SOURCE_ID" \
        --ingestion-job-id "$INGESTION_JOB_ID" \
        --query 'ingestionJob.failureReasons' --output json >&2
      exit 1 ;;
    *) echo "    ingestion $INGESTION_STATUS ..."; sleep 10 ;;
  esac
done

# ── Force a fresh stage deployment ───────────────────────────────────────────
# AWS::ApiGateway::Deployment is immutable, so CloudFormation will not redeploy
# the stage just because a Method changed. This guarantees the stage serves the
# routes AgentCore is about to export, whether or not the Deployment logical ID
# in template.yaml was bumped. See the comment on ApiDeploymentV1.
REST_API_ID="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='RestApiId'].OutputValue" --output text)"
STAGE="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='StageName'].OutputValue" --output text)"

echo "==> Redeploying $REST_API_ID to stage $STAGE"
aws apigateway create-deployment \
  --rest-api-id "$REST_API_ID" \
  --stage-name "$STAGE" \
  --description "scripts/deploy.sh $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --no-cli-pager >/dev/null

# ── Hand the generated ids to AgentCore ──────────────────────────────────────
# The REST API id and the knowledge base id are both created by the stack, so
# agentcore.json cannot carry them statically.
aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs" --output json > "$OUTPUTS"

echo "==> Syncing restApiId and knowledgeBaseId into agentcore.json"
node scripts/sync-gateway-target.mjs

echo "==> Validating and deploying the AgentCore project"
agentcore validate
agentcore deploy --yes
