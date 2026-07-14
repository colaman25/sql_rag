param(
    [string]$EnvFile = (Join-Path $PSScriptRoot "..\..\.env"),
    [string]$Region = "eu-west-2"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $EnvFile)) { throw "Env file not found: $EnvFile" }

$vars = @{}
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $idx = $line.IndexOf("=")
        $key = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1).Trim()
        $vars[$key] = $value
    }
}

function Get-Required {
    param([string]$Name)
    if (-not $vars.ContainsKey($Name) -or [string]::IsNullOrWhiteSpace($vars[$Name])) {
        throw "Missing required value for $Name in $EnvFile"
    }
    return $vars[$Name]
}

$secretPayloads = [ordered]@{
    "sql-rag/huggingface"        = @{ HF_TOKEN = (Get-Required "HF_TOKEN") }
    "sql-rag/openrouter"         = @{ OPENROUTER_API_KEY = (Get-Required "OPENROUTER_API_KEY") }
    "sql-rag/athena-credentials" = @{
        ATHENA_AWS_ACCESS_KEY_ID     = (Get-Required "ATHENA_AWS_ACCESS_KEY_ID")
        ATHENA_AWS_SECRET_ACCESS_KEY = (Get-Required "ATHENA_AWS_SECRET_ACCESS_KEY")
    }
}

foreach ($secretId in $secretPayloads.Keys) {
    $json = $secretPayloads[$secretId] | ConvertTo-Json -Compress

    # Passing $json directly as a --secret-string argument is unreliable on
    # Windows: PowerShell hands native exes a single re-parsed command line,
    # and embedded double quotes can get silently stripped before aws.exe
    # ever sees them (producing invalid, unquoted JSON in Secrets Manager).
    # Writing to a temp file and using `file://` sidesteps that entirely.
    $tmpFile = New-TemporaryFile
    try {
        [System.IO.File]::WriteAllText($tmpFile.FullName, $json, (New-Object System.Text.UTF8Encoding $false))
        Write-Host "Updating secret: $secretId"
        aws secretsmanager put-secret-value --region $Region --secret-id $secretId --secret-string "file://$($tmpFile.FullName)" | Out-Null
    } finally {
        Remove-Item $tmpFile.FullName -Force
    }
}

Write-Host "All secrets updated in Secrets Manager ($Region)."
