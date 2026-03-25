# CLAW status — no admin required
param([string]$ClawDir = "D:\claw")

function Show-Svc($name) {
    $s = Get-Service $name -ErrorAction SilentlyContinue
    if ($s) {
        $colour = if ($s.Status -eq "Running") { "Green" } else { "Red" }
        Write-Host ("  {0,-12} {1}" -f $name, $s.Status) -ForegroundColor $colour
    } else {
        Write-Host ("  {0,-12} NOT INSTALLED" -f $name) -ForegroundColor Yellow
    }
}

Write-Host "`nCLAW Services" -ForegroundColor Cyan
Show-Svc "claw-api"
Show-Svc "claw-web"

Write-Host "`nAPI Health" -ForegroundColor Cyan
try {
    $r = Invoke-RestMethod "http://localhost:8765/health" `
         -Headers @{"X-API-Key"="claw-dev-key-change-in-production"} `
         -TimeoutSec 4
    Write-Host "  status  : $($r.status)"   -ForegroundColor Green
    Write-Host "  ollama  : $($r.ollama_available)"
    Write-Host "  model   : $($r.ollama_model)"
    Write-Host "  projects: $($r.projects_loaded)"
} catch {
    Write-Host "  API not responding" -ForegroundColor Red
}

Write-Host "`nLog tails (last 5 lines each)" -ForegroundColor Cyan
foreach ($log in @("$ClawDir\logs\api\stdout.log", "$ClawDir\logs\web\stdout.log")) {
    if (Test-Path $log) {
        Write-Host "`n  $log" -ForegroundColor Gray
        Get-Content $log -Tail 5 | ForEach-Object { Write-Host "    $_" }
    }
}
Write-Host ""
