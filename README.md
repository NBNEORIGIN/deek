# CLAW — Coding and Language Agent Workbench

Sovereign AI coding agent. Self-hosted replacement for Windsurf/Cursor
with permanent per-project context, hybrid local/API model routing,
and three simultaneous interfaces.

```
VS Code extension  ──┐
Web chat           ──┤──▶ CLAW API (FastAPI) ──▶ Qwen 7B (local)
WhatsApp           ──┘                       └──▶ Claude Sonnet (API)
```

## Quick start

### 1. Prerequisites

- Python 3.11+
- Node.js 18+
- [Ollama](https://ollama.com/download) — install and run, then:
  ```
  ollama pull qwen2.5-coder:7b
  ollama pull nomic-embed-text
  ```
- PostgreSQL 17 (already running)
- pgvector — see below

### 2. Install pgvector (run as Administrator)

```powershell
# Download pre-built binary from:
# https://github.com/andreiramani/pgvector_pgsql_windows/releases/tag/0.8.2_17.6
# File: vector.v0.8.2-pg17.zip
#
# Then from an Administrator PowerShell:
$src = "C:\path\to\extracted\pgvector"
$pg  = "C:\Program Files\PostgreSQL\17"
Copy-Item "$src\lib\vector.dll"            "$pg\lib\" -Force
Copy-Item "$src\share\extension\*"         "$pg\share\extension\" -Force
New-Item -ItemType Directory -Force "$pg\include\server\extension\vector"
Copy-Item "$src\include\server\extension\vector\*" "$pg\include\server\extension\vector\" -Force

# Enable the extension
$env:PGPASSWORD = "postgres123"
& "$pg\bin\psql.exe" -U postgres -d claw -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### 3. Python setup

```powershell
cd D:\claw
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
copy .env.example .env   # then edit .env
```

### 4. Configure projects

Edit `projects/phloe/config.json` — set `codebase_path` to your Phloe repo path.  
Edit `projects/phloe/core.md` — already pre-written, update as needed.

### 5. Index a project

```powershell
.\.venv\Scripts\python scripts\index_project.py --project phloe
```

### 6. Start the API

```powershell
.\.venv\Scripts\uvicorn api.main:app --port 8765 --reload
```

Verify: `http://localhost:8765/health`

### 7. Start the web chat

```powershell
cd web
npm run dev
```

Open: `http://localhost:3000`

### 8. Install the VS Code extension

```powershell
cd vscode-extension
npx vsce package
code --install-extension claw-0.1.0.vsix
```

Press `Ctrl+Shift+A` in VS Code to open the CLAW panel.

---

## Architecture

```
claw/
├── core/              Python agent core
│   ├── agent.py       Orchestrator — all channels flow through here
│   ├── channels/      Message envelope (normalises all input)
│   ├── context/       Three-tier context engine + pgvector indexer
│   ├── models/        Ollama + Claude clients, routing logic
│   ├── tools/         File, search, exec tools with approval gate
│   └── memory/        SQLite conversation + decision memory
├── api/               FastAPI server (port 8765)
├── vscode-extension/  TypeScript VS Code extension
├── web/               Next.js web chat (port 3000)
└── projects/          Per-project config (core.md + config.json)
    ├── phloe/
    ├── manufacturing/
    └── _template/
```

## Model routing

| Condition | Model |
|---|---|
| Simple keywords (fix, add, update) | Qwen 7B (local, free) |
| Complex keywords (architect, review, security) | Claude Sonnet (API) |
| Context > 6000 tokens | Claude Sonnet (API) |
| `force_model` set in config.json | Forced |
| Default | Qwen 7B (local) |

## Adding a project

```powershell
.\.venv\Scripts\python scripts\new_project.py --id myproject --name "My Project" --path C:\path\to\repo
# Then edit projects/myproject/core.md
.\.venv\Scripts\python scripts\index_project.py --project myproject
```

## WhatsApp (OpenClaw)

Install OpenClaw on the sovereign server, point it to:
`http://<claw-server>:8765/whatsapp-proxy`

Map phone numbers to projects in `api/routes/whatsapp.py`:
```python
PHONE_TO_PROJECT = {
    '+447XXXXXXXXXX': 'phloe',
}
```

## HALT conditions

| Code | Condition | Fix |
|---|---|---|
| HALT-01 | nomic-embed-text missing | `ollama pull nomic-embed-text` |
| HALT-02 | pgvector not in PostgreSQL | Install binary (see Step 2 above) |
| HALT-03 | Qwen 7B not in Ollama | `ollama pull qwen2.5-coder:7b` |
| HALT-04 | No Anthropic API key | Set `ANTHROPIC_API_KEY` in `.env` |
| HALT-05 | VS Code extension fails | `npm install && npm run compile` |
| HALT-07 | File edit outside project root | Path traversal check in `context/engine.py` |
