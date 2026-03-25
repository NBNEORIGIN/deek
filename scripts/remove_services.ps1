# Remove NSSM services — run ONCE as Administrator.
# After this, the CLAW tray app manages all processes directly.
# No admin rights are ever needed again.

param([string]$ClawDir = "D:\claw")

$NSSM = Join-Path $ClawDir "scripts\nssm.exe"

if (-not (Test-Path $NSSM)) {
    Write-Host "[ERR] nssm.exe not found at $NSSM" -ForegroundColor Red
    exit 1
}

foreach ($svc in @("claw-web", "claw-api")) {
    $status = & $NSSM status $svc 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Removing $svc ..." -ForegroundColor Cyan
        & $NSSM stop   $svc 2>&1 | Out-Null
        Start-Sleep -Seconds 2
        & $NSSM remove $svc confirm 2>&1 | Out-Null
        Write-Host "  [OK] $svc removed" -ForegroundColor Green
    } else {
        Write-Host "  [--] $svc not installed, skipping" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "Done. Run setup_tray.ps1 (no admin needed) to start CLAW." -ForegroundColor Green
