# Cairn

## What It Does
NBNE's sovereign AI development memory system. Runs on NBNE hardware and replaces
cloud-based coding assistants. Cairn remembers every decision, dead end, and
workaround across all projects. It also serves as the business brain — assembling
live data from all modules into a unified context that staff can query in plain
English.

## Who Uses It
- **Toby Fletcher** — business queries, project direction, the "boardroom scenario"
- **Claude Code** — principal developer, uses Cairn's memory on every task
- **Qwen / DeepSeek** — junior developers, delegated mechanical tasks
- **Staff** — web dashboard for business questions

## Tech Stack
- Backend: FastAPI + PostgreSQL (pgvector) on nbne1 (192.168.1.228)
- Frontend: Next.js (web-business, cairn.nbnesigns.co.uk)
- Retrieval: Hybrid BM25 + pgvector cosine similarity with RRF fusion
- Embeddings: nomic-embed-text (768-dim)
- Session storage: SQLite per project
- Hosting: Hetzner (178.104.1.152, port 8765) + local development (D:\claw)
- MCP: cairn_mcp_server.py exposes 5 tools to Claude Code

## Connections
- **Feeds data to:** All modules (memory retrieval, context assembly)
- **Receives data from:** [[modules/phloe]] (context), [[modules/manufacture]] (context),
  [[modules/ledger]] (context), [[modules/amazon-intelligence]] (context),
  [[modules/etsy-intelligence]] (context), [[modules/crm]] (semantic search)
- **Context endpoint:** Cairn IS the context layer — it queries all other modules

## Current Status
- Build phase: Production (API, web UI, MCP server, wiki layer)
- Last significant change: Wiki layer implementation (April 2026)
- Known issues: RTX 1050 limits local inference; RTX 3090 arriving for upgrade
- Indexing: Active for all registered projects (pgvector + nomic-embed-text)

## Key Concepts
- **3-tier context:** Tier 1 (core.md), Tier 2 (hybrid BM25+pgvector), Tier 3 (on-demand file reads)
- **Memory write-back:** Every non-trivial task writes decisions back to Cairn
- **Delegation protocol:** Tasks classified by complexity → assigned to appropriate model tier
- **Business brain:** Assembles live data from all modules for natural language queries
- **Wiki layer:** Compiled knowledge articles with retrieval boost over raw chunks
- **Make → Measure → Sell:** The NBNE value chain that Cairn's modules map to

## API Endpoints

### Core retrieval and memory
- `GET  /retrieve?query=&project=&limit=` — hybrid BM25 + pgvector retrieval
- `GET  /memory/retrieve?query=&project=` — chat history retrieval
- `POST /memory/write` — write decision back to memory
- `POST /index?project=` — trigger project reindex

### Wiki layer
- `GET  /api/wiki/search?q=&top_k=` — semantic wiki search
- `GET  /api/wiki/article/{path}` — single article as markdown
- `POST /api/wiki/compile?scope=` — trigger recompilation (all|modules|products|clients)
- `GET  /api/wiki/status` — article counts and compilation status

### Freshness and discoverability (added April 2026)
- `POST /api/cairn/notify` — modules signal data changes for async wiki recompilation
- `GET  /api/cairn/catalogue` — full ecosystem snapshot: modules, wiki, pgvector, audit

## Module Integration Pattern

When a module ingests new data, it should POST to `/api/cairn/notify`:

```bash
curl -X POST http://localhost:8765/api/cairn/notify \
     -H "Content-Type: application/json" \
     -d '{
       "module": "amazon_intelligence",
       "event_type": "snapshot_completed",
       "scope": "products",
       "affected_entities": ["M0001", "M0042"],
       "occurred_at": "2026-04-07T14:30:00Z"
     }'
```

Valid `scope` values: `products`, `clients`, `modules`, `marketplaces`, `blanks`.
Valid `event_type` values: `snapshot_completed`, `data_ingested`, `schema_changed`.

Cairn queues the scope for recompilation and re-embedding asynchronously.
The module does not wait — fire and forget.

First integration: Amazon Intelligence calls notify after each weekly snapshot.
Future modules add the call when they are next touched for other reasons.

## Session Start Usage

Every CC session should call the catalogue before any retrieval:

```
GET http://localhost:8765/api/cairn/catalogue
```

Returns: registered modules, wiki article status, context endpoint reachability,
pgvector chunk counts per project, recompile queue state, and audit warnings.

## Freshness Architecture

- **notify endpoint**: modules push change events → items queued in `wiki_recompile_queue`
- **recompile worker**: background task processes queue every 5 minutes (or immediately on signal)
- **daily audit**: runs at 06:00 UTC — catches stale articles even if notify was missed
- **catalogue endpoint**: assembles current ecosystem state from registry + live checks

## Related
- [[modules/phloe]] — largest module, most active development
- [[modules/manufacture]] — production data feeds business brain
- [[modules/amazon-intelligence]] — listing health in dashboard
- [[modules/etsy-intelligence]] — Etsy sales data in dashboard
