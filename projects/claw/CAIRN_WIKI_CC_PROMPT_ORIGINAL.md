# CAIRN WIKI LAYER — Claude Code Implementation Brief
# Compiled Knowledge + Visual Map
# North By North East Print & Sign Ltd
# Date: 6 April 2026

---

## Before You Start

Read CLAUDE.md and CAIRN_PROTOCOL.md before starting.
Pull memory for project "claw" before starting.
This brief modifies Cairn's core retrieval architecture — understand the
existing pgvector + BM25 hybrid search before writing code.

---

## What This Does

Cairn currently retrieves raw chunks from pgvector. Chunks are paragraphs
ripped from their context — they answer "what" but not "why" or "how does
this relate to everything else." Staff and developer prompts burn tokens
reassembling context from multiple chunks.

This brief adds a compiled wiki layer that sits on top of the existing
vector store. An LLM reads raw data from all modules, writes structured
markdown articles with backlinks, and indexes them into pgvector with a
retrieval boost. The result: one retrieval returns a complete, contextualised
article instead of five disconnected chunks.

Additionally, the wiki's module articles power a visual map on the Cairn
web interface — an interactive node graph showing all NBNE apps and how
they interconnect. Staff click a node, get the wiki article. They finally
understand what all these systems are and how they relate.

**This is not a replacement for pgvector.** It is a layer on top. Raw
chunks remain for topics the wiki hasn't compiled yet. The wiki is the
preferred retrieval source when it exists.

---

## Section 1 — Wiki Directory Structure

Create the wiki directory within the Cairn project:

```
D:\claw\wiki\
├── modules/
│   ├── phloe.md
│   ├── manufacture.md
│   ├── ledger.md
│   ├── cairn.md
│   ├── amazon-intelligence.md
│   ├── crm.md
│   ├── render.md
│   └── graph.json
├── products/
│   └── (compiled per M-number once AMI is running)
├── blanks/
│   └── (compiled per blank name)
├── marketplaces/
│   └── (compiled per marketplace)
├── clients/
│   └── (compiled from CRM data)
├── processes/
│   └── (compiled from SOPs and process docs)
├── index.md
└── _meta/
    ├── last_compiled.json
    ├── compilation_log.json
    └── schema.json
```

### index.md

The master index. The LLM reads this first when navigating the wiki.

```markdown
# NBNE Knowledge Base

## Modules
- [[modules/phloe]] — Multi-tenant booking platform (WaaS)
- [[modules/manufacture]] — Production intelligence and make-list
- [[modules/ledger]] — Financial management and accounts
- [[modules/cairn]] — AI memory and orchestration layer
- [[modules/amazon-intelligence]] — Listing health and performance
- [[modules/crm]] — Client relationships and B2B pipeline
- [[modules/render]] — Product publishing across marketplaces

## Products
See [[products/index]] for full catalogue (compiled from Manufacture + AMI)

## Blanks
See [[blanks/index]] for substrate reference (DONALD, SAVILLE, DICK, etc.)

## Marketplaces
- [[marketplaces/UK]] — Primary market, ~328 ASINs
- [[marketplaces/US]] — ~310 ASINs
- [[marketplaces/CA]] — ~248 ASINs
- [[marketplaces/AU]] — ~231 ASINs

## Clients
See [[clients/index]] for B2B client directory (compiled from CRM)

## Processes
See [[processes/index]] for SOPs and workflows

Last compiled: {timestamp}
```

### Backlink format

Use `[[relative/path]]` for internal links. The compilation job resolves
these to actual file paths. The retrieval layer follows backlinks one
level deep when a wiki article is retrieved — if the brain retrieves
`products/M0001.md`, it also loads `blanks/DONALD.md` and
`marketplaces/UK.md` if referenced.

**Commit:** `feat(cairn): wiki directory structure and index`

---

## Section 2 — Module Articles (Build First)

These are the foundation. Write them manually for the first version —
they describe stable architecture, not changing data. Future compilations
update the status sections automatically.

### Article template for modules

```markdown
# {Module Name}

## What It Does
{2-3 sentence description a non-developer can understand}

## Who Uses It
{Team members and their roles with this module}

## Tech Stack
{Framework, database, hosting — one line each}

## Connections
- **Feeds data to:** [[module1]], [[module2]]
- **Receives data from:** [[module3]]
- **Context endpoint:** `GET /api/cairn/context` — {what it returns}

## Current Status
- Build phase: {Phase N}
- Last significant change: {date and description}
- Known issues: {list or "None"}

## Key Concepts
{Domain vocabulary specific to this module — e.g. M-numbers for Manufacture,
booking paradigms for Phloe, health scores for AMI}

## Related
- [[processes/deployment]] — how to deploy changes
- [[products/index]] — product data flows through this module
```

### Write these articles now

**modules/phloe.md:**

```markdown
# Phloe

## What It Does
Multi-tenant booking platform. Each tenant (hair salon, gym, restaurant,
nurse) gets their own branded booking page where their clients can book
appointments, classes, tables, or food orders. Phloe handles the booking
logic, reminders, payments, and client communications.

## Who Uses It
- **Toby** — development and infrastructure
- **Jo** — tenant onboarding and support
- **Tenants** — DemNurse (Jody), Ganbaru Kai, Real Fitness (Ami),
  Amble Pin Cushion (Norma), Tavola, Pizza Shack

## Tech Stack
- Backend: Django 5.x + PostgreSQL
- Frontend: Next.js
- Hosting: Hetzner (nbne.uk)
- Email: Postmark (migrating from SMTP)
- Payments: Stripe

## Connections
- **Feeds data to:** [[modules/ledger]] (booking revenue),
  [[modules/cairn]] (context endpoint)
- **Receives data from:** None (standalone SaaS)
- **Context endpoint:** `GET /api/cairn/context` — tenant count,
  booking volume, active paradigms

## Current Status
- Build phase: Production (4 booking paradigms live)
- Last significant change: Postmark email migration (April 2026)
- Known issues: Locale-awareness needed for international expansion
- Next priority: Locale compliance packs

## Key Concepts
- **Booking paradigms:** appointment, class/timetable, table reservation,
  food ordering
- **Tenants:** independent businesses using Phloe
- **WaaS:** Workflow-as-a-Service — Phloe's market positioning

## Related
- [[processes/tenant-onboarding]]
- [[modules/ledger]] — booking revenue feeds financial reporting
```

Write equivalent articles for: **manufacture**, **ledger**, **cairn**,
**amazon-intelligence**, **crm**, **render**.

Use the information in `projects/*/core.md` files and CAIRN_PROTOCOL.md
as source material. Each article should be accurate, concise, and written
for a non-developer audience (Jo, Ben, Gabby should be able to read them).

**Commit:** `feat(cairn): module wiki articles`

---

## Section 3 — Graph Data for Visual Map

Generate `wiki/modules/graph.json` from the module articles. Parse the
`## Connections` section of each article and build a node-edge structure:

```json
{
  "nodes": [
    {
      "id": "phloe",
      "label": "Phloe",
      "description": "Multi-tenant booking platform",
      "status": "production",
      "article_path": "wiki/modules/phloe.md",
      "category": "sell"
    },
    {
      "id": "manufacture",
      "label": "Manufacture",
      "description": "Production intelligence and make-list",
      "status": "development",
      "article_path": "wiki/modules/manufacture.md",
      "category": "make"
    },
    {
      "id": "ledger",
      "label": "Ledger",
      "description": "Financial management and accounts",
      "status": "development",
      "article_path": "wiki/modules/ledger.md",
      "category": "measure"
    },
    {
      "id": "cairn",
      "label": "Cairn",
      "description": "AI memory and orchestration",
      "status": "production",
      "article_path": "wiki/modules/cairn.md",
      "category": "brain"
    },
    {
      "id": "amazon-intelligence",
      "label": "Amazon Intelligence",
      "description": "Listing health and performance analysis",
      "status": "development",
      "article_path": "wiki/modules/amazon-intelligence.md",
      "category": "sell"
    },
    {
      "id": "crm",
      "label": "CRM",
      "description": "Client relationships and B2B pipeline",
      "status": "development",
      "article_path": "wiki/modules/crm.md",
      "category": "sell"
    },
    {
      "id": "render",
      "label": "Render",
      "description": "Product publishing across marketplaces",
      "status": "development",
      "article_path": "wiki/modules/render.md",
      "category": "make"
    }
  ],
  "edges": [
    {"from": "phloe", "to": "ledger", "label": "booking revenue"},
    {"from": "phloe", "to": "cairn", "label": "context endpoint"},
    {"from": "manufacture", "to": "cairn", "label": "context endpoint"},
    {"from": "manufacture", "to": "amazon-intelligence", "label": "M-number + margin data"},
    {"from": "ledger", "to": "cairn", "label": "context endpoint"},
    {"from": "amazon-intelligence", "to": "cairn", "label": "context endpoint"},
    {"from": "amazon-intelligence", "to": "render", "label": "improvement queue"},
    {"from": "crm", "to": "cairn", "label": "semantic search"},
    {"from": "render", "to": "manufacture", "label": "ASIN mapping"}
  ],
  "categories": {
    "make": {"colour": "#4A90D9", "label": "Make"},
    "measure": {"colour": "#50C878", "label": "Measure"},
    "sell": {"colour": "#E8913A", "label": "Sell"},
    "brain": {"colour": "#9B59B6", "label": "Brain"}
  }
}
```

The categories follow the business value chain from CAIRN_MODULES.md:
**Make → Measure → Sell**, with Cairn as the brain that connects them.

**Commit:** `feat(cairn): module graph data`

---

## Section 4 — Visual Map Component

Build a React component for the Cairn web interface that renders the
module graph as an interactive node diagram.

### Requirements

- Reads `graph.json` from the wiki API endpoint
- Renders nodes as circles/cards, colour-coded by category
- Renders edges as lines/curves with labels
- Nodes are draggable (force-directed layout)
- Click a node → slide-in panel shows the wiki article (rendered markdown)
- Responsive — works on desktop and tablet
- Accessible at `/map` on cairn.nbnesigns.co.uk

### Tech choices

Use `react-force-graph-2d` or D3.js force simulation. The graph is small
(7 nodes, 9 edges) so performance isn't a concern. Prioritise readability
and visual clarity over animation complexity.

### Layout suggestion

```
                    ┌─────────┐
                    │  Cairn  │
                    │ (brain) │
                    └────┬────┘
                         │
          ┌──────────────┼──────────────┐
          │              │              │
    ┌─────┴─────┐  ┌─────┴─────┐  ┌────┴──────┐
    │Manufacture│  │  Ledger   │  │    CRM    │
    │  (make)   │  │ (measure) │  │  (sell)   │
    └─────┬─────┘  └───────────┘  └───────────┘
          │
    ┌─────┴─────┐
    │  Render   │──────────┐
    │  (make)   │          │
    └───────────┘    ┌─────┴──────┐
                     │  Amazon    │
                     │Intelligence│
                     │  (sell)    │
                     └────────────┘
                     
    ┌───────────┐
    │   Phloe   │
    │  (sell)   │
    └───────────┘
```

The force layout will arrange this naturally. Don't hardcode positions.

### Node card design

Each node shows:
- Module name (bold)
- One-line description
- Status indicator (green dot = production, amber = development, red = down)
- Category colour as border/accent

### Article panel

When a node is clicked, a panel slides in from the right showing the wiki
article rendered as HTML. Use a markdown renderer (e.g. `react-markdown`).
Include a "View full article" link that opens the article in a new tab.

### API endpoint

Add to the Cairn web API:

```
GET /api/wiki/graph
→ returns graph.json

GET /api/wiki/article/{path}
→ returns rendered markdown for a wiki article
```

**Commit:** `feat(cairn): interactive module map UI`

---

## Section 5 — Wiki Compilation Job

Build a compilation job that generates wiki articles from live data.
This runs on a schedule (weekly) or can be triggered manually.

### Compilation flow

```
1. Read source data from module APIs:
   - Manufacture: GET /api/cairn/context → products, blanks, M-numbers
   - Amazon Intelligence: GET /api/cairn/context → health scores, issues
   - Ledger: GET /api/cairn/context → financial summary
   - CRM: query pgvector for client summaries

2. For each entity (product, blank, marketplace, client):
   - Check _meta/last_compiled.json for last compilation timestamp
   - If source data is newer than last compilation: recompile
   - If no change: skip

3. For each article to compile:
   - Assemble the raw data into a structured prompt
   - Send to compilation LLM (local Qwen or DeepSeek via OpenRouter)
   - Validate output (must contain required sections, valid backlinks)
   - Write to wiki/{category}/{slug}.md
   - Update _meta/last_compiled.json

4. After all articles compiled:
   - Regenerate index.md
   - Regenerate graph.json (parse connections from module articles)
   - Re-embed updated articles into pgvector with source_type='wiki'
```

### Compilation prompt (products)

```
You are compiling a wiki article for NBNE's business knowledge base.

Write a concise article about product {m_number} using this data:

Product: {m_number} — {description}
Blank: {blank_name}
Marketplaces: {list of marketplace + ASIN + price + health score}
Content quality: {bullet_count} bullets, {image_count} images
Performance (30d): {sessions}, {conversion_rate}%, {units_sold} units
Ad performance: {ad_spend}, ACOS {acos}%
Margin: {gross_margin}%
Issues: {diagnosis_codes}
Recommendations: {recommendations}

Format:
# {M-number} — {Short Description}

## Overview
{2-3 sentences: what the product is, which blank it uses, where it sells}

## Performance
{Table: marketplace, ASIN, sessions, conversion, health score}

## Issues
{Current diagnosis codes with plain-English explanation}

## Recommendations
{Prioritised action list}

## Related
{Backlinks to blank page, marketplace pages, similar products}

Rules:
- Write for a non-developer audience
- Use plain English, not jargon
- Include backlinks using [[path]] format
- Be concise — aim for 200-400 words
- Do not invent data — only use what is provided above
```

### Compilation prompt (clients)

```
You are compiling a wiki article for NBNE's CRM knowledge base.

Write a concise client profile using this data:

Client: {name}
Company: {company}
Email history: {summary of email threads — topics, dates, outcomes}
Projects: {list of projects with status}
Last contact: {date}
Revenue: {total revenue from this client}
Notes: {any CRM notes}

Format:
# {Client Name} — {Company}

## Relationship Summary
{2-3 sentences: who they are, how long we've worked together, what we do for them}

## Project History
{Table: project, date, status, value}

## Recent Communications
{Last 3-5 email exchanges summarised in one sentence each}

## Next Steps
{Any pending actions, follow-ups, or opportunities}

## Related
{Backlinks to relevant products, processes, or other clients}

Rules:
- Write for Jo and Toby — they know the clients, this is a reference doc
- Never include sensitive information (bank details, personal data)
- Summarise email content, do not reproduce it
- Be concise — aim for 150-300 words
```

### Model routing for compilation

The compilation job is high-volume, low-complexity — perfect for cheap models:

- **Local Qwen 70B (once 3090 is running):** Free. Ideal default.
- **DeepSeek via OpenRouter:** Cheap fallback if local is unavailable.
- **Claude Sonnet:** Only for module articles (7 total, infrequent updates,
  need to be accurate about architecture).

The compilation prompt is structured and bounded. A 7B model could handle
product articles. Reserve larger models for client articles where the
email summarisation requires more nuance.

### Embedding wiki articles

After compilation, embed each article into pgvector:

```python
def embed_wiki_article(filepath: str):
    """
    Read the wiki article, split into chunks if > 1000 tokens,
    embed via the same pipeline as raw chunks, but tag with:
    - source_type: 'wiki'
    - source_path: 'wiki/products/M0001.md'
    - compiled_at: timestamp
    """
```

**Commit:** `feat(cairn): wiki compilation job`

---

## Section 6 — Retrieval Boost for Wiki Articles

Modify the existing retrieval pipeline to prefer wiki articles over
raw chunks when both match a query.

### Current retrieval flow

```
User query → embed query → cosine similarity search in pgvector
           → BM25 keyword search
           → hybrid score = α * cosine + (1-α) * BM25
           → return top-K chunks
```

### Updated retrieval flow

```
User query → embed query → cosine similarity search in pgvector
           → BM25 keyword search
           → hybrid score = α * cosine + (1-α) * BM25
           → apply wiki boost: if source_type == 'wiki', score *= 1.5
           → deduplicate: if a wiki article and a raw chunk cover
             the same entity, keep only the wiki article
           → follow backlinks: for each wiki article in top-K,
             load any [[linked]] articles (one level deep, max 3)
           → return top-K results (mix of wiki articles and raw chunks)
```

### Implementation

Add a `source_type` column to the memory chunks table if not present:

```sql
ALTER TABLE memory_chunks ADD COLUMN IF NOT EXISTS source_type
    TEXT DEFAULT 'raw';
CREATE INDEX IF NOT EXISTS idx_chunks_source_type
    ON memory_chunks(source_type);
```

In the retrieval function:

```python
def retrieve(query: str, top_k: int = 10, project: str = None):
    results = hybrid_search(query, top_k=top_k * 2, project=project)
    
    # Apply wiki boost
    for r in results:
        if r.source_type == 'wiki':
            r.score *= 1.5
    
    # Re-sort by boosted score
    results.sort(key=lambda r: r.score, reverse=True)
    
    # Deduplicate: wiki wins over raw for same entity
    seen_entities = set()
    deduped = []
    for r in results:
        entity = extract_entity(r)  # e.g. "M0001", "DONALD", "phloe"
        if entity and entity in seen_entities and r.source_type == 'raw':
            continue
        seen_entities.add(entity)
        deduped.append(r)
    
    # Follow backlinks from wiki articles (one level, max 3)
    final = []
    backlink_budget = 3
    for r in deduped[:top_k]:
        final.append(r)
        if r.source_type == 'wiki' and backlink_budget > 0:
            links = extract_backlinks(r.content)
            for link in links[:backlink_budget]:
                linked = load_wiki_article(link)
                if linked:
                    final.append(linked)
                    backlink_budget -= 1
    
    return final[:top_k]
```

### Backlink extraction

```python
import re

def extract_backlinks(content: str) -> list[str]:
    """Extract [[wiki/path]] links from markdown content."""
    return re.findall(r'\[\[([^\]]+)\]\]', content)

def load_wiki_article(path: str) -> MemoryChunk | None:
    """Load a wiki article by path from the database."""
    return db.query(
        "SELECT * FROM memory_chunks WHERE source_path = %s AND source_type = 'wiki'",
        [f"wiki/{path}.md"]
    )
```

**Commit:** `feat(cairn): wiki retrieval boost and backlink following`

---

## Section 7 — Wire Into Existing Layers

### Layer 1: Claude Code sessions

Update CLAUDE.md to include wiki awareness:

```markdown
## Wiki Layer
Cairn maintains a compiled wiki at `wiki/`. When retrieving context,
wiki articles are preferred over raw chunks for the same topic. If you
update a module's architecture or add a new feature, update the
corresponding wiki article in `wiki/modules/{module}.md`.

After significant changes, trigger a wiki recompilation:
  POST /api/wiki/compile?scope=modules
```

### Layer 2: Web interface

Update the stream proxy (`route.ts`) to include wiki context:

```typescript
// After existing CRM semantic search, add:
const wikiResults = await fetch(
  `${CAIRN_API}/api/wiki/search?q=${encodeURIComponent(userMessage)}&top_k=3`
);
const wikiContext = await wikiResults.json();

// Inject into the prompt:
if (wikiContext.articles.length > 0) {
  systemPrompt += `\n[WIKI CONTEXT]\n`;
  for (const article of wikiContext.articles) {
    systemPrompt += `${article.content}\n---\n`;
  }
  systemPrompt += `[END WIKI CONTEXT]\n`;
}
```

This means staff queries now get: personality → live business data →
CRM semantic search → wiki articles → their question. The wiki articles
provide the structured context that raw chunks lack.

### API endpoints

Add to the Cairn API:

```
GET  /api/wiki/search?q={query}&top_k={n}
     → hybrid search with wiki boost, returns articles

GET  /api/wiki/graph
     → returns graph.json for the visual map

GET  /api/wiki/article/{path}
     → returns a single wiki article as rendered markdown

POST /api/wiki/compile?scope={all|modules|products|clients}
     → triggers compilation job for specified scope

GET  /api/wiki/status
     → returns last compilation timestamps, article counts, health
```

**Commit:** `feat(cairn): wiki API endpoints and layer integration`

---

## Section 8 — Compilation Schedule

### Frequency per article type

| Article Type | Compilation Trigger | Model |
|---|---|---|
| Modules | Manual or after architecture changes | Claude Sonnet |
| Products | Weekly (after AMI snapshot) or on data upload | Local Qwen / DeepSeek |
| Blanks | Weekly (aggregated from product articles) | Local Qwen / DeepSeek |
| Marketplaces | Weekly (aggregated from product articles) | Local Qwen / DeepSeek |
| Clients | Weekly or after new CRM email ingestion | DeepSeek / Sonnet |
| Processes | Manual (when SOPs change) | Claude Sonnet |

### Scheduling

Add a Django management command or a cron job:

```bash
# Weekly compilation — Sunday night after AMI snapshot
0 23 * * 0  cd /path/to/cairn && python manage.py compile_wiki --scope=all

# Trigger after AMI data upload (called by the upload pipeline)
python manage.py compile_wiki --scope=products
```

### Compilation logging

Every compilation run writes to `_meta/compilation_log.json`:

```json
{
  "run_id": "2026-04-06-2300",
  "scope": "all",
  "articles_compiled": 47,
  "articles_skipped": 2506,
  "articles_failed": 0,
  "model_used": "deepseek-chat",
  "tokens_used": 124000,
  "cost_estimate": "£0.02",
  "duration_seconds": 340
}
```

Write to Cairn memory after each compilation run.

**Commit:** `feat(cairn): wiki compilation schedule and logging`

---

## Build Order

1. **Section 1 + 2:** Directory structure and module articles (manual write).
   Gives the wiki its skeleton. ~1 hour.

2. **Section 3 + 4:** Graph data and visual map component. Staff can see
   the module map immediately. ~2 hours.

3. **Section 6:** Retrieval boost. Wiki articles start winning over raw
   chunks in search. ~1 hour.

4. **Section 7:** Wire into both layers (CC and web). Wiki context
   appears in responses. ~1 hour.

5. **Section 5 + 8:** Compilation job and schedule. Products, blanks,
   clients start auto-compiling. ~2 hours.

Total: ~7 hours of CC time across 2-3 sessions.

The module articles and visual map (steps 1-2) deliver immediate staff
value. The compilation pipeline (step 5) delivers ongoing developer value
by reducing retrieval tokens on every subsequent query.

---

## Ongoing Rules

Follow CLAUDE.md procedure on every task.

### Write-Back Requirements

After Section 2 (module articles): write to `claw/core.md`
After Section 4 (visual map): write to `claw/core.md`
After Section 6 (retrieval boost): write to `claw/core.md` at Opus level
— this changes the core retrieval architecture

### Final commit:

```
feat(cairn): wiki layer — compiled knowledge, visual map, retrieval boost
```

The code stays in Northumberland. The knowledge compounds.
