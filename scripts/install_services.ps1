# CLAW Windows Service Installer
# Run this ONCE as Administrator to install everything.
# After this, CLAW starts automatically with Windows.

param(
    [string]$ClawDir   = "D:\claw",
    [string]$PythonExe = "",          # leave blank to auto-detect
    [string]$NodeExe   = "",          # leave blank to auto-detect
    [string]$NpmCmd    = ""           # leave blank to auto-detect
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Helpers ───────────────────────────────────────────────────────────────────

function Banner($msg) {
    Write-Host "`n===  $msg  ===" -ForegroundColor Cyan
}

function OK($msg)  { Write-Host "  [OK]  $msg" -ForegroundColor Green  }
function ERR($msg) { Write-Host "  [ERR] $msg" -ForegroundColor Red    }
function INFO($msg){ Write-Host "  [..] $msg"  -ForegroundColor Gray   }

function Require-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal $id
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        ERR "This script must be run as Administrator."
        ERR "Right-click PowerShell -> 'Run as Administrator' and try again."
        exit 1
    }
    OK "Running as Administrator"
}

function Find-Exe($hint, $name) {
    if ($hint -and (Test-Path $hint)) { return $hint }
    $found = Get-Command $name -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    ERR "Cannot find '$name'. Pass it with -${name}Exe parameter."
    exit 1
}

# ── Locate binaries ───────────────────────────────────────────────────────────

Require-Admin

$NSSM = Join-Path $ClawDir "scripts\nssm.exe"
if (-not (Test-Path $NSSM)) {
    ERR "nssm.exe not found at $NSSM"
    ERR "Run setup.ps1 first, or copy nssm.exe there manually."
    exit 1
}

# Python — prefer venv
$VenvPy = Join-Path $ClawDir ".venv\Scripts\python.exe"
if ($PythonExe -eq "" -and (Test-Path $VenvPy)) { $PythonExe = $VenvPy }
$PythonExe = Find-Exe $PythonExe "python"
OK "Python: $PythonExe"

# Node / npm
if ($NodeExe -eq "") {
    $n = Get-Command node -ErrorAction SilentlyContinue
    if ($n) { $NodeExe = $n.Source }
}
$NodeExe  = Find-Exe $NodeExe  "node"
OK "Node:   $NodeExe"

$NpmScript = Join-Path $ClawDir "web\node_modules\.bin\next.cmd"
if (-not (Test-Path $NpmScript)) {
    # fall back to global next
    $ng = Get-Command next -ErrorAction SilentlyContinue
    if ($ng) { $NpmScript = $ng.Source } else { $NpmScript = "npm" }
}

# ── Install / reinstall service helper ───────────────────────────────────────

function Install-NssmService {
    param($Name, $Exe, $Args, $WorkDir, $LogDir, $DisplayName, $EnvExtra = "")

    # Remove if already exists
    $existing = & $NSSM status $Name 2>&1
    if ($LASTEXITCODE -eq 0) {
        INFO "Removing existing '$Name' service…"
        & $NSSM stop    $Name 2>&1 | Out-Null
        & $NSSM remove  $Name confirm 2>&1 | Out-Null
    }

    # Create
    & $NSSM install $Name $Exe $Args
    & $NSSM set $Name AppDirectory      $WorkDir
    & $NSSM set $Name DisplayName       $DisplayName
    & $NSSM set $Name Description       "CLAW Coding Agent — $DisplayName"
    & $NSSM set $Name Start             SERVICE_AUTO_START

    # Logging
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    & $NSSM set $Name AppStdout  (Join-Path $LogDir "stdout.log")
    & $NSSM set $Name AppStderr  (Join-Path $LogDir "stderr.log")
    & $NSSM set $Name AppRotateFiles 1
    & $NSSM set $Name AppRotateBytes 5242880   # 5 MB

    # Restart on failure
    & $NSSM set $Name AppExit Default Restart
    & $NSSM set $Name AppRestartDelay 5000

    # Extra env vars — NSSM requires each VAR=VALUE on its own line
    if ($EnvExtra) {
        # Split on spaces that precede KEY=VALUE patterns, join with newlines
        $lines = ($EnvExtra -split '\s+(?=\w+=)') -join "`n"
        & $NSSM set $Name AppEnvironmentExtra $lines
    }

    OK "Installed service: $Name"
}

# ── 1. CLAW API service ───────────────────────────────────────────────────────

Banner "Installing claw-api service"

$UvicornArgs = "-m uvicorn api.main:app --host 0.0.0.0 --port 8765 --workers 1"
$ApiEnv = "PYTHONIOENCODING=utf-8 PYTHONUTF8=1"

Install-NssmService `
    -Name        "claw-api" `
    -Exe         $PythonExe `
    -Args        $UvicornArgs `
    -WorkDir     $ClawDir `
    -LogDir      (Join-Path $ClawDir "logs\api") `
    -DisplayName "CLAW API (FastAPI)" `
    -EnvExtra    $ApiEnv

# ── 2. CLAW Web service ───────────────────────────────────────────────────────

Banner "Installing claw-web service"

# Use start_web.cmd wrapper — it runs 'next build' then 'next start' so the
# service is self-healing after power cuts (no stale .next dir issues).
$StartWebCmd = Join-Path $ClawDir "scripts\start_web.cmd"
$CmdExe      = "$env:SystemRoot\System32\cmd.exe"
$WebArgs     = "/c `"$StartWebCmd`""

Install-NssmService `
    -Name        "claw-web" `
    -Exe         $CmdExe `
    -Args        $WebArgs `
    -WorkDir     (Join-Path $ClawDir "web") `
    -LogDir      (Join-Path $ClawDir "logs\web") `
    -DisplayName "CLAW Web Chat (Next.js)"

# ── 3. Start both services ────────────────────────────────────────────────────

Banner "Starting services"

Start-Service claw-api -ErrorAction SilentlyContinue
Start-Sleep -Seconds 4
$apiStatus = (Get-Service claw-api).Status
if ($apiStatus -eq "Running") { OK "claw-api  : $apiStatus" }
else {
    ERR "claw-api  : $apiStatus — check D:\claw\logs\api\stderr.log"
    Get-Content "D:\claw\logs\api\stderr.log" -Tail 8 -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
}

Start-Service claw-web -ErrorAction SilentlyContinue
Start-Sleep -Seconds 4
$webStatus = (Get-Service claw-web).Status
if ($webStatus -eq "Running") { OK "claw-web  : $webStatus" }
else {
    ERR "claw-web  : $webStatus — check D:\claw\logs\web\stderr.log"
    Get-Content "D:\claw\logs\web\stderr.log" -Tail 8 -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
}

# ── 4. Register tray app at startup ──────────────────────────────────────────

Banner "Registering tray app at user login"

$TrayScript = Join-Path $ClawDir "tray\claw_tray.py"
$RunKey     = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$TrayCmd    = "`"$PythonExe`" `"$TrayScript`""

Set-ItemProperty -Path $RunKey -Name "CLAW-Tray" -Value $TrayCmd
OK "Tray registered in HKCU Run key"

# Launch tray now without waiting
Start-Process -FilePath $PythonExe -ArgumentList "`"$TrayScript`"" -WindowStyle Hidden

# ── Done ──────────────────────────────────────────────────────────────────────

Banner "All done"
Write-Host @"

  Services installed and running:
    claw-api   http://localhost:8765      (FastAPI + agent)
    claw-web   http://localhost:3000      (Next.js chat UI)

  Both will start automatically at boot.

  System tray icon is now visible in the taskbar.
    Green  = everything healthy
    Amber  = API running but Ollama offline
    Red    = API offline

  Useful commands:
    Restart API :  Restart-Service claw-api
    Restart Web :  Restart-Service claw-web
    View API log:  Get-Content D:\claw\logs\api\stdout.log -Tail 50 -Wait
    View Web log:  Get-Content D:\claw\logs\web\stdout.log -Tail 50 -Wait

"@
