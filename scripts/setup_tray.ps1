# Register and launch the CLAW tray app.
# No admin rights required. Run this once after remove_services.ps1,
# or any time you need to re-register the tray at Windows login.

param([string]$ClawDir = "D:\claw")

$PythonExe  = Join-Path $ClawDir ".venv\Scripts\python.exe"
$TrayScript = Join-Path $ClawDir "tray\claw_tray.py"
$RunKey     = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"

if (-not (Test-Path $PythonExe)) {
    Write-Host "[ERR] Python venv not found: $PythonExe" -ForegroundColor Red
    Write-Host "      Run: cd $ClawDir && python -m venv .venv && .venv\Scripts\pip install -e ." -ForegroundColor Yellow
    exit 1
}

# Register at login
$TrayCmd = "`"$PythonExe`" `"$TrayScript`""
Set-ItemProperty -Path $RunKey -Name "CLAW-Tray" -Value $TrayCmd
Write-Host "[OK] Tray registered at user login" -ForegroundColor Green

# Kill any existing tray instance so we get a clean start
Get-WmiObject Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*claw_tray*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Sleep -Seconds 1

# Launch tray (it will spawn the API and web as children)
Start-Process -FilePath $PythonExe -ArgumentList "`"$TrayScript`"" -WindowStyle Hidden
Write-Host "[OK] Tray launched — check the system tray" -ForegroundColor Green
Write-Host ""
Write-Host "  API  -> http://localhost:8765"
Write-Host "  Web  -> http://localhost:3000"
Write-Host ""
Write-Host "  Web takes ~15 s on first start (Next.js dev compile)."
