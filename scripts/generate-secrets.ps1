<#
.SYNOPSIS
    Generate / fill in secrets for memvault-os .env (Windows / PowerShell)

.DESCRIPTION
    Reads .env.example, fills empty secret slots with cryptographically random
    base64 values, preserves any user-supplied values in an existing .env, and
    writes back to .env. Idempotent — never overwrites an existing non-empty
    secret value.

.PARAMETER EnvPath
    Path to the target .env. Defaults to ".env" in current directory.

.PARAMETER ExamplePath
    Path to the source .env.example. Defaults to ".env.example".
#>

[CmdletBinding()]
param(
    [string]$EnvPath = ".env",
    [string]$ExamplePath = ".env.example"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function New-Secret {
    [CmdletBinding()]
    param([int]$Length = 24)
    # WHY hex (not base64): POSTGRES_PASSWORD / REDIS_PASSWORD are interpolated
    # into postgresql://user:PWD@host and redis://:PWD@host URLs in
    # infra/docker-compose.yml. base64 contains '+', '/', '=' which break URL
    # parsing in the user-info section. Hex is fully URL-safe ([0-9a-f]).
    $bytes = [byte[]]::new($Length)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    return -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

# Keys that must be auto-generated and their byte length (random bytes in).
# Matches scripts/generate-secrets.sh in length where applicable.
$SecretSpec = [ordered]@{
    'POSTGRES_PASSWORD'   = 24
    'REDIS_PASSWORD'      = 18
    'MEMVAULT_SECRET_KEY' = 32
    'LITELLM_MASTER_KEY'  = 24
    'MINIO_ROOT_PASSWORD' = 24
}

if (-not (Test-Path -LiteralPath $ExamplePath)) {
    Write-Error ".env.example not found at: $ExamplePath"
}

# Load existing .env (if any) into an ordered map of key → raw line.
$existing = [ordered]@{}
$existingValues = @{}
if (Test-Path -LiteralPath $EnvPath) {
    $lines = Get-Content -LiteralPath $EnvPath
    foreach ($line in $lines) {
        if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$') {
            $existing[$Matches[1]] = $line
            $existingValues[$Matches[1]] = $Matches[2]
        }
    }
}

# Walk through .env.example and produce the new .env contents line-by-line.
$exampleLines = Get-Content -LiteralPath $ExamplePath
$out = New-Object System.Collections.Generic.List[string]
$generated = New-Object System.Collections.Generic.List[string]
$preserved = New-Object System.Collections.Generic.List[string]

foreach ($line in $exampleLines) {
    if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$') {
        $key = $Matches[1]
        $defaultValue = $Matches[2]

        $existingValue = $null
        if ($existingValues.ContainsKey($key)) {
            $existingValue = $existingValues[$key]
        }

        if ($null -ne $existingValue -and $existingValue -ne '') {
            # Preserve user-supplied or already-generated value.
            $out.Add("$key=$existingValue")
            $preserved.Add($key) | Out-Null
            continue
        }

        if ($SecretSpec.Contains($key)) {
            $value = New-Secret -Length $SecretSpec[$key]
            $out.Add("$key=$value")
            $generated.Add($key) | Out-Null
            continue
        }

        # Non-secret with default in example → carry default through.
        $out.Add("$key=$defaultValue")
        continue
    }

    # Comment / blank line — copy verbatim.
    $out.Add($line)
}

# Append any extra keys present in existing .env but not in example
# (e.g. provider keys user added manually). Preserve them at the end.
foreach ($key in $existing.Keys) {
    $alreadyPresent = $false
    foreach ($outLine in $out) {
        if ($outLine -match "^$key=") { $alreadyPresent = $true; break }
    }
    if (-not $alreadyPresent) {
        $out.Add($existing[$key])
        $preserved.Add($key) | Out-Null
    }
}

# Write atomically.
$tempPath = "$EnvPath.tmp"
Set-Content -LiteralPath $tempPath -Value $out -Encoding UTF8
Move-Item -LiteralPath $tempPath -Destination $EnvPath -Force

Write-Host "[generate-secrets] wrote $EnvPath" -ForegroundColor Green
if ($generated.Count -gt 0) {
    Write-Host "  generated: $($generated -join ', ')" -ForegroundColor Cyan
}
if ($preserved.Count -gt 0) {
    Write-Host "  preserved: $($preserved -join ', ')" -ForegroundColor DarkGray
}
