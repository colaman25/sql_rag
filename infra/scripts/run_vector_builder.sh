#!/usr/bin/env bash
set -euo pipefail

CLUSTER="${CLUSTER:-sql-rag-cluster}"
TASK_DEFINITION="${TASK_DEFINITION:-sql-rag-vector-builder}"
REGION="${REGION:-eu-west-2}"
SUBNETS="${1:?Usage: run_vector_builder.sh <comma-separated-public-subnet-ids> <security-group-id>}"
SECURITY_GROUP="${2:?Usage: run_vector_builder.sh <comma-separated-public-subnet-ids> <security-group-id>}"

IFS=',' read -ra SUBNET_ARR <<< "$SUBNETS"
SUBNET_JSON=$(printf '"%s",' "${SUBNET_ARR[@]}")
SUBNET_JSON="[${SUBNET_JSON%,}]"

# Public subnet + a public IP (no NAT gateway in this VPC) is how the task
# reaches the internet for HuggingFace/Athena/S3 calls; the vector-builder
# security group has no inbound rules at all, so it isn't reachable from
# the internet despite having a public IP.
NETWORK_CONFIG="{\"awsvpcConfiguration\":{\"subnets\":${SUBNET_JSON},\"securityGroups\":[\"${SECURITY_GROUP}\"],\"assignPublicIp\":\"ENABLED\"}}"

echo "Running vector-builder task on cluster ${CLUSTER}..."
TASK_ARN=$(aws ecs run-task \
  --region "$REGION" \
  --cluster "$CLUSTER" \
  --task-definition "$TASK_DEFINITION" \
  --launch-type FARGATE \
  --network-configuration "$NETWORK_CONFIG" \
  --count 1 \
  --query "tasks[0].taskArn" --output text)

echo "Task started: ${TASK_ARN}"
echo "Tail logs with: aws logs tail /ecs/sql-rag/vector-builder --follow --region ${REGION}"
echo "Check status with: aws ecs describe-tasks --cluster ${CLUSTER} --tasks ${TASK_ARN} --region ${REGION} --query 'tasks[0].lastStatus'"
echo ""
echo "Once it reaches STOPPED with exitCode 0, restart the retriever so it picks up the new vectorstore:"
echo "  aws ecs update-service --cluster ${CLUSTER} --service sql-rag-retriever --force-new-deployment --region ${REGION}"
