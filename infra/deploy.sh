#!/usr/bin/env bash
#
# Packages lambda/order_tracker and deploys infra/ecomcs-extend-infra.yaml.
#
# The Lambda source is 6KB, past the 4096-byte ceiling on inline `Code.ZipFile`,
# so plain CloudFormation has to read it from S3 — hence the zip + upload here.
#
# Env overrides: STACK_NAME, STAGE_NAME, PROJECT_NAME, AWS_REGION,
#                ARTIFACT_BUCKET, API_AUTH_TYPE
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"

STACK_NAME="ecomcs-extended-infra"
STAGE_NAME="prod"
PROJECT_NAME="EcomCS-Extra-Infra"
API_AUTH_TYPE="NONE"

# Default to the region in agentcore/aws-targets.json so this stack lands
# alongside the AgentCore stack rather than in the CLI's default region.
if [[ -z "${AWS_REGION:-}" ]]; then
  AWS_REGION="$(python3 -c "import json,sys; print(json.load(open('$ROOT/agentcore/aws-targets.json'))[0]['region'])" 2>/dev/null || echo us-east-1)"
fi
export AWS_REGION

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ARTIFACT_BUCKET="ecom-agent-extended-artifacts-${ACCOUNT_ID}-${AWS_REGION}"

BUILD_DIR="$HERE/.build"
SRC_DIR="$ROOT/lambda/order_tracker"
ZIP_PATH="$BUILD_DIR/order_tracker.zip"

echo "==> account $ACCOUNT_ID  region $AWS_REGION  stack $STACK_NAME  stage $STAGE_NAME"

# ── Package ────────────────────────────────────────────────────────────────
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
# -X drops extra file attributes and -j flattens paths, so an unchanged source
# yields a byte-identical zip and therefore a stable content hash.
( cd "$SRC_DIR" && zip -q -X -j "$ZIP_PATH" order_tracker.py )

CODE_HASH="$(python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest()[:16])" "$ZIP_PATH")"
CODE_S3_KEY="order-tracker/${CODE_HASH}.zip"
echo "==> packaged $(basename "$ZIP_PATH") -> s3://${ARTIFACT_BUCKET}/${CODE_S3_KEY}"

# ── Artifact bucket ────────────────────────────────────────────────────────
if ! aws s3api head-bucket --bucket "$ARTIFACT_BUCKET" >/dev/null 2>&1; then
  echo "==> creating artifact bucket $ARTIFACT_BUCKET"
  if [[ "$AWS_REGION" == "us-east-1" ]]; then
    # us-east-1 rejects a LocationConstraint; every other region requires one.
    aws s3api create-bucket --bucket "$ARTIFACT_BUCKET" >/dev/null
  else
    aws s3api create-bucket --bucket "$ARTIFACT_BUCKET" \
      --create-bucket-configuration "LocationConstraint=$AWS_REGION" >/dev/null
  fi
  aws s3api put-public-access-block --bucket "$ARTIFACT_BUCKET" \
    --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" >/dev/null
fi

aws s3 cp "$ZIP_PATH" "s3://${ARTIFACT_BUCKET}/${CODE_S3_KEY}" --only-show-errors

# ── Deploy ─────────────────────────────────────────────────────────────────
aws cloudformation deploy \
  --template-file "$HERE/ecomcs-extended-infra.yaml" \
  --stack-name "$STACK_NAME" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    "ProjectName=$PROJECT_NAME" \
    "StageName=$STAGE_NAME" \
    "CodeS3Bucket=$ARTIFACT_BUCKET" \
    "CodeS3Key=$CODE_S3_KEY" \
    "ApiAuthorizationType=$API_AUTH_TYPE"

echo
echo "==> outputs"
aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output table
