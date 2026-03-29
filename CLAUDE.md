# Cairn — Claude Code Session Instructions

## What this project is
Cairn (formerly CLAW) is a self-hosted sovereign AI coding agent
built for NBNE (North By North East Print & Sign Ltd), Alnwick,
Northumberland. It replaces Cursor and Windsurf as the primary
development tool for all NBNE projects.

## Project location
D:\claw — FastAPI backend
D:\claw\web — Next.js frontend
D:\nbne_business\nbne_platform — Phloe Django backend

## Key architecture rules
- core/agent.py — ClawAgent is the main orchestrator
- core/models/router.py — five-tier model routing
- core/models/task_classifier.py — rule-based, <1ms, no LLM calls
- core/tools/registry.py — SAFE/REVIEW/DESTRUCTIVE risk levels
- core/wiggum.py — autonomous outer loop
- Every file write requires approval — never auto-execute DESTRUCTIVE tools

## Phloe rule (critical)
Every Django queryset touching Phloe data MUST filter by tenant.
Failure to do this is a data isolation bug. The output validator
checks for this automatically. Never generate a queryset without
.filter(tenant=request.tenant) or equivalent.

## Current hardware
GTX 1050 8GB — CLAW_FORCE_API=true, all inference via API
RTX 3090 24GB arriving next week — will enable local inference
CLAW_FORCE_API=false after 3090 installed

## Model routing
Tier 1: Local Ollama (qwen2.5-coder:7b currently)
Tier 2: DeepSeek API (deepseek-chat)
Tier 3: Claude Sonnet (claude-sonnet-4-6)
Tier 4: Claude Opus (claude-opus-4-6)
Fallback: OpenAI GPT (on Claude rate limits)

## Projects
- claw/cairn: D:\claw (this codebase)
- phloe: D:\nbne_business\nbne_platform (Django/Next.js WaaS)
- manufacturing: not yet built (replaces Excel sheet)
- origin-designed: Amazon/eBay/Etsy generic sign products

## Primary Interface
Cairn's primary interface is the VS Code extension.
  Install: code --install-extension vscode-extension/cairn-0.2.0.vsix
  Open panel: Ctrl+Shift+A
  Status bar: bottom left shows connection status
  Commands: Ctrl+Shift+P → "Cairn:"

The web UI (localhost:3000) is secondary —
use it for the status dashboard and approval queue.

## Frontend builds
Frontend runs as a production build (npm start), not dev server.
After any frontend code change run build-cairn.bat then
restart-claw.bat to apply changes.
Never use npm run dev — it's unstable for daily use.

## After extension changes
Rebuild and reinstall the VS Code extension:
  cd vscode-extension && npm run compile && npx vsce package
  code --install-extension cairn-*.vsix

## Test suite
295 tests across tests/
Run: pytest tests/ -v
All must pass before any commit

## Commit discipline
- Never commit log files (logs/ is gitignored)
- Never commit .env or web/.env.local
- Always run pytest before committing
- Commit message format: type(scope): description
  e.g. feat(memory): add BM25 hybrid retrieval

## What's been built
See projects/claw/core.md for full decision history

## What's in progress
- Layer 3: Skills layer (core/skills/)
- Status dashboard (web/src/app/status/)
- Chat history UI with subproject sidebar
- Three-tier engineering review loop
- Rebrand from CLAW to Cairn (in progress)

## Development approach
- Read files before editing — never assume contents
- Run tests after each logical change
- Commit working increments — don't batch everything into one commit
- If a test fails: fix it before moving on
- If stuck: report the blocker clearly, don't guess
