#!/usr/bin/env bash
set -euo pipefail

REGION="${1:-eu-west-2}"
REPOSITORY="${2:-sql-rag}"
TAG="${3:-latest}"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${REPOSITORY}:${TAG}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "Logging in to ECR: ${REGISTRY}"
aws ecr get-login-password --region "${REGION}" | docker login --username AWS --password-stdin "${REGISTRY}"

# Fargate's default runtime architecture is X86_64 -- force amd64 so this
# builds correctly even from an Apple Silicon / ARM machine.
echo "Building image ${IMAGE} (linux/amd64)"
docker build --platform linux/amd64 -t "${IMAGE}" "${ROOT_DIR}"

echo "Pushing image ${IMAGE}"
docker push "${IMAGE}"

echo "Done. Image URI: ${IMAGE}"
