param(
    [string]$Cluster = "sql-rag-cluster",
    [string]$TaskDefinition = "sql-rag-vector-builder",
    [Parameter(Mandatory = $true)][string]$Subnets,       # comma-separated public subnet ids (from stack output PublicSubnetIds)
    [Parameter(Mandatory = $true)][string]$SecurityGroup, # from stack output VectorBuilderSecurityGroupId
    [string]$Region = "eu-west-2"
)

$ErrorActionPreference = "Stop"

# Public subnet + a public IP (no NAT gateway in this VPC) is how the task
# reaches the internet for HuggingFace/Athena/S3 calls; the vector-builder
# security group has no inbound rules at all, so it isn't reachable from
# the internet despite having a public IP.
$subnetIds = $Subnets -split "," | ForEach-Object { $_.Trim() }
$networkConfig = @{
    awsvpcConfiguration = @{
        subnets        = $subnetIds
        securityGroups = @($SecurityGroup)
        assignPublicIp = "ENABLED"
    }
} | ConvertTo-Json -Compress -Depth 5

# Passing $networkConfig directly as a --network-configuration argument is
# unreliable on Windows: PowerShell hands native exes a single re-parsed
# command line, and embedded double quotes can get silently stripped before
# aws.exe ever sees them (producing invalid, unquoted JSON). Writing to a
# temp file and using `file://` sidesteps that entirely.
$tmpFile = New-TemporaryFile
try {
    [System.IO.File]::WriteAllText($tmpFile.FullName, $networkConfig, (New-Object System.Text.UTF8Encoding $false))

    Write-Host "Running vector-builder task on cluster $Cluster..."
    $rawResult = aws ecs run-task `
        --region $Region `
        --cluster $Cluster `
        --task-definition $TaskDefinition `
        --launch-type FARGATE `
        --network-configuration "file://$($tmpFile.FullName)" `
        --count 1
    if ($LASTEXITCODE -ne 0) { throw "aws ecs run-task failed (exit $LASTEXITCODE): $rawResult" }
} finally {
    Remove-Item $tmpFile.FullName -Force
}

$result = $rawResult | ConvertFrom-Json

if ($result.failures) {
    throw "run-task failed: $($result.failures | ConvertTo-Json)"
}

$taskArn = $result.tasks[0].taskArn
Write-Host "Task started: $taskArn"
Write-Host "Tail logs with: aws logs tail /ecs/sql-rag/vector-builder --follow --region $Region"
Write-Host "Check status with: aws ecs describe-tasks --cluster $Cluster --tasks $taskArn --region $Region --query 'tasks[0].lastStatus'"
Write-Host ""
Write-Host "Once it reaches STOPPED with exitCode 0, restart the retriever so it picks up the new vectorstore:"
Write-Host "  aws ecs update-service --cluster $Cluster --service sql-rag-retriever --force-new-deployment --region $Region"
