# CLAW Service Uninstaller — run as Administrator
param([string]$ClawDir = "D:\claw")

$NSSM = Join-Path $ClawDir "scripts\nssm.exe"

foreach ($svc in @("claw-api", "claw-web")) {
    $s = Get-Service $svc -ErrorAction SilentlyContinue
    if ($s) {
        Write-Host "Stopping and removing $svc..." -ForegroundColor Yellow
        & $NSSM stop   $svc 2>&1 | Out-Null
        & $NSSM remove $svc confirm 2>&1 | Out-Null
        Write-Host "  Removed $svc" -ForegroundColor Green
    } else {
        Write-Host "  $svc not installed, skipping" -ForegroundColor Gray
    }
}

# Remove tray startup entry
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" `
                    -Name "CLAW-Tray" -ErrorAction SilentlyContinue
Write-Host "Tray startup entry removed" -ForegroundColor Green
Write-Host "`nAll CLAW services uninstalled." -ForegroundColor Cyan
