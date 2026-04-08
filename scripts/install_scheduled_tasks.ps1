# Cairn Windows Scheduled Task installer
# Registers tasks that run independently of the FastAPI process.
# No admin required for current-user tasks.
#
# Tasks installed:
#   Cairn-AMI-Sync       -- SP-API sync every 6 hours
#   CairnEmailInbox      -- cairn@ inbox poll every 15 minutes

param(
    [string]$ClawDir = "D:\claw"
)

$PythonExe = Join-Path $ClawDir ".venv\Scripts\python.exe"
$SyncScript = Join-Path $ClawDir "scripts\run_ami_sync.py"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Python venv not found at $PythonExe"
    exit 1
}

if (-not (Test-Path $SyncScript)) {
    Write-Error "Sync script not found at $SyncScript"
    exit 1
}

Write-Host "Installing Cairn AMI sync scheduled task..."

# Remove existing task if present
$existing = Get-ScheduledTask -TaskName "Cairn-AMI-Sync" -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName "Cairn-AMI-Sync" -Confirm:$false
    Write-Host "  Removed existing task"
}

# Action: python run_ami_sync.py
$action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument $SyncScript `
    -WorkingDirectory $ClawDir

# Four daily triggers at 00:00, 06:00, 12:00, 18:00
$t1 = New-ScheduledTaskTrigger -Daily -At "00:00"
$t2 = New-ScheduledTaskTrigger -Daily -At "06:00"
$t3 = New-ScheduledTaskTrigger -Daily -At "12:00"
$t4 = New-ScheduledTaskTrigger -Daily -At "18:00"

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName "Cairn-AMI-Sync" `
    -TaskPath "\Cairn\" `
    -Action $action `
    -Trigger $t1,$t2,$t3,$t4 `
    -Settings $settings `
    -Principal $principal `
    -Description "Cairn SP-API sync: orders, traffic, inventory, velocity. 4x daily." `
    | Out-Null

$registered = Get-ScheduledTask -TaskName "Cairn-AMI-Sync" -ErrorAction SilentlyContinue
if ($registered) {
    Write-Host "  [OK] Cairn-AMI-Sync registered" -ForegroundColor Green
    Write-Host "  Triggers: 00:00, 06:00, 12:00, 18:00 daily"
    Write-Host "  Script: $SyncScript"
    Write-Host ""
    Write-Host "To run a sync now (with force flag):"
    Write-Host "  $PythonExe $SyncScript --force"
    Write-Host ""
    Write-Host "Logs: $ClawDir\logs\ami_sync\ami_sync.log"
} else {
    Write-Error "Task registration failed"
    exit 1
}

# ── CairnEmailInbox — cairn@ inbox poll every 15 minutes ──────────────────

$InboxScript = Join-Path $ClawDir "scripts\process_cairn_inbox.py"

if (-not (Test-Path $InboxScript)) {
    Write-Warning "Inbox script not found at $InboxScript — skipping CairnEmailInbox"
} else {
    Write-Host "Installing Cairn email inbox scheduled task..."

    $existingInbox = Get-ScheduledTask -TaskName "CairnEmailInbox" -ErrorAction SilentlyContinue
    if ($existingInbox) {
        Unregister-ScheduledTask -TaskName "CairnEmailInbox" -Confirm:$false
        Write-Host "  Removed existing task"
    }

    $inboxAction = New-ScheduledTaskAction `
        -Execute $PythonExe `
        -Argument $InboxScript `
        -WorkingDirectory $ClawDir

    # Every 15 minutes, starting now, indefinitely
    $inboxTrigger = New-ScheduledTaskTrigger `
        -Once `
        -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 15) `
        -RepetitionDuration ([TimeSpan]::MaxValue)

    $inboxSettings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
        -RestartCount 1 `
        -RestartInterval (New-TimeSpan -Minutes 2) `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask `
        -TaskName "CairnEmailInbox" `
        -TaskPath "\Cairn\" `
        -Action $inboxAction `
        -Trigger $inboxTrigger `
        -Settings $inboxSettings `
        -Principal $principal `
        -Description "Cairn: poll cairn@nbnesigns.com for new messages, ingest and embed. Every 15 minutes." `
        | Out-Null

    $registeredInbox = Get-ScheduledTask -TaskName "CairnEmailInbox" -ErrorAction SilentlyContinue
    if ($registeredInbox) {
        Write-Host "  [OK] CairnEmailInbox registered" -ForegroundColor Green
        Write-Host "  Trigger: every 15 minutes"
        Write-Host "  Script: $InboxScript"
        Write-Host "  Logs: $ClawDir\logs\email_ingest\cairn_inbox.log"
        Write-Host ""
        Write-Host "NOTE: Requires IMAP_PASSWORD_CAIRN set in $ClawDir\.env" -ForegroundColor Yellow
        Write-Host "NOTE: Set up IONOS forwarding from sales@ and toby@ to cairn@ before this runs." -ForegroundColor Yellow
    } else {
        Write-Warning "CairnEmailInbox registration failed — check PowerShell permissions"
    }
}

Write-Host ""
Write-Host "NOTE: claw-api NSSM service requires Administrator." -ForegroundColor Yellow
Write-Host "Run install_services.ps1 as Admin to make the API survive reboots."
