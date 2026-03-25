# Fix claw-api encoding issue — run as Administrator
param([string]$ClawDir = "D:\claw")

$NSSM   = Join-Path $ClawDir "scripts\nssm.exe"
$CmdExe = "$env:SystemRoot\System32\cmd.exe"
$Bat    = Join-Path $ClawDir "scripts\start_api.cmd"
$LogDir = Join-Path $ClawDir "logs\api"

Write-Host "Reinstalling claw-api with cmd.exe wrapper..." -ForegroundColor Cyan

& $NSSM stop   claw-api 2>&1 | Out-Null
& $NSSM remove claw-api confirm 2>&1 | Out-Null

& $NSSM install claw-api $CmdExe "/c `"$Bat`""
& $NSSM set claw-api AppDirectory  $ClawDir
& $NSSM set claw-api DisplayName   "CLAW API (FastAPI)"
& $NSSM set claw-api Start         SERVICE_AUTO_START
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
& $NSSM set claw-api AppStdout     (Join-Path $LogDir "stdout.log")
& $NSSM set claw-api AppStderr     (Join-Path $LogDir "stderr.log")
& $NSSM set claw-api AppRotateFiles 1
& $NSSM set claw-api AppRotateBytes 5242880
& $NSSM set claw-api AppExit       Default Restart
& $NSSM set claw-api AppRestartDelay 5000

Start-Service claw-api -ErrorAction SilentlyContinue
Start-Sleep -Seconds 6

$status = (Get-Service claw-api).Status
if ($status -eq "Running") {
    Write-Host "[OK]  claw-api: Running  ->  http://localhost:8765" -ForegroundColor Green
    Write-Host "`nLast stdout lines:" -ForegroundColor Gray
    Get-Content (Join-Path $LogDir "stdout.log") -Tail 10 -ErrorAction SilentlyContinue
} else {
    Write-Host "[ERR] claw-api: $status" -ForegroundColor Red
    Get-Content (Join-Path $LogDir "stderr.log") -Tail 15 -ErrorAction SilentlyContinue
}
