param(
    [string]$Cluster = "sql-rag-cluster",
    [string]$ServiceName = "sql-rag-streamlit",
    [string]$Region = "eu-west-2"
)

$ErrorActionPreference = "Stop"

$taskArns = (aws ecs list-tasks --region $Region --cluster $Cluster --service-name $ServiceName --query "taskArns" --output text).Trim()
if (-not $taskArns) { throw "No running tasks found for service $ServiceName in cluster $Cluster" }
$taskArn = ($taskArns -split "\s+")[0]

$eniId = (aws ecs describe-tasks --region $Region --cluster $Cluster --tasks $taskArn `
    --query "tasks[0].attachments[0].details[?name=='networkInterfaceId'].value | [0]" --output text).Trim()

$publicIp = (aws ec2 describe-network-interfaces --region $Region --network-interface-ids $eniId `
    --query "NetworkInterfaces[0].Association.PublicIp" --output text).Trim()

if (-not $publicIp -or $publicIp -eq "None") {
    throw "Task has no public IP yet -- it may still be starting. Retry in a few seconds."
}

Write-Host "Streamlit UI: http://${publicIp}:8501"
