# CLAW — Self-Hosted Sovereign AI Coding Agent
# Version: 1.1
# This is CLAW's context for maintaining itself.

## What this is
CLAW (Coding and Language Agent Workbench) is a self-hosted AI coding agent
built at D:\claw. It replaces Cursor/Windsurf as the primary AI coding tool
for all NBNE projects. It runs on a Windows machine (Intel i5-10400F,
RTX 3050 8GB, 16GB RAM).

## Architecture
```
D:\claw\
├── api/              FastAPI server — entry point: api/main.py
│   ├── main.py       Routes: /chat, /projects, /debug/tools/{id}, /health
│   └── routes/       whatsapp.py proxy
├── core/
│   ├── agent.py      Main orchestrator — ClawAgent.process()
│   ├── channels/     MessageEnvelope, AgentResponse
│   ├── context/      engine.py (3-tier), indexer.py (pgvector)
│   ├── memory/       store.py (SQLite per project)
│   ├── models/       router.py, claude_client.py, ollama_client.py, openai_client.py
│   └── tools/        registry.py + all tool modules
├── projects/         Per-project config.json + core.md
│   ├── claw/         This project (self-referential)
│   ├── phloe/        Phloe WaaS platform
│   └── manufacturing/ Origin Designed manufacturing app
├── core/wiggum.py    WiggumOrchestrator — outer loop driving CLAW toward a goal
├── tests/            test_claw.py — 36 pytest tests (run: .venv\Scripts\python -m pytest tests/)
├── web/              Next.js chat UI — runs on port 3000 (next dev)
├── tray/             claw_tray.py — system tray process manager (replaces NSSM)
├── vscode-extension/ VS Code panel — Ctrl+Shift+A
└── scripts/          test_agent.py, index_project.py, new_project.py
```

## Running services
- CLAW API: managed by tray app — http://localhost:8765
- Web UI: managed by tray app — http://localhost:3000 (next dev mode)
- Both start automatically at login via the tray app (no NSSM, no admin required)
- To restart: right-click the tray icon → Restart API / Restart Web

## Current model config
- API_PROVIDER=auto: tries Claude first, falls back to OpenAI on rate-limit
- Default model: claude-sonnet-4-6 (CLAUDE_MODEL in .env)
- Fallback model: gpt-4o (OPENAI_MODEL in .env)
- Opus escalation: architecture/security/trade-off keywords → claude-opus-4-6
- Local model (disabled): qwen2.5-coder:7b via Ollama — re-enable after RTX 3090

## WIGGUM loop
- POST /wiggum  {"goal": "...", "success_criteria": [...], "project": "claw"}
- GET  /wiggum/{run_id}  — poll for status (running | complete | max_iterations | error)
- GET  /wiggum           — list all runs
- Orchestrator iterates: assess (read_only) → plan (read_only) → execute → repeat
- read_only=True in envelope suppresses REVIEW/DESTRUCTIVE tools (assessment passes only)
- Human approval still required for REVIEW/DESTRUCTIVE tools even inside a WIGGUM run

## Indexing status
- CLAW self-index: ✅ BUILT — 69 files, 0 errors (pgvector, nomic-embed-text)
- Phloe index: ✅ BUILT — 1,902 chunks across 260 files, 0 errors
- Tier 2 semantic search is ACTIVE for both projects
- Re-index after major changes: python scripts/index_project.py --project claw

## Tool system
Tools registered for this project. Risk levels:
- SAFE (auto-execute): read_file, search_code, git_status, git_diff, git_log,
  web_fetch, web_check_status, web_search, check_server
- REVIEW (approval required): edit_file, create_file, git_add, git_commit,
  git_branch, git_stash, run_tests, run_migration
- DESTRUCTIVE (explicit confirm): run_command, git_push

## Key files to know
- core/agent.py — ClawAgent, tool dispatch, model routing, _get_api_client()
- core/models/claude_client.py — Anthropic API wrapper, tool_choice
- core/models/openai_client.py — OpenAI wrapper, tool format translation
- core/tools/registry.py — TOOL_SCHEMAS dict, ToolRegistry class
- core/context/engine.py — 3-tier context, pgvector Tier 2
- core/context/indexer.py — CodeIndexer, MAX_CHUNK_CHARS=1500
- .env — API keys, model config, API_PROVIDER, CLAW_FORCE_API
- tests/test_claw.py — 27 tests covering endpoints, tools, models, indexer

## Critical rules
1. Never modify .env directly — always show the change for approval
2. Never git push without explicit user confirmation
3. run_migration is not relevant here (no Django) — don't suggest it
4. The venv is at D:\claw\.venv — always use .\.venv\Scripts\activate on Windows
5. Ports: API=8765, Web UI=3000

## Known issues / recent changes
- Migrated from NSSM Windows services to tray-based process manager (no admin needed)
- OpenAI fallback added (API_PROVIDER=auto) — automatic failover to gpt-4o
- pgvector index built and active for both claw and phloe projects
- Indexer MAX_CHUNK_CHARS=1500 prevents nomic-embed-text 500 errors on dense files
- tool_choice: auto in claude_client.py ensures Claude uses tools reliably
- web_fetch uses verify=False (SSL) to handle Cloudflare-protected sites
- generate_video and generate_image registered but not in permissions (GPU-intensive)
