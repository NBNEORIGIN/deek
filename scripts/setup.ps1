# CLAW first-time setup — Windows PowerShell
# Run from the claw\ directory: .\scripts\setup.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "`nSetting up CLAW..." -ForegroundColor Cyan

# ── Prerequisite checks ──────────────────────────────────────────────────────

function Check-Command($cmd) {
    return [bool](Get-Command $cmd -ErrorAction SilentlyContinue)
}

if (-not (Check-Command python)) {
    Write-Error "Python 3.11+ required"; exit 1
}
if (-not (Check-Command node)) {
    Write-Error "Node.js 18+ required"; exit 1
}
if (-not (Check-Command ollama)) {
    Write-Host "WARNING: Ollama not found in PATH." -ForegroundColor Yellow
    Write-Host "Download from https://ollama.com/download and install it." -ForegroundColor Yellow
    Write-Host "Then run: ollama pull qwen2.5-coder:7b && ollama pull nomic-embed-text" -ForegroundColor Yellow
} else {
    Write-Host "Pulling Ollama models (this may take a while)..."
    ollama pull qwen2.5-coder:7b
    ollama pull nomic-embed-text
}

# ── Python environment ────────────────────────────────────────────────────────

Write-Host "`nCreating Python virtual environment..."
python -m venv .venv

Write-Host "Installing Python dependencies..."
.\.venv\Scripts\python.exe -m pip install --upgrade pip -q
.\.venv\Scripts\pip.exe install -e ".[dev]" -q

# ── Database setup ────────────────────────────────────────────────────────────

Write-Host "`nSetting up PostgreSQL database..."
$pgBin = "C:\Program Files\PostgreSQL\17\bin"
if (Test-Path "$pgBin\psql.exe") {
    $env:PGPASSWORD = "postgres123"
    & "$pgBin\psql.exe" -U postgres -c "CREATE DATABASE claw;" 2>$null
    Write-Host "NOTE: pgvector extension must be installed separately."
    Write-Host "Download from: https://github.com/pgvector/pgvector/releases"
    Write-Host "For PostgreSQL 17 on Windows, download the zip and copy files to:"
    Write-Host "  C:\Program Files\PostgreSQL\17\lib\ (pgvector.dll)"
    Write-Host "  C:\Program Files\PostgreSQL\17\share\extension\ (vector.*)"
    Write-Host "Then run: psql -U postgres -d claw -c `"CREATE EXTENSION vector;`""
}

# ── Environment file ──────────────────────────────────────────────────────────

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "`nCreated .env from .env.example — edit it with your settings." -ForegroundColor Yellow
} else {
    Write-Host ".env already exists — skipping."
}

# ── Data directory ────────────────────────────────────────────────────────────

New-Item -ItemType Directory -Force -Path "data" | Out-Null

# ── VS Code extension ─────────────────────────────────────────────────────────

Write-Host "`nBuilding VS Code extension..."
Push-Location vscode-extension
npm install --silent
npm run compile
Pop-Location
Write-Host "Extension compiled. To install:"
Write-Host "  cd vscode-extension && npx vsce package && code --install-extension claw-0.1.0.vsix"

# ── Web interface ─────────────────────────────────────────────────────────────

Write-Host "`nInstalling web interface dependencies..."
Push-Location web
npm install --silent
Pop-Location

# ── Done ──────────────────────────────────────────────────────────────────────

Write-Host "`n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
Write-Host "CLAW setup complete." -ForegroundColor Green
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Edit .env — set ANTHROPIC_API_KEY and DATABASE_URL"
Write-Host "  2. Install pgvector for PostgreSQL 17 (see above)"
Write-Host "  3. Edit projects/phloe/core.md and set codebase_path in config.json"
Write-Host "  4. .\.venv\Scripts\python.exe scripts\index_project.py --project phloe"
Write-Host "  5. .\.venv\Scripts\uvicorn.exe api.main:app --port 8765 --reload"
Write-Host "  6. cd web && npm run dev   (web chat at http://localhost:3000)"
Write-Host "  7. Install VS Code extension: Ctrl+Shift+A to open panel"
Write-Host ""
