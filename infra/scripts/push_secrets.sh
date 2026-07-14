#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.env}"
REGION="${2:-eu-west-2}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

require() {
  local name="$1"
  local value="${!name:-}"
  if [[ -z "$value" ]]; then
    echo "Missing required value for $name in $ENV_FILE" >&2
    exit 1
  fi
  printf '%s' "$value"
}

HF_TOKEN_VAL=$(require HF_TOKEN)
OPENROUTER_API_KEY_VAL=$(require OPENROUTER_API_KEY)
ATHENA_AWS_ACCESS_KEY_ID_VAL=$(require ATHENA_AWS_ACCESS_KEY_ID)
ATHENA_AWS_SECRET_ACCESS_KEY_VAL=$(require ATHENA_AWS_SECRET_ACCESS_KEY)

put() {
  local secret_id="$1"
  local json="$2"
  echo "Updating secret: $secret_id"
  aws secretsmanager put-secret-value --region "$REGION" --secret-id "$secret_id" --secret-string "$json" >/dev/null
}

put "sql-rag/huggingface" "$(printf '{"HF_TOKEN":"%s"}' "$HF_TOKEN_VAL")"
put "sql-rag/openrouter" "$(printf '{"OPENROUTER_API_KEY":"%s"}' "$OPENROUTER_API_KEY_VAL")"
put "sql-rag/athena-credentials" "$(printf '{"ATHENA_AWS_ACCESS_KEY_ID":"%s","ATHENA_AWS_SECRET_ACCESS_KEY":"%s"}' "$ATHENA_AWS_ACCESS_KEY_ID_VAL" "$ATHENA_AWS_SECRET_ACCESS_KEY_VAL")"

echo "All secrets updated in Secrets Manager (${REGION})."
