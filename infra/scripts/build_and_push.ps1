param(
    [string]$Region = "eu-west-2",
    [string]$Repository = "sql-rag",
    [string]$Tag = "latest"
)

$ErrorActionPreference = "Stop"

$account = (aws sts get-caller-identity --query Account --output text).Trim()
if (-not $account) { throw "Could not determine AWS account id. Is the AWS CLI configured?" }

$registry = "$account.dkr.ecr.$Region.amazonaws.com"
$image = "$registry/${Repository}:$Tag"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")

Write-Host "Logging in to ECR: $registry"
aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $registry
if ($LASTEXITCODE -ne 0) { throw "docker login failed" }

# Fargate's default runtime architecture is X86_64 -- force amd64 so this
# builds correctly even from an Apple Silicon / ARM machine.
Write-Host "Building image $image (linux/amd64)"
docker build --platform linux/amd64 -t $image $repoRoot
if ($LASTEXITCODE -ne 0) { throw "docker build failed" }

Write-Host "Pushing image $image"
docker push $image
if ($LASTEXITCODE -ne 0) { throw "docker push failed" }

Write-Host "Done. Image URI: $image"
