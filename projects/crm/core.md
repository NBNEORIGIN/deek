# NBNE CRM — Core Context

## What this is
B2B project management and business development platform for NBNE's commercial signage work.
Live at crm.nbnesigns.co.uk. Being upgraded from standalone tool to Cairn-connected business
development platform with semantic search, email integration, and cross-module intelligence.

## Current stack
- Frontend + API: Next.js (migrating from Vercel to Hetzner)
- Database: PostgreSQL + pgvector on nbne1 (192.168.1.228), DB: cairn_crm
  Connection: postgresql://cairn:cairn_nbne_2026@192.168.1.228:5432/cairn_crm
- AI: Llama for insights (existing), migrating to Cairn hybrid search
- Domain: crm.nbnesigns.co.uk
- GitHub: https://github.com/NBNEORIGIN/crm
- Email: cairn@nbnesigns.com (dedicated), sales@nbnesigns.co.uk (read-only), toby@nbnesigns.com (read-only)
- Backup: Contabo nightly (automatic — all nbne1 databases included)

## Live data (as of 03 April 2026)
- Pipeline: £50,309 total (18 leads £17,450, 13 quoted £25,716, 6 in production £6,218)
- 31 live recommendations
- Active projects include: Bamburgh Golf Club, Paton & Co Estate Agents, Glendale Show

## The boardroom scenario (primary use case)
Toby is with a client. Asks Cairn: "What are our options for an internal illuminated sign?"
Cairn searches across all CRM data semantically and returns past projects, materials, methods,
and guideline pricing in seconds. This requires hybrid BM25 + pgvector retrieval across
projects, quotes, emails, materials, and knowledge base entries.

## Connected modules
- Ledger (port 8016) — margins, revenue, cost data for pipeline prioritisation
- Manufacture (port 8015) — capacity, make list, for "should we quote this?" decisions
- Cairn API (port 8765) — memory, retrieval, business brain

## Decision Log

### 2026-04-04 — Project registered in Cairn
**Context**: CRM v2 spec provided, upgrading from standalone to Cairn-connected
**Decision**: Registered as Cairn project. Codebase on C: drive (confirm path with Toby).
Full spec at D:\claw\projects\crm\CRM_V2_SPEC.md
**Rejected**: Moving off Vercel (too much migration risk for Phase 1)

### 2026-04-04 — Hosting and email decisions
**Context**: Toby confirmed Vercel is deprecated, CRM moves to Hetzner/local server
**Decision**: Host on Hetzner (or local NBNE server) with daily Contabo backups.
Three email sources: cairn@nbnesigns.com (new dedicated inbox), sales@nbnesigns.co.uk
(existing sales inbox for historical context), toby@nbnesigns.com (existing for
direct client correspondence). All three feed into CRM semantic memory.
**Rationale**: Sovereign hosting, Ollama embeddings available locally, no Vercel
timeout limits for email worker, Contabo backup aligns with other module backup strategy
**Rejected**: Staying on Vercel (deprecated, can't reach local Ollama, cron limits)
