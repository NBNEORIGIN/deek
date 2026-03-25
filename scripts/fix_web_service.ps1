# Quick fix for claw-web service — run as Administrator
param([string]$ClawDir = "D:\claw")

$NSSM      = Join-Path $ClawDir "scripts\nssm.exe"
$CmdExe    = "$env:SystemRoot\System32\cmd.exe"
$StartWeb  = Join-Path $ClawDir "scripts\start_web.cmd"
$WebDir    = Join-Path $ClawDir "web"
$LogDir    = Join-Path $ClawDir "logs\web"

Write-Host "Cmd    : $CmdExe"
Write-Host "Script : $StartWeb"

# Remove old service
& $NSSM stop   claw-web 2>&1 | Out-Null
& $NSSM remove claw-web confirm 2>&1 | Out-Null

# Recreate using cmd.exe /c start_web.cmd — auto-builds before starting
& $NSSM install claw-web $CmdExe "/c `"$StartWeb`""
& $NSSM set claw-web AppDirectory     $WebDir
& $NSSM set claw-web DisplayName      "CLAW Web Chat (Next.js)"
& $NSSM set claw-web Start            SERVICE_AUTO_START
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
& $NSSM set claw-web AppStdout  (Join-Path $LogDir "stdout.log")
& $NSSM set claw-web AppStderr  (Join-Path $LogDir "stderr.log")
& $NSSM set claw-web AppRotateFiles 1
& $NSSM set claw-web AppRotateBytes 5242880
& $NSSM set claw-web AppExit    Default Restart
& $NSSM set claw-web AppRestartDelay 5000

# Start it
Start-Service claw-web -ErrorAction SilentlyContinue
Start-Sleep -Seconds 5

$status = (Get-Service claw-web).Status
if ($status -eq "Running") {
    Write-Host "[OK]  claw-web: Running  ->  http://localhost:3000" -ForegroundColor Green
} else {
    Write-Host "[ERR] claw-web: $status" -ForegroundColor Red
    Write-Host "stderr:" -ForegroundColor Yellow
    Get-Content (Join-Path $LogDir "stderr.log") -Tail 15 -ErrorAction SilentlyContinue
    Write-Host "stdout:" -ForegroundColor Yellow
    Get-Content (Join-Path $LogDir "stdout.log") -Tail 15 -ErrorAction SilentlyContinue
}
