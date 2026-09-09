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
# gateway targets, then `aws cloudformation delete-stack --stack-name <STACK>`.
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
  --capabilities CAPABILITY_IAM \
  --tags "agentcore:project-name=Ecom" \
  --no-fail-on-empty-changeset

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

# ── Hand the generated REST API id to AgentCore ──────────────────────────────
aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs" --output json > "$OUTPUTS"

echo "==> Syncing restApiId into agentcore.json"
node scripts/sync-gateway-target.mjs

echo "==> Validating and deploying the AgentCore project"
agentcore validate
agentcore deploy --yes
