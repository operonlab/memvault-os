<#
.SYNOPSIS
    memvault-os one-shot installer for Windows 11 + Docker Desktop + WSL2.

.DESCRIPTION
    Detects WSL2, Docker Desktop, optional NVIDIA GPU (via WSL), checks ports,
    clones the repo, generates secrets, prompts for at least one LLM provider
    key, pulls images, runs migrations, and opens the web UI.

    Mirrors scripts/install.sh feature-for-feature on the Windows track. MLX is
    not supported on Windows — only the GPU (vLLM) and CPU (ONNX) paths.

.PARAMETER InstallDir
    Where to clone the repo. Defaults to "$env:USERPROFILE\memvault-os".

.PARAMETER Repo
    Git URL to clone. Defaults to operonlab/memvault-os main.

.PARAMETER NonInteractive
    Skip interactive prompts (uses env-provided values + defaults). Useful for
    CI dry-runs.

.EXAMPLE
    irm https://raw.githubusercontent.com/operonlab/memvault-os/main/scripts/install.ps1 | iex

.EXAMPLE
    pwsh -File scripts/install.ps1 -InstallDir D:\memvault
#>

[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:USERPROFILE 'memvault-os'),
    [string]$Repo = 'https://github.com/operonlab/memvault-os.git',
    [switch]$NonInteractive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Pretty output helpers
# ---------------------------------------------------------------------------
function Write-Step    { param([string]$Msg) Write-Host "==> $Msg"   -ForegroundColor Cyan }
function Write-Ok      { param([string]$Msg) Write-Host "  ok  $Msg" -ForegroundColor Green }
function Write-Warn2   { param([string]$Msg) Write-Host "  !!  $Msg" -ForegroundColor Yellow }
function Write-Fail    { param([string]$Msg) Write-Host "  xx  $Msg" -ForegroundColor Red }

function Read-Default {
    param([string]$Prompt, [string]$Default = '')
    if ($NonInteractive) { return $Default }
    $suffix = if ($Default) { " [$Default]" } else { '' }
    $value = Read-Host "$Prompt$suffix"
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    return $value
}

function Read-Secret {
    param([string]$Prompt)
    if ($NonInteractive) { return '' }
    $secure = Read-Host -AsSecureString $Prompt
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Test-PortFree {
    param([int]$Port)
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        return ($null -eq $conn)
    } catch {
        # No listener found → port is free.
        return $true
    }
}

function Find-FreePort {
    param([int]$StartPort, [string]$Label)
    $port = $StartPort
    if (Test-PortFree -Port $port) { return $port }
    Write-Warn2 "Port $port ($Label) is in use."
    if ($NonInteractive) {
        # Bump to next free up to +20 then give up.
        for ($i = 1; $i -le 20; $i++) {
            $candidate = $port + $i
            if (Test-PortFree -Port $candidate) { return $candidate }
        }
        throw "No free port found near $StartPort for $Label"
    }
    while ($true) {
        $answer = Read-Host "Enter a different port for $Label"
        if ($answer -match '^\d+$') {
            $candidate = [int]$answer
            if ($candidate -gt 0 -and $candidate -lt 65536 -and (Test-PortFree -Port $candidate)) {
                return $candidate
            }
        }
        Write-Warn2 "Port $answer is invalid or already in use, try again."
    }
}

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host 'memvault-os installer (Windows)' -ForegroundColor Magenta
Write-Host '================================'
Write-Host ''

# 1. WSL2 detection ---------------------------------------------------------
Write-Step 'Checking WSL2'
$hasWsl = $false
try {
    $wslOut = & wsl.exe --list --verbose 2>&1
    if ($LASTEXITCODE -eq 0) {
        # `wsl --list --verbose` outputs UTF-16; if redirected, may have NULs.
        $wslText = ($wslOut -join "`n") -replace "`0", ''
        if ($wslText -match '\b2\b') {
            $hasWsl = $true
        }
    }
} catch {
    $hasWsl = $false
}
if (-not $hasWsl) {
    Write-Fail 'WSL2 not detected.'
    Write-Host '   Run as admin:  wsl --install -d Ubuntu' -ForegroundColor Yellow
    Write-Host '   Then reboot and re-run this installer.' -ForegroundColor Yellow
    throw 'WSL2 missing'
}
Write-Ok 'WSL2 distro present'

# 2. Docker Desktop ---------------------------------------------------------
Write-Step 'Checking Docker Desktop'
$hasDocker = $false
try {
    & docker version --format '{{.Server.Version}}' *>$null
    if ($LASTEXITCODE -eq 0) { $hasDocker = $true }
} catch {
    $hasDocker = $false
}
if (-not $hasDocker) {
    Write-Fail 'Docker Desktop is not running or not installed.'
    Write-Host '   Opening download page...' -ForegroundColor Yellow
    try { Start-Process 'https://www.docker.com/products/docker-desktop/' } catch { }
    throw 'Docker not available'
}
Write-Ok 'Docker daemon reachable'

# 3. GPU detection ----------------------------------------------------------
Write-Step 'Detecting NVIDIA GPU (via WSL)'
$hasGpu = $false
try {
    & wsl.exe -- nvidia-smi --query-gpu=name --format=csv,noheader *>$null
    if ($LASTEXITCODE -eq 0) { $hasGpu = $true }
} catch {
    $hasGpu = $false
}
if ($hasGpu) {
    Write-Ok 'NVIDIA GPU detected — will use vLLM embedding backend'
} else {
    Write-Warn2 'No GPU — falling back to ONNX embedding backend'
}

# 4. Port checks ------------------------------------------------------------
Write-Step 'Checking ports 8080 (api) and 3000 (web)'
$apiPort = Find-FreePort -StartPort 8080 -Label 'api'
$webPort = Find-FreePort -StartPort 3000 -Label 'web'
Write-Ok "api → $apiPort, web → $webPort"

# 5. Clone repo -------------------------------------------------------------
Write-Step 'Cloning repository'
if (Test-Path -LiteralPath (Join-Path $InstallDir '.git')) {
    Write-Ok "Repo already present at $InstallDir — skipping clone"
} else {
    if (Test-Path -LiteralPath $InstallDir) {
        $items = Get-ChildItem -LiteralPath $InstallDir -Force -ErrorAction SilentlyContinue
        if ($null -ne $items -and $items.Count -gt 0) {
            throw "$InstallDir exists and is not empty — aborting"
        }
    } else {
        New-Item -ItemType Directory -Path $InstallDir | Out-Null
    }
    & git clone --depth 1 $Repo $InstallDir
    if ($LASTEXITCODE -ne 0) { throw "git clone failed (exit $LASTEXITCODE)" }
    Write-Ok "Cloned to $InstallDir"
}

Push-Location -LiteralPath $InstallDir
try {

# 6. Generate .env ----------------------------------------------------------
Write-Step 'Generating .env'
$genScript = Join-Path -Path $InstallDir -ChildPath 'scripts\generate-secrets.ps1'
if (-not (Test-Path -LiteralPath $genScript)) {
    throw "generate-secrets.ps1 missing at $genScript"
}
& $genScript -EnvPath '.env' -ExamplePath '.env.example'
Write-Ok '.env ready'

# 7. Embedding backend ------------------------------------------------------
Write-Step 'Configuring embedding backend'
$composeFile = 'infra/docker-compose.yml'
$embedBackend = 'onnx'
if ($hasGpu) {
    $composeFile = 'infra/docker-compose.yml;infra/docker-compose.gpu.yml'
    $embedBackend = 'vllm_proxy'
}

# 8. LLM provider key (mandatory ≥1) ---------------------------------------
Write-Step 'Configuring LLM provider'
$providers = [ordered]@{
    '1' = @{ Name = 'OpenAI';    Key = 'OPENAI_API_KEY'    }
    '2' = @{ Name = 'Anthropic'; Key = 'ANTHROPIC_API_KEY' }
    '3' = @{ Name = 'Gemini';    Key = 'GEMINI_API_KEY'    }
    '4' = @{ Name = 'DeepSeek';  Key = 'DEEPSEEK_API_KEY'  }
}
$providerKey = $null
$providerVal = $null
if (-not $NonInteractive) {
    Write-Host '  Choose at least one provider:'
    foreach ($k in $providers.Keys) { Write-Host "    [$k] $($providers[$k].Name)" }
    $choice = Read-Default -Prompt '  Provider' -Default '1'
    if (-not $providers.Contains($choice)) { $choice = '1' }
    $providerKey = $providers[$choice].Key
    $providerVal = Read-Secret -Prompt "  $($providers[$choice].Name) API key"
    if ([string]::IsNullOrWhiteSpace($providerVal)) {
        Write-Warn2 'No API key entered — you will need to edit .env manually before LLM features work.'
    }
}

# 9. Patch .env -------------------------------------------------------------
Write-Step 'Patching .env with port + backend selections'
$envLines = Get-Content -LiteralPath '.env'
$updated = New-Object System.Collections.Generic.List[string]
$seen = @{}
function Set-EnvKey {
    param([string]$Key, [string]$Value)
    $seen[$Key] = $true
    $found = $false
    for ($i = 0; $i -lt $envLines.Count; $i++) {
        if ($envLines[$i] -match "^\s*$Key\s*=") {
            $envLines[$i] = "$Key=$Value"
            $found = $true
            break
        }
    }
    if (-not $found) {
        $envLines += "$Key=$Value"
    }
}
Set-EnvKey -Key 'API_PORT'      -Value $apiPort
Set-EnvKey -Key 'WEB_PORT'      -Value $webPort
Set-EnvKey -Key 'COMPOSE_FILE'  -Value $composeFile
Set-EnvKey -Key 'EMBED_BACKEND' -Value $embedBackend
if ($providerKey -and $providerVal) {
    Set-EnvKey -Key $providerKey -Value $providerVal
}
Set-Content -LiteralPath '.env' -Value $envLines -Encoding UTF8
Write-Ok '.env patched'

# 10. Pull + start ---------------------------------------------------------
Write-Step 'Pulling images (this may take a while)'
& docker compose pull
if ($LASTEXITCODE -ne 0) { throw 'docker compose pull failed' }

Write-Step 'Starting stack'
& docker compose up -d
if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed' }
Write-Ok 'Containers started'

# 11. Wait for api health --------------------------------------------------
Write-Step "Waiting for api on http://localhost:$apiPort/healthz"
$ready = $false
for ($i = 1; $i -le 60; $i++) {
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:$apiPort/healthz" -TimeoutSec 2 -ErrorAction Stop
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch {
        Start-Sleep -Seconds 2
    }
}
if (-not $ready) {
    Write-Warn2 'api did not become healthy within 120s — check `docker compose logs api`'
} else {
    Write-Ok 'api healthy'
}

# 12. LiteLLM smoke test ---------------------------------------------------
if ($providerKey -and $providerVal) {
    Write-Step 'Smoke-testing LiteLLM gateway'
    $body = @{
        model    = 'gpt-3.5-turbo'
        messages = @(@{ role = 'user'; content = 'ping' })
    } | ConvertTo-Json -Compress
    try {
        $headers = @{ 'Authorization' = "Bearer $((Get-Content .env | Select-String '^LITELLM_MASTER_KEY=' | ForEach-Object { ($_ -split '=',2)[1] }))" }
        $null = Invoke-RestMethod -Method Post -Uri 'http://localhost:4000/v1/chat/completions' `
                                  -Headers $headers -ContentType 'application/json' -Body $body -TimeoutSec 15
        Write-Ok 'LLM gateway responded'
    } catch {
        Write-Warn2 "LLM smoke test failed: $($_.Exception.Message)"
    }
}

# 13. Migration -----------------------------------------------------------
Write-Step 'Running database migrations'
& docker compose exec -T api alembic upgrade head
if ($LASTEXITCODE -ne 0) {
    Write-Warn2 'alembic upgrade failed — review `docker compose logs api`'
} else {
    Write-Ok 'Migrations applied'
}

# 14. Done ----------------------------------------------------------------
Write-Host ''
Write-Host 'memvault-os is up.' -ForegroundColor Green
Write-Host "  Web:  http://localhost:$webPort"
Write-Host "  API:  http://localhost:$apiPort"
Write-Host ''
try { Start-Process "http://localhost:$webPort" } catch { }

}
finally {
    Pop-Location
}
