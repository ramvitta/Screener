# Starts ClickHouse (Docker), the FastAPI backend, and the Next.js frontend.
# Safe to re-run - skips anything already running.
# Windows equivalent of scripts/start.sh. Requires Windows PowerShell 5.1+.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$ROOT_DIR     = Split-Path -Parent $PSScriptRoot
$BACKEND_DIR  = Join-Path $ROOT_DIR 'backend'
$FRONTEND_DIR = Join-Path $ROOT_DIR 'frontend'
$RUN_DIR      = Join-Path $ROOT_DIR '.run'
$LOG_DIR      = Join-Path $ROOT_DIR 'logs'
$BACKEND_PORT  = 8000
$FRONTEND_PORT = 3001

New-Item -ItemType Directory -Force -Path $RUN_DIR, $LOG_DIR | Out-Null

function Log  { Write-Host "==> $($args -join ' ')" -ForegroundColor Blue }
function Warn { Write-Host "!! $($args -join ' ')" -ForegroundColor Yellow }

function Resolve-Docker {
    # Docker Desktop may not be on PATH (especially user-level installs).
    $cmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'),
        (Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe')
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

function Test-PortListening([int]$Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conn) { return $true }
    return $false
}

function Test-PidRunning([string]$PidFile) {
    if (-not (Test-Path $PidFile)) { return $false }
    $pidValue = Get-Content $PidFile -ErrorAction SilentlyContinue
    if (-not $pidValue) { return $false }
    $proc = Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue
    if ($proc) { return $true }
    return $false
}

# ---------- ClickHouse ----------
$docker = Resolve-Docker
if (-not $docker) {
    Warn "docker CLI not found on PATH or standard install locations - skipping ClickHouse."
    Warn "Backend will fall back to sample data."
} else {
    Log "Starting ClickHouse (Docker)..."
    Push-Location $BACKEND_DIR
    try {
        & $docker compose up -d
    } finally {
        Pop-Location
    }

    Log "Waiting for ClickHouse to accept connections..."
    $up = $false
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8123/ping' -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { $up = $true; break }
        } catch {}
        Start-Sleep -Seconds 1
    }
    if ($up) { Log "ClickHouse is up (http://127.0.0.1:8123)." }
    else { Warn "ClickHouse did not respond to /ping - check 'docker logs options_clickhouse'." }
}

# ---------- Backend ----------
$backendPid = Join-Path $RUN_DIR 'backend.pid'
if (Test-PidRunning $backendPid) {
    Log "Backend already running (pid $(Get-Content $backendPid))."
} elseif (Test-PortListening $BACKEND_PORT) {
    Warn "Port $BACKEND_PORT is already in use by another process - leaving it alone."
} else {
    Log "Starting backend (FastAPI) on port $BACKEND_PORT..."
    $venvPython = Join-Path $BACKEND_DIR '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        Log "No venv found - creating one and installing dependencies (first run only)."
        $py = $null
        $pyCmd = Get-Command python -ErrorAction SilentlyContinue
        if ($pyCmd) { $py = $pyCmd.Source }
        if (-not $py) {
            $pyCmd2 = Get-Command py -ErrorAction SilentlyContinue
            if ($pyCmd2) { $py = $pyCmd2.Source }
        }
        if (-not $py) { throw "python/py not found on PATH - cannot create venv." }
        & $py -m venv (Join-Path $BACKEND_DIR '.venv')
        & $venvPython -m pip install -q -r (Join-Path $BACKEND_DIR 'requirements.txt')
    }
    $stdout = Join-Path $LOG_DIR 'backend.stdout.log'
    $stderr = Join-Path $LOG_DIR 'backend.stderr.log'
    $proc = Start-Process -FilePath $venvPython `
        -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port',"$BACKEND_PORT" `
        -WorkingDirectory $BACKEND_DIR `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr `
        -WindowStyle Hidden -PassThru
    $proc.Id | Out-File -FilePath $backendPid -NoNewline
    Start-Sleep -Seconds 2
    if (Test-PidRunning $backendPid) {
        Log "Backend started (pid $($proc.Id)) - log: logs/backend.log"
    } else {
        Warn "Backend failed to start - check logs/backend.stdout.log"
    }
}

# ---------- Frontend ----------
$frontendPid = Join-Path $RUN_DIR 'frontend.pid'
if (Test-PidRunning $frontendPid) {
    Log "Frontend already running (pid $(Get-Content $frontendPid))."
} elseif (Test-PortListening $FRONTEND_PORT) {
    Warn "Port $FRONTEND_PORT is already in use by another process - leaving it alone."
} else {
    Log "Starting frontend (Next.js) on port $FRONTEND_PORT..."
    if (-not (Test-Path (Join-Path $FRONTEND_DIR 'node_modules'))) {
        Log "No node_modules found - running npm install (first run only)."
        Push-Location $FRONTEND_DIR
        try { & npm install } finally { Pop-Location }
    }
    $npm = $null
    $npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($npmCmd) { $npm = $npmCmd.Source }
    if (-not $npm) {
        $npmCmd2 = Get-Command npm -ErrorAction SilentlyContinue
        if ($npmCmd2) { $npm = $npmCmd2.Source }
    }
    if (-not $npm) { throw "npm not found on PATH." }
    $fout = Join-Path $LOG_DIR 'frontend.log'
    $ferr = Join-Path $LOG_DIR 'frontend.stderr.log'
    $proc = Start-Process -FilePath $npm `
        -ArgumentList 'run','dev','--','-p',"$FRONTEND_PORT" `
        -WorkingDirectory $FRONTEND_DIR `
        -RedirectStandardOutput $fout -RedirectStandardError $ferr `
        -WindowStyle Hidden -PassThru
    $proc.Id | Out-File -FilePath $frontendPid -NoNewline
    Start-Sleep -Seconds 3
    if (Test-PidRunning $frontendPid) {
        Log "Frontend started (pid $($proc.Id)) - log: logs/frontend.log"
    } else {
        Warn "Frontend failed to start - check logs/frontend.log"
    }
}

Write-Host ""
Log "Done."
Write-Host "  Frontend:   http://localhost:$FRONTEND_PORT"
Write-Host "  Backend:    http://localhost:$BACKEND_PORT/docs"
Write-Host "  ClickHouse: http://127.0.0.1:8123"
Write-Host ""
Write-Host "  Stop everything with:  powershell -ExecutionPolicy Bypass -File scripts/stop.ps1 --all"
