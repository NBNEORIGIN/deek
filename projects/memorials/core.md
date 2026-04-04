# Memorials — Cairn Agent Core Context
# Version: 1.0
# KEEP THIS FILE UNDER 2000 TOKENS.
# Update when significant architectural decisions are made.
# NEVER auto-generate this file. Write it by hand.

## What this is
Memorials is NBNE's personalised memorial product order processing and SVG
generation system. FastAPI 0.115 backend + Next.js 16 frontend, deployed via
Docker Compose (backend:8012, frontend:3012). Staff upload Amazon order .txt
files; the system downloads customisation ZIPs, extracts XML personalisation
data and customer photos, maps SKUs to memorial types/colours/processors, and
generates batched SVG print sheets across 7 processor types. Used daily for
production runs.

GitHub: https://github.com/NBNEORIGIN/memorials
Local: D:\memorials
Stack: FastAPI + SQLAlchemy 2.0 + PostgreSQL/SQLite, Next.js 16 + Tailwind 4

## Non-negotiable rules

1. Never hardcode credentials. All SMTP, database, and API secrets via env
   vars or .env files only. Never in source code.
2. GRAPHICS_DIR must be configurable via environment variable. The default
   must point to bundled assets (./assets/graphics), not a personal path.
3. Processor classes are stateless. No shared mutable state between processors.
   Each receives only its own pre-filtered, pre-sorted items.
4. SKU mappings (SkuMapping table) are the single source of truth for routing
   orders to processors. Never bypass SkuMapping to hardcode associations.
5. Print sheet layouts are database-driven via CellLayout. Code defaults are
   fallbacks only — staff admin overrides always take precedence.
6. Never commit uploaded files, output SVGs, or database files to git.

## Domain vocabulary

- Processor: SVG generator class registered via @register in processors/registry.py
- Print sheet: full-page SVG containing a grid of memorial cells for printing
- Cell: one memorial item positioned within a print sheet grid
- CellLayout: DB record overriding text/graphic positions for a processor or SKU
- SkuMapping: links Amazon/Etsy SKU → memorial_type + colour + processor
- Job: one batch upload (one .txt file = one Job, containing multiple JobItems)
- JobItem: one order line within a Job, resolved to a processor via SKU lookup
- Enrichment: downloading Amazon customisation ZIP, extracting XML + photos

## Architecture

```
backend/app/config.py        — pydantic-settings, env-driven configuration
backend/app/models.py        — all 11 SQLAlchemy models (single file)
backend/app/processors/      — 7 SVG processor classes + base + registry
backend/app/ingestion/       — Amazon order parser + XML extractor
backend/app/routers/         — FastAPI routes (orders, skus, generate, layouts, bugreport)
frontend/src/app/page.tsx    — main memorial maker UI
frontend/src/app/admin/      — admin dashboard (SKUs, colours, types, layouts)
```

## Current state

- All 7 processors generating print sheets correctly
- Admin dashboard functional for SKU/colour/type/layout management
- Docker deployment working on NBNE infrastructure (Hetzner 178.104.1.152)
- Bug reporting via IONOS SMTP (XSS-safe as of 2026-04-04)
- CORS locked to app.nbnesigns.co.uk (configurable via CORS_ORIGINS env var)
- Upload filenames UUID-sanitised (path traversal fix 2026-04-04)
- Cairn Protocol memory: POST /api/memory/write, GET /api/memory/retrieve
- Hybrid BM25 + cosine retrieval with Ollama/sentence-transformers embeddings
- Ark backup: backup-memorials.sh + restore-memorials.sh (SQLite + 3 Docker volumes)
- No authentication on API — acceptable behind nginx, known gap
- pytest scaffold exists (test_health, test_processors, test_memory)
