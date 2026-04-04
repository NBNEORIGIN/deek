# CRM v2 — Cairn-Integrated Business Development Platform
# Implementation Session

---

Read CLAUDE.md and CAIRN_PROTOCOL.md before starting.
Pull memory for projects "crm", "claw", and "manufacturing" before starting.
Read D:\claw\projects\crm\CRM_V2_SPEC.md — the full specification.
Read D:\claw\projects\crm\core.md — current state and decisions.

---

## Context

You are implementing the CRM v2 upgrade for NBNE. The full spec is in
CRM_V2_SPEC.md. Read it completely before writing any code.

Key points:
- CRM will be deployed to crm.nbnesigns.co.uk (Hetzner — new deployment)
- GitHub: https://github.com/NBNEORIGIN/crm
- Pipeline: £50,309 across 37 projects
- Primary use case: semantic search — "what are our options for an illuminated
  sign?" returns past projects, materials, methods, and pricing from memory
- Architecture: Cairn sits above all modules. No direct DB access between modules.

## UI Changes (IMPORTANT — read before touching the frontend)

The current CRM has a 4-pane cards layout for projects plus a table toggle.
The snapshot overview / AI insights panel is unused.

**Changes required:**

1. **Drop the 4-pane cards layout.** The project list should be TABLE VIEW
   ONLY as the default. No cards toggle. Clean, sortable table with columns:
   Reference, Project, Client, Stage, Value, Last Contact, Lead Source.

2. **Replace the snapshot/insights panel** with a **Cairn chat interface**.
   This is the same streaming chat component used on cairn.nbnesigns.co.uk
   (see D:\claw\web-business\src\app\(authenticated)\ask\page.tsx for the
   pattern). Key differences for the CRM version:
   - Scoped to CRM data: queries hit GET /api/cairn/search (hybrid BM25 +
     cosine over crm_embeddings) instead of general Cairn memory
   - The chat panel sits alongside the project table (sidebar or split view)
   - Staff can ask: "what options do we have for illuminated signs?",
     "show me all work for golf clubs", "what materials did we use on
     the Glendale Show signs?"
   - Uses SSE streaming from the Cairn API, same EventSource pattern
   - Include the 🎤 voice-to-ask button (same as cairn.nbnesigns.co.uk)
   - Include the 🔊 Listen button on responses
   - Include the 💾 Remember this button on responses

3. **Keep the existing left nav** (Projects, Clients, Materials, Suppliers,
   Knowledge Base) but add a "New Lead from Email" flow that creates leads
   from the email integration (Phase 2 — just keep the button for now).

4. **Keep Live Recommendations** but wire them to Cairn recommendations
   from POST /api/cairn/memory write-back (Phase 1, step 9).

## Infrastructure (CONFIRMED)

### Database
PostgreSQL + pgvector on nbne1 RAID server. Create the CRM database:
```
# Credentials: see Cairn memory (reference_local_server.md) or .env
ssh toby@192.168.1.228
sudo -u postgres createdb cairn_crm
sudo -u postgres psql -d cairn_crm -c "GRANT ALL PRIVILEGES ON DATABASE cairn_crm TO cairn;"
sudo -u postgres psql -d cairn_crm -c "CREATE EXTENSION IF NOT EXISTS vector;"
sudo -u postgres psql -d cairn_crm -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
```
Connection string: set `CRM_DATABASE_URL` in `.env` (see `.env.example`)
Format: `postgresql://cairn:<password>@192.168.1.228:5432/cairn_crm`
Backup: automatic — nightly Contabo backup covers all nbne1 databases.
Add cairn_crm to the backup list in /data/cairn/backup.sh.

### Hosting
Deploy to Hetzner (178.104.1.152) as Docker containers.
CRM ports: backend 8003, frontend 3003.
Nginx reverse proxy: crm.nbnesigns.co.uk → 127.0.0.1:3003
SSL: Cloudflare origin cert at /etc/ssl/cloudflare/nbne/origin.pem (wildcard *.nbnesigns.co.uk)

### Embeddings
Use OpenAI text-embedding-3-small (768 dims) — CRM is on Hetzner, not the
local server with Ollama. ~£0.01 per 1M tokens. Consistent with Cairn's
existing batch indexer approach (see core/context/indexer.py).

### Email (3 sources — Phase 2, but design schema now)
| Email | Purpose | Access |
|---|---|---|
| cairn@nbnesigns.com | Dedicated CRM inbox. Cairn drafts, humans approve+send | IMAP + SMTP (IONOS) |
| sales@nbnesigns.co.uk | Existing sales inbox — ingest for context | IMAP read-only |
| toby@nbnesigns.com | Client correspondence — ingest for context | IMAP read-only |

Toby will create cairn@nbnesigns.com and provide all credentials.

## Existing Data on nbne1

Other databases on the same server (for cross-module queries via API, NOT direct access):
- claw (136 MB) — Cairn core
- amazon_manager (53 MB) — Amazon Intelligence
- manufacture (13 MB) — Manufacturing
- ledger (9.5 MB) — Financial system

## Sub-Agent Usage

| Task | Assign to | Cost tier |
|---|---|---|
| Codebase exploration, file reads | Explore agent (Sonnet) | ~£0.24/1M in |
| SQL schema, boilerplate routes, CRUD | Sonnet sub-agent | ~£0.24/1M in |
| Embedding pipeline, search implementation | Sonnet sub-agent | ~£0.24/1M in |
| Hybrid search (BM25+cosine+RRF), architecture | Opus (yourself) | ~£1.20/1M in |
| Docker/nginx config, deployment | Sonnet sub-agent | ~£0.24/1M in |
| UI components (chat panel, table view) | Sonnet sub-agent | ~£0.24/1M in |

## Memory Protocol

Before each phase:
  retrieve_codebase_context(query=<phase>, project="crm", limit=10)
  retrieve_chat_history(query=<phase>, project="crm", limit=10)

After each phase:
  update_memory(project="crm", query=..., decision=..., rejected=...,
                outcome="committed", model=..., files_changed=[...])

## Cost Logging

After every phase:
  log_cost(session_id=<session>, prompt_summary=<one line>, project="crm",
           costs=[{model, tokens_in, tokens_out, cost_gbp}], total_cost_gbp=X)

## Build Order (Phase 1 only — this session)

0. Register CRM project in Cairn:
   - Create projects/crm/config.json with codebase_path: "D:\\crm"
   - POST /index?project=crm to seed the index
   - Verify: GET /projects returns "crm"
   (This enables Steps 1+ to use memory retrieval correctly)

1. Clone repo: git clone https://github.com/NBNEORIGIN/crm D:\crm
   Audit current codebase — schema, API routes, existing Llama/RAG, UI components
   (Use Explore sub-agent)

2. Create cairn_crm database on nbne1 (SSH commands above)

3. Enable pgvector + pg_trgm, create crm_embeddings table with HNSW + GIN
   (Schema in CRM_V2_SPEC.md — delegate to Sonnet)

4. Build embedding pipeline — on project/client/material/quote CRUD,
   embed and upsert. Use OpenAI text-embedding-3-small (768 dims).
   (Delegate to Sonnet, review before committing)

5. Backfill crm_embeddings from all existing projects, clients, materials, KB
   (Delegate to Sonnet)

6. Build GET /api/cairn/search — hybrid BM25 + cosine + RRF merge
   THIS IS THE CORE. Opus-level. Get this right.

6b. **Test hybrid search** — integration tests against real pgvector DB.
    Test cases: exact match, semantic match, empty results, multi-entity
    (e.g. "illuminated signs for golf clubs" should hit projects + materials).
    Do not proceed to step 7 until search quality is verified.

7. Migrate existing RAG Knowledge Search to unified crm_embeddings index

8. Build GET /api/cairn/context — pipeline summary, follow-ups, email digest
   (Delegate to Sonnet, response schema in CRM_V2_SPEC.md)

9. Build POST /api/cairn/memory — write-back for recommendations
   (Delegate to Sonnet)

10. **UI: Replace 4-pane cards with table-only project list**
    Drop the cards/table toggle. Table is the default and only view.
    Columns: Reference, Project, Client, Stage, Value, Last Contact, Lead Source.
    Sortable by any column. Filterable by stage.
    (Delegate to Sonnet)

11. **UI: Replace snapshot/insights panel with Cairn chat**
    Build a chat panel (sidebar or right-side split) that queries
    GET /api/cairn/search with SSE streaming. Include:
    - Voice input (🎤 mic button, Whisper transcription)
    - Text-to-speech (🔊 Listen button on responses)
    - Remember this (💾 saves to Cairn memory)
    Follow the pattern from D:\claw\web-business\src\app\(authenticated)\ask\page.tsx
    (Delegate to Sonnet for structure, review the search integration yourself)

12. Wire Live Recommendations to Cairn recommendations table

13. Add project reference system NBNE-YYYY-NNN (Sonnet)

14. Add lead_source field + client lifetime_value + last_contact_at (Sonnet)

15. Create emails + cairn_recommendations tables (schema ready for Phase 2)
    (Sonnet — schema in CRM_V2_SPEC.md)

16. Create Dockerfile + docker-compose for Hetzner deployment
    (Sonnet — follow D:\claw\deploy\docker-compose.yml pattern)

17. Deploy to Hetzner: build, push, nginx config, test

18. Wire to cairn.nbnesigns.co.uk dashboard:
    Update D:\claw\web-business\src\app\api\context\route.ts:
    { key: 'crm', name: 'Customers', url: `${process.env.CRM_API_URL || 'http://localhost:8003'}/api/cairn/context` }
    Note: on Hetzner, use the Docker bridge IP or container name, not host.docker.internal (Linux-only limitation).

Commit after each step. One logical change per commit.
Write back to Cairn memory after each step.

## Phases 2-4 (separate sessions)
- Phase 2: Email integration (cairn@, sales@, toby@ IMAP ingestion, classification, project matching)
- Phase 3: Pipeline intelligence (conversion analytics, stale alerts, client LTV)
- Phase 4: Google Ads integration (future)

Only build Phase 1 in this session.

## Constraints

- Human-in-the-loop for all outbound communication
- No direct DB access between modules — API only
- GDPR: client data export + deletion must work
- Conventional commit messages, atomic commits
- Write back to memory after every phase
- Log costs after every phase

The code stays in Northumberland.
