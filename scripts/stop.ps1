# Stops the frontend and backend started by scripts/start.ps1.
# ClickHouse (Docker) is left running by default - pass --all to stop it too.
# Windows equivalent of scripts/stop.sh. Requires Windows PowerShell 5.1+.
[CmdletBinding()]
param([switch]$All)

$ErrorActionPreference = 'SilentlyContinue'

$ROOT_DIR     = Split-Path -Parent $PSScriptRoot
$BACKEND_DIR  = Join-Path $ROOT_DIR 'backend'
$RUN_DIR      = Join-Path $ROOT_DIR '.run'
$BACKEND_PORT  = 8000
$FRONTEND_PORT = 3001

function Log  { Write-Host "==> $($args -join ' ')" -ForegroundColor Blue }
function Warn { Write-Host "!! $($args -join ' ')" -ForegroundColor Yellow }

function Stop-ByPidFile([string]$Name, [string]$PidFile, [int]$Port) {
    if (Test-Path $PidFile) {
        $pidValue = Get-Content $PidFile
        if ($pidValue) {
            $proc = Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue
            if ($proc) {
                Log "Stopping $Name (pid $pidValue)..."
                Stop-Process -Id ([int]$pidValue) -Force
            }
        }
        Remove-Item $PidFile -Force
    }

    # Fallback: kill anything still bound to the port (e.g. a dev server that
    # spawned a child process under a different pid).
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conns) {
        $leftover = $conns | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($p in $leftover) {
            Warn "Port $Port still in use by pid $p - stopping."
            Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        }
    }
}

Stop-ByPidFile 'frontend' (Join-Path $RUN_DIR 'frontend.pid') $FRONTEND_PORT
Stop-ByPidFile 'backend'  (Join-Path $RUN_DIR 'backend.pid')  $BACKEND_PORT

if ($All) {
    Log "Stopping ClickHouse (Docker)..."
    $dockerExe = $null
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($dockerCmd) {
        $dockerExe = $dockerCmd.Source
    } else {
        $localDocker = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'
        $progDocker  = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'
        if (Test-Path $localDocker) { $dockerExe = $localDocker }
        elseif (Test-Path $progDocker) { $dockerExe = $progDocker }
    }
    if ($dockerExe) {
        Push-Location $BACKEND_DIR
        try { & $dockerExe compose down } finally { Pop-Location }
    } else {
        Warn "docker CLI not found - could not stop ClickHouse."
    }
} else {
    Log "Leaving ClickHouse running (pass --all to stop it too)."
}

Log "Done."
