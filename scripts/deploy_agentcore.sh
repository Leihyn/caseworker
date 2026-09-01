#!/usr/bin/env bash
# Deploy Caseworker to Amazon Bedrock AgentCore Runtime.
#
# Prerequisites:
#   - AWS credentials with Bedrock + AgentCore permissions (aws configure / SSO)
#   - uv (https://docs.astral.sh/uv/)
#   - Docker or Finch running (the starter toolkit builds an ARM64 container)
#
# Usage: ./scripts/deploy_agentcore.sh [region]

set -euo pipefail

REGION="${1:-${AWS_REGION:-us-east-1}}"
BACKEND_DIR="$(cd "$(dirname "$0")/../backend" && pwd)"
cd "$BACKEND_DIR"

echo "==> Verifying AWS credentials"
uv run --extra dev python - <<'PY'
import boto3, sys
try:
    ident = boto3.client("sts").get_caller_identity()
    print(f"    Account {ident['Account']} as {ident['Arn']}")
except Exception as exc:
    sys.exit(f"    No usable AWS credentials: {exc}\n    Run `aws configure` or set AWS_* env vars first.")
PY

echo "==> Generating requirements.txt for the AgentCore container"
uv export --no-dev --extra agentcore --no-hashes --format requirements-txt > requirements.txt

echo "==> Configuring AgentCore runtime (entrypoint: agentcore_app.py)"
uvx --from bedrock-agentcore-starter-toolkit --with "botocore[crt]" agentcore configure \
  --entrypoint agentcore_app.py \
  --name caseworker \
  --region "$REGION" \
  --non-interactive

echo "==> Launching to AgentCore Runtime"
uvx --from bedrock-agentcore-starter-toolkit --with "botocore[crt]" agentcore launch

cat <<'EOF'

==> Deployed. Invoke it with:
  uvx --from bedrock-agentcore-starter-toolkit agentcore invoke \
    '{"action": "analyze", "case_id": "CW-1042"}'

For durable cloud state, set these on the runtime before invoking:
  CASEWORKER_REPOSITORY=dynamodb   CASEWORKER_TABLE=caseworker-cases
  CASEWORKER_SESSIONS_BUCKET=<your-s3-bucket>
EOF
