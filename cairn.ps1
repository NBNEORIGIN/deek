<#
.SYNOPSIS
    Cairn prompt wrapper — prepends the Cairn Protocol to every Claude Code prompt.

.DESCRIPTION
    Checks Cairn API health, resolves the active project, builds a protocol-prefixed
    prompt, pipes it to claude --print, and writes session state.

.PARAMETER Prompt
    The task or question to send to Claude Code.

.PARAMETER Project
    Project name (claw, phloe, render, manufacture, ledger, crm, etc.).
    If omitted, reads from session_state.json or lists available projects.

.PARAMETER NoMemory
    Skip memory retrieval steps (steps 1-3) in the protocol prefix.

.PARAMETER Opus
    Force Claude Opus (Tier 4) for this task.

.EXAMPLE
    .\cairn.ps1 "fix the git_commit tool mapping" claw
    .\cairn.ps1 "add tenant filter" phloe -Opus
    .\cairn.ps1 "scaffold new endpoint" -NoMemory
#>

param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$Prompt,

    [Parameter(Position=1)]
    [string]$Project,

    [switch]$NoMemory,
    [switch]$Opus
)

$ErrorActionPreference = "Stop"
$CairnBase = "http://localhost:8765"
$DataDir = "D:\claw\data"
$StateFile = Join-Path $DataDir "session_state.json"

# Ensure data directory exists
if (-not (Test-Path $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
}

# Step 1: Health check
try {
    $health = Invoke-RestMethod -Uri "$CairnBase/health" -TimeoutSec 5
} catch {
    Write-Host ""
    Write-Host "CAIRN OFFLINE" -ForegroundColor Red
    Write-Host "-------------------------------------"
    Write-Host "Start Cairn first:"
    Write-Host ""
    Write-Host "  cd D:\claw"
    Write-Host "  .\.venv\Scripts\python -m uvicorn api.main:app --host 0.0.0.0 --port 8765"
    Write-Host ""
    Write-Host "Or run: build-cairn.bat"
    Write-Host ""
    exit 1
}

# Step 2: Resolve project
if ([string]::IsNullOrWhiteSpace($Project)) {
    if (Test-Path $StateFile) {
        $state = Get-Content $StateFile -Raw | ConvertFrom-Json
        if ($state.active_project) {
            $Project = $state.active_project
            Write-Host "CAIRN: Using last project: $Project" -ForegroundColor DarkGray
        }
    }
}

if ([string]::IsNullOrWhiteSpace($Project)) {
    try {
        $projects = Invoke-RestMethod -Uri "$CairnBase/projects" -TimeoutSec 5
        Write-Host ""
        Write-Host "No project specified. Available projects:" -ForegroundColor Yellow
        Write-Host "-------------------------------------"
        foreach ($p in $projects.projects) {
            $name = $p.name
            $status = $p.status
            Write-Host "  $name ($status)"
        }
        Write-Host ""
        Write-Host 'Usage: cairn "your prompt" <project>'
        Write-Host ""
    } catch {
        Write-Host "No project specified and could not list projects." -ForegroundColor Red
    }
    exit 1
}

# Step 3: Build the prefixed prompt
# Use single-quoted here-strings for template parts to avoid PowerShell parsing issues
$memoryBlock = @'
BEFORE responding:
1. Call retrieve_codebase_context(query="{PROMPT}", project="{PROJECT}", limit=10)
2. Call retrieve_chat_history(query="{PROMPT}", project="{PROJECT}", limit=10)
3. Check: has this been solved before? Is there a rejected approach to avoid?
4. Classify task complexity and delegate to the appropriate model tier per CLAUDE.md
'@

$noMemoryBlock = @'
BEFORE responding:
1. Classify task complexity and delegate to the appropriate model tier per CLAUDE.md
'@

$afterBlock = @'
AFTER responding:
5. Call update_memory() with decision, rejected approaches, outcome, files changed
6. Call log_cost() with token counts and GBP cost for every model used
7. If files were changed: POST http://localhost:8765/index?project={PROJECT}
'@

# Select before block
if ($NoMemory) {
    $beforeSection = $noMemoryBlock
} else {
    $beforeSection = $memoryBlock
}

# Replace placeholders with actual values
$beforeSection = $beforeSection -replace '\{PROMPT\}', $Prompt
$beforeSection = $beforeSection -replace '\{PROJECT\}', $Project
$afterSection = $afterBlock -replace '\{PROJECT\}', $Project

# Build the full prompt
$lines = @(
    "[CAIRN PROTOCOL - execute silently before responding]"
    ""
    "Project: $Project"
    "Prompt: $Prompt"
    ""
    $beforeSection
    ""
    "RESPOND to the prompt above."
    ""
    $afterSection
    ""
    "[END CAIRN PROTOCOL]"
)

if ($Opus) {
    $lines += "[FORCE TIER: Use Claude Opus for this task]"
}

$prefixedPrompt = $lines -join "`n"

# Step 4: Print status and send
$tierLabel = "AUTO"
if ($Opus) { $tierLabel = "OPUS" }
Write-Host ""
Write-Host "CAIRN -> $Project | $Prompt" -ForegroundColor Cyan
if ($NoMemory) {
    Write-Host "  (memory retrieval skipped)" -ForegroundColor DarkGray
}
if ($Opus) {
    Write-Host "  (forced Opus tier)" -ForegroundColor Magenta
}
Write-Host ""

# Pipe to Claude Code
$prefixedPrompt | claude --print

# Step 5: Write session state
$timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$sessionState = @{
    active_project = $Project
    last_prompt    = $Prompt
    last_run       = $timestamp
} | ConvertTo-Json -Compress

Set-Content -Path $StateFile -Value $sessionState -Encoding UTF8
