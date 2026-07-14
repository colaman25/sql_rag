#!/usr/bin/env bash
set -euo pipefail

CLUSTER="${1:-sql-rag-cluster}"
SERVICE_NAME="${2:-sql-rag-streamlit}"
REGION="${3:-eu-west-2}"

TASK_ARN=$(aws ecs list-tasks --region "$REGION" --cluster "$CLUSTER" --service-name "$SERVICE_NAME" --query "taskArns[0]" --output text)
if [[ -z "$TASK_ARN" || "$TASK_ARN" == "None" ]]; then
  echo "No running tasks found for service ${SERVICE_NAME} in cluster ${CLUSTER}" >&2
  exit 1
fi

ENI_ID=$(aws ecs describe-tasks --region "$REGION" --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  --query "tasks[0].attachments[0].details[?name=='networkInterfaceId'].value | [0]" --output text)

PUBLIC_IP=$(aws ec2 describe-network-interfaces --region "$REGION" --network-interface-ids "$ENI_ID" \
  --query "NetworkInterfaces[0].Association.PublicIp" --output text)

if [[ -z "$PUBLIC_IP" || "$PUBLIC_IP" == "None" ]]; then
  echo "Task has no public IP yet -- it may still be starting. Retry in a few seconds." >&2
  exit 1
fi

echo "Streamlit UI: http://${PUBLIC_IP}:8501"
