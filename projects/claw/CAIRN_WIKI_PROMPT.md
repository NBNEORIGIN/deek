# CAIRN WIKI LAYER — Implementation Prompt
# Compiled Knowledge + Visual Map
# North By North East Print & Sign Ltd
# Date: 6 April 2026 (reviewed and corrected)

---

## Before You Start

Read CLAUDE.md and CAIRN_PROTOCOL.md before starting.
Pull memory for project "claw" before starting.
Read the original spec: D:\claw\projects\claw\CAIRN_WIKI_CC_PROMPT_ORIGINAL.md
This prompt supersedes it with corrections.

This brief modifies Cairn's core retrieval architecture — understand the
existing pgvector + BM25 hybrid search before writing code.

---

## Corrections From Review (IMPORTANT — read before implementing)

The original spec has these errors. Do NOT copy them into the code:

1. **Etsy Intelligence is missing from the graph.** Add it as a `sell`
   category node with edges to Cairn and Manufacture. 8 nodes, not 7.

2. **Table name is wrong.** The spec says `memory_chunks` — the actual
   table is `claw_code_chunks` in PostgreSQL. Check the real schema:
   ```sql
   SELECT column_name FROM information_schema.columns
   WHERE table_name = 'claw_code_chunks';
   ```
   The retrieval boost (Section 6) must target this table, not a
   fictional `memory_chunks` table.

3. **No local Ollama on Hetzner.** The Cairn API on Hetzner runs
   `CLAW_FORCE_API=true` — no Ollama. Wiki compilation must use:
   - **DeepSeek** via existing API key (cheapest, ~£0.20/1M input)
   - **OpenRouter** as fallback (access to Llama 70B, Mixtral, etc.)
   OpenRouter API key: pull from Cairn memory (reference_openrouter_api.md)
   or env var `OPENROUTER_API_KEY`
   OpenRouter base URL: https://openrouter.ai/api/v1 (OpenAI SDK compatible)
   Do NOT reference local Qwen for compilation — it only runs on the
   Alnwick workstation, not on Hetzner where the API lives.

4. **Phloe article content is wrong.** Do NOT hardcode the article from
   the spec. Instead:
   - Pull current tenant data from Cairn memory and Phloe context endpoint
   - Co-Director is **Joanne Tompkins**, not Jo Fletcher
   - DemNurse is **Amy Law** (nursing), not "Real Fitness (Ami)"
   - Real Fitness is a PROSPECT, not a current tenant — include as
     planned/future in the Phloe article
   - Read projects/*/core.md files for accurate, current information

5. **`manage.py` is Django — Cairn is FastAPI.** The compilation schedule
   (Section 8) references Django management commands. Replace with:
   - FastAPI endpoint: `POST /api/wiki/compile?scope=all|modules|products|clients`
   - Cron job calls the endpoint via curl
   - Or use Cairn's existing scheduling infrastructure

6. **The graph needs more nodes.** The original spec has 7 modules.
   The current NBNE ecosystem includes these additional planned modules:
   - **Etsy Intelligence** (sell) — live, mirrors AMI
   - **Ledger-Light** (measure) — planned, lightweight bookkeeping embedded
     in Phloe for tenants. Connects to Phloe and Ledger.
   - **Meridian** (brain) — planned, AI-powered trading bot. Standalone.
   Total: 10 nodes (7 existing + Etsy + Ledger-Light + Meridian).
   Planned modules should render with a dashed border or lighter opacity.

## What This Does (unchanged from spec)

Adds a compiled wiki layer on top of the existing pgvector store. An LLM
reads raw data from all modules, writes structured markdown articles with
backlinks, and indexes them with a retrieval boost. One retrieval returns
a complete, contextualised article instead of five disconnected chunks.

The wiki's module articles also power a visual map on cairn.nbnesigns.co.uk
— an interactive node graph showing all NBNE apps and how they interconnect.

**This is not a replacement for pgvector.** It is a layer on top.

---

## Infrastructure

### Database
PostgreSQL on nbne1 (192.168.1.228), database: claw
Connection: postgresql://cairn:cairn_nbne_2026@192.168.1.228:5432/claw
Existing table for chunks: `claw_code_chunks`
Wiki articles get `chunk_type = 'wiki'` in this table (not a new table)

### Hetzner deployment
Cairn API: 178.104.1.152, port 8765 (Docker container deploy-cairn-api-1)
After changes: `ssh root@178.104.1.152 "cd /opt/nbne/cairn && git pull && cd deploy && docker compose --env-file .env up -d --build cairn-api"`

### Web interface
cairn.nbnesigns.co.uk, web-business container
After changes: rebuild and restart cairn-web

### Compilation models
| Model | Use for | Cost |
|---|---|---|
| DeepSeek (deepseek-chat) | Product articles, blank articles, marketplace articles | ~£0.20/1M in |
| OpenRouter (llama-3-70b or mixtral) | Client articles (need nuance for email summarisation) | ~£0.50/1M in |
| Claude Sonnet | Module articles only (7 total, accuracy critical) | ~£0.24/1M in |

Env vars needed:
```
DEEPSEEK_API_KEY=<existing>
OPENROUTER_API_KEY=<from Cairn memory>
```

---

## Sub-Agent Usage

| Task | Assign to | Cost tier |
|---|---|---|
| Directory structure, file creation | Sonnet sub-agent | ~£0.24/1M in |
| Module article writing | Sonnet sub-agent (read core.md files first) | ~£0.24/1M in |
| Graph JSON generation | Sonnet sub-agent | ~£0.24/1M in |
| Visual map React component | Sonnet sub-agent | ~£0.24/1M in |
| Retrieval boost (Section 6) | Opus (yourself) — core architecture change | ~£1.20/1M in |
| Compilation job | Sonnet sub-agent | ~£0.24/1M in |
| API endpoints | Sonnet sub-agent | ~£0.24/1M in |

---

## Memory Protocol

Before each section:
  retrieve_codebase_context(query=<section>, project="claw", limit=10)
  retrieve_chat_history(query=<section>, project="claw", limit=10)

After each section:
  update_memory(project="claw", query=..., decision=..., rejected=...,
                outcome="committed", model=..., files_changed=[...])

## Cost Logging

After every section:
  log_cost(session_id=<session>, prompt_summary=<one line>, project="claw",
           costs=[{model, tokens_in, tokens_out, cost_gbp}], total_cost_gbp=X)

---

## Build Order

### Step 1: Directory structure + module articles (Sections 1-2)

Create wiki/ directory. Write module articles by reading projects/*/core.md
and Cairn memory — do NOT copy the Phloe article from the spec verbatim
(it has errors). Write articles for: phloe, manufacture, ledger, cairn,
amazon-intelligence, etsy-intelligence, crm, render.

Each article follows the template in the spec. Use accurate, current data.

Commit: `feat(cairn): wiki directory structure and module articles`

### Step 2: Graph data + visual map (Sections 3-4)

Generate graph.json with 10 nodes (7 live + Etsy + Ledger-Light + Meridian).
Planned modules get `"status": "planned"`.

For the visual map component: use `react-force-graph-2d`. The graph will
grow beyond 10 nodes as more modules are added — a force-directed layout
scales better than hand-positioned SVG. Install the dependency:
`cd web-business && npm install react-force-graph-2d`

Add /map route to cairn.nbnesigns.co.uk. Add to sidebar navigation.

Commit: `feat(cairn): module graph data and interactive visual map`

### Step 3: Retrieval boost (Section 6)

THIS IS AN OPUS-LEVEL ARCHITECTURAL CHANGE.

Modify the existing retrieval in `core/memory/retriever.py` to:
1. Add wiki boost: `source_type == 'wiki'` results get score *= 1.5
2. Deduplicate: wiki wins over raw for same entity
3. Follow backlinks: one level deep, max 3 linked articles

The actual table is `claw_code_chunks`. Check the schema first.
The `chunk_type` column already exists — use it to identify wiki chunks
(`chunk_type = 'wiki'`).

Do NOT create a new `memory_chunks` table. Work with the existing schema.

Commit: `feat(cairn): wiki retrieval boost and backlink following`

### Step 4: API endpoints + layer integration (Section 7)

Add to Cairn API (api/main.py):
```
GET  /api/wiki/search?q=&top_k=
GET  /api/wiki/graph
GET  /api/wiki/article/{path}
POST /api/wiki/compile?scope=
GET  /api/wiki/status
```

Wire wiki search into the web chat stream proxy alongside existing
CRM search and module context injection.

Add /map to sidebar navigation on cairn.nbnesigns.co.uk.

Commit: `feat(cairn): wiki API endpoints and layer integration`

### Step 5: Compilation job + schedule (Sections 5, 8)

Build the compilation pipeline using DeepSeek (products) and OpenRouter
(clients). Module articles are manual/Sonnet.

Trigger via `POST /api/wiki/compile`. Schedule weekly via cron calling
the endpoint, not Django manage.py.

Commit: `feat(cairn): wiki compilation job and schedule`

### Step 6: Deploy to Hetzner

Rebuild both cairn-api and cairn-web. Test:
- /map renders the visual graph
- Click a node → article panel opens
- Wiki articles appear in chat responses
- Compilation endpoint works

Commit: deployment, no code changes

---

## Constraints

- Follow CLAUDE.md procedure on every task
- One logical change per commit, conventional messages
- Write back to Cairn memory after each section
- Log costs after each section
- The retrieval boost (Step 3) must not break existing retrieval for
  projects that have no wiki articles
- Module articles must be accurate — read core.md files, don't invent

The code stays in Northumberland. The knowledge compounds.
