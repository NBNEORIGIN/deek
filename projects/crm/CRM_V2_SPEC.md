# CRM v2 — Cairn-Integrated Business Development Platform
# North By North East Print & Sign Ltd
# Development Specification
# Date: 03 April 2026

---

## Purpose

The NBNE CRM is being upgraded from a standalone project management tool to a Cairn-connected business development platform. The end objective is to allow Cairn to function as a B2B project manager and business advisor — monitoring the sales pipeline, reading and triaging inbound emails, identifying follow-up opportunities, and providing cross-module intelligence by connecting CRM data with Ledger (margins), Manufacture (capacity), and Phloe (bookings).

This document is a CC (Claude Code) implementation prompt.

---

## Current State

**Live app**: crm.nbnesigns.co.uk
**Hosting**: New deployment — Hetzner Docker (previously undeployed)
**What exists** (from screenshot, 03 April 2026):

- Projects view (Cards / Table toggle)
- Revenue Overview: Total Pipeline £50,309 | Leads £17,450 (18 projects) | Quoted £25,716 (13 projects) | In Production £6,218 (6 projects) | Completed £0
- AI Business Insights panel (Powered by Llama — currently "Analyzing...")
- Revenue Forecast panel (AI Powered — currently "Analyzing trends...")
- AI Knowledge Search (Powered by RAG — natural language search over past projects)
- Live Recommendations (31 actions)
- Project list: Small Shed Door Sign, Shop Front Signage - Maid Just Right, Various Signage - Glendale Show, Bamburgh Golf Club signage, Paton & Co Estate Agents
- Left nav: Projects, Clients, Materials, Suppliers, Knowledge Base
- "New Lead from Email" button (top right)

**Stack**: Next.js, PostgreSQL + pgvector on nbne1, Llama for insights, RAG for knowledge search

---

## Architecture Principle

Every NBNE module owns its own data and exposes it via API. No module has direct database access to another module. Cairn is the memory layer that sits above all modules.

```
                        CAIRN
                   (memory + brain)
                         |
         ┌───────────────┼───────────────┐
         │               │               │
     Manufacture       Ledger          CRM ◄── YOU ARE HERE
       API              API             API
         │               │               │
      Render          Phloe           Google Ads
       API              API             API
```

CRM is the third module in the Make → Measure → Sell value chain. It answers: **are we reaching the right people, and are we closing the deals?**

---

## Semantic Memory Layer

### Why This Matters — The Boardroom Scenario

Toby is standing with a client in their boardroom. The client asks: "What are our options for an internal illuminated sign?" Toby gets his phone out and asks Cairn. Within seconds, Cairn responds with:

> "You've done three internal illuminated projects in the last 18 months. The Bamburgh Golf Club boardroom sign was a 1200x400mm LED-backlit acrylic panel at £1,850. Paton & Co got a halo-lit aluminium composite at £2,400. The Glendale Show stand used edge-lit acrylic at £680 but that was a smaller piece. Based on typical boardroom dimensions, I'd suggest the LED-backlit acrylic or halo-lit options. Guideline price for this type of project: £1,500–£2,800 depending on size and finish. Want me to draft a quote?"

That answer requires semantic search across project descriptions, email threads, materials used, prices quoted, and photos — not a SQL query on a status field. This is the same hybrid BM25 + cosine similarity retrieval architecture used in Cairn's codebase memory and in the other NBNE modules.

### Retrieval Architecture

CRM uses the same three-layer retrieval as all Cairn-connected modules:

**Layer 1 — BM25 (lexical/keyword)**
Fast keyword matching. When the query contains specific terms like "illuminated sign", "LED", "halo-lit", "boardroom", BM25 finds exact and near-exact matches in project descriptions, email bodies, material names, and knowledge base entries.

**Layer 2 — pgvector (semantic/cosine similarity)**
Embedding-based search using `nomic-embed-text` via Ollama (local) or an API fallback. Catches conceptual matches that BM25 misses — e.g., a project described as "backlit reception panel" is semantically similar to "internal illuminated sign" even though the keywords differ.

**Layer 3 — Hybrid RRF (Reciprocal Rank Fusion)**
Merges BM25 and pgvector results using RRF scoring, same as Cairn's codebase retrieval. This consistently outperforms either method alone.

### What Gets Indexed

Every piece of CRM data that could inform a business conversation:

| Source | Content Indexed | Embedding Strategy |
|--------|----------------|-------------------|
| Projects | Title, description, scope notes, materials used, methods, completion notes | One chunk per project, re-embedded on update |
| Emails | Subject + body (plain text), per email | One chunk per email, embedded on ingestion |
| Clients | Company name, notes, sector, past project summaries | One chunk per client, re-embedded on update |
| Materials | Material name, description, typical use cases, supplier | One chunk per material |
| Knowledge Base | Existing KB entries (already RAG-indexed) | Migrate to unified index |
| Quotes | Line items, pricing, specifications | One chunk per quote |
| Photos/Attachments | Filename, alt text, project association (not the image itself) | Metadata chunk per attachment |

### Neon pgvector Setup

```sql
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Unified semantic index table
CREATE TABLE crm_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type TEXT NOT NULL,          -- project, email, client, material, kb, quote
    source_id UUID NOT NULL,            -- FK to source table
    content TEXT NOT NULL,              -- the text that was embedded
    embedding vector(768),             -- nomic-embed-text dimension
    metadata JSONB,                    -- source-specific metadata (project value, client name, date, etc.)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- HNSW index for fast cosine similarity search
CREATE INDEX crm_embeddings_cosine_idx
    ON crm_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- BM25 index via pg_trgm for trigram-based text search
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX crm_embeddings_content_trgm_idx
    ON crm_embeddings USING gin (content gin_trgm_ops);

-- Full-text search index for BM25-style ranking
ALTER TABLE crm_embeddings ADD COLUMN content_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
CREATE INDEX crm_embeddings_tsv_idx ON crm_embeddings USING gin (content_tsv);
```

### Embedding Pipeline

```
On project create/update:
  → Extract text (title + description + scope + materials + completion notes)
  → Generate embedding via Ollama nomic-embed-text (local) or API fallback
  → Upsert into crm_embeddings with source_type='project'

On email ingestion:
  → Extract text (subject + body_plain)
  → Generate embedding
  → Insert into crm_embeddings with source_type='email'

On client update:
  → Extract text (company + notes + sector)
  → Generate embedding
  → Upsert into crm_embeddings with source_type='client'

On quote creation:
  → Extract text (line items + specs + pricing summary)
  → Generate embedding
  → Insert into crm_embeddings with source_type='quote'
```

### Hybrid Search Endpoint

**Endpoint**: `GET /api/cairn/search`
**Auth**: Bearer token via `CAIRN_API_KEY`
**Purpose**: Semantic + lexical search across all CRM data. This is what powers the boardroom scenario.

```json
// Request
{
  "query": "internal illuminated sign boardroom options and prices",
  "limit": 10,
  "source_types": ["project", "quote", "material", "email"],  // optional filter
  "min_score": 0.3
}

// Response
{
  "results": [
    {
      "source_type": "project",
      "source_id": "uuid",
      "content": "Bamburgh Golf Club boardroom sign — 1200x400mm LED-backlit acrylic panel, white LEDs behind opal diffuser, brushed aluminium frame. Wall-mounted with French cleat.",
      "score": 0.89,
      "retrieval_method": "hybrid_rrf",
      "metadata": {
        "project_name": "Bamburgh Golf Club signage",
        "client": "Bamburgh Golf Club",
        "value": 2400.00,
        "stage": "completed",
        "completed_at": "2025-11-15",
        "materials": ["3mm opal acrylic", "LED strip 6500K", "aluminium composite"],
        "photos": ["bamburgh_boardroom_final.jpg"]
      }
    },
    {
      "source_type": "quote",
      "source_id": "uuid",
      "content": "Halo-lit aluminium composite sign, 1500x500mm, warm white LEDs...",
      "score": 0.82,
      "retrieval_method": "hybrid_rrf",
      "metadata": {
        "project_name": "Paton & Co Estate Agents",
        "quoted_value": 2400.00,
        "line_items": [
          {"item": "Aluminium composite panel 1500x500mm", "cost": 180.00},
          {"item": "LED halo channel", "cost": 320.00},
          {"item": "Installation", "cost": 400.00}
        ]
      }
    }
  ],
  "query_embedding_model": "nomic-embed-text",
  "total_results": 5
}
```

### Existing RAG Migration

The CRM already has an "AI Knowledge Search (Powered by RAG)" panel. This should be migrated to the unified `crm_embeddings` table rather than maintaining a separate index. The existing Knowledge Base entries become `source_type='kb'` in the unified index, and the search UI queries the same hybrid endpoint. One index, one search, all sources.

### Embedding Model Strategy

**Primary**: `nomic-embed-text` via Ollama on the sovereign server (192.168.1.228). 768-dimensional embeddings, runs on CPU or the existing 8GB GPU. Free, local, sovereign.

**Fallback**: If the sovereign server is unreachable (e.g., Vercel can't reach the local network), use an API embedding model. Options:
- Anthropic embeddings (if/when available)
- OpenAI `text-embedding-3-small` as a temporary fallback
- Queue the embedding for later processing when the sovereign server is back

**Consistency rule**: All embeddings in `crm_embeddings` must use the same model. If the fallback model is used, flag the row for re-embedding when the primary model is available.

---

## What Needs Building

### 1. Cairn Context API Endpoint

**Endpoint**: `GET /api/cairn/context`
**Auth**: Bearer token via `CAIRN_API_KEY` environment variable

This is the read endpoint Cairn calls to understand CRM state. Returns a structured JSON summary.

```json
{
  "module": "crm",
  "generated_at": "2026-04-03T19:00:00Z",
  "pipeline": {
    "total_value": 50309.00,
    "stages": {
      "lead": { "count": 18, "value": 17450.00 },
      "quoted": { "count": 13, "value": 25716.00 },
      "in_production": { "count": 6, "value": 6218.00 },
      "completed": { "count": 0, "value": 0.00 }
    },
    "conversion_rate": 0.0,
    "avg_deal_value": 1287.40,
    "avg_days_to_close": null
  },
  "follow_ups_due": [
    {
      "project": "Bamburgh Golf Club signage",
      "client": "Bamburgh Golf Club",
      "stage": "quoted",
      "value": 2400.00,
      "days_since_last_contact": 15,
      "next_action": "Follow up on quote"
    }
  ],
  "new_leads_7d": 3,
  "top_opportunity": {
    "project": "Paton & Co Estate Agents",
    "client": "Paton & Co",
    "value": 4500.00,
    "stage": "in_production",
    "next_action": "Schedule installation"
  },
  "email_digest": {
    "unread_enquiries": 2,
    "awaiting_response": 4,
    "oldest_unanswered_days": 3
  },
  "summary": "Pipeline £50,309. 2 follow-ups overdue. 2 unread enquiries in cairn@nbnesigns.com. Conversion rate needs data — no projects completed yet in tracking period."
}
```

### 2. Cairn Write-Back Endpoint

**Endpoint**: `POST /api/cairn/memory`
**Auth**: Bearer token via `CAIRN_API_KEY`

Allows Cairn to push observations, recommendations, or memory entries back into CRM.

```json
{
  "type": "recommendation",
  "priority": "high",
  "message": "Follow up Bamburgh Golf Club — quote sent 15 days ago, no response. Similar projects close within 10 days.",
  "project_id": "uuid-here",
  "source_modules": ["crm", "ledger"],
  "created_at": "2026-04-03T19:00:00Z"
}
```

These should appear in the existing "Live Recommendations" panel and be flagged as Cairn-generated.

### 3. Email Integration — cairn@nbnesigns.com

**Dedicated email**: cairn@nbnesigns.com (IONOS, to be created by Toby)
**Protocol**: IMAP (read) + SMTP (send)
**Purpose**: Cairn monitors this inbox for inbound B2B enquiries, client replies, and supplier correspondence. This is NOT a replacement for personal email — it's a shared business intelligence inbox.

#### 3a. Email Ingestion Service

A background service (Vercel Cron or separate worker) that:

1. Connects to cairn@nbnesigns.com via IMAP (IONOS)
2. Polls every 5 minutes for new messages
3. For each new email:
   - Extracts: sender, subject, body (plain text), date, attachments (metadata only)
   - Runs classification (using existing Llama integration or Claude API):
     - `new_enquiry` — potential new B2B lead
     - `existing_project` — reply related to a tracked project (match by client email, subject line, or project reference)
     - `supplier` — from a known supplier
     - `spam` / `irrelevant` — auto-archive
   - If `new_enquiry`: creates a Lead in CRM, notifies via Live Recommendations
   - If `existing_project`: attaches to project timeline, updates last_contact date
   - If `supplier`: logs in supplier correspondence, flags if procurement-relevant

#### 3b. Email Sending (Cairn-Drafted)

Cairn can draft emails but **never sends without human approval**. The flow:

1. Cairn identifies a follow-up is needed (e.g., quote sent 15 days ago)
2. Cairn drafts an email using project context + client history
3. Draft appears in CRM UI under the project, flagged "Cairn Draft — Review & Send"
4. Jo or Toby reviews, edits if needed, clicks Send
5. Email sent via SMTP from cairn@nbnesigns.com (or forwarded to personal email for sending)
6. Sent email logged against project timeline

#### 3c. Email Environment Variables

```env
CAIRN_EMAIL_ADDRESS=cairn@nbnesigns.com
CAIRN_EMAIL_IMAP_HOST=imap.ionos.co.uk
CAIRN_EMAIL_IMAP_PORT=993
CAIRN_EMAIL_SMTP_HOST=smtp.ionos.co.uk
CAIRN_EMAIL_SMTP_PORT=587
CAIRN_EMAIL_PASSWORD=<secure>
```

#### 3d. Email-to-CRM Matching Rules

Priority order for matching inbound email to existing projects:

1. **Project reference in subject** — e.g., "RE: NBNE-2026-042 Bamburgh Golf Club"
2. **Client email domain match** — sender@bamburghgolfclub.co.uk matches client record
3. **Sender email exact match** — sender is a known contact
4. **Subject keyword match** — fuzzy match against active project names
5. **Unmatched** — create as new lead, flag for human triage

### 4. Enhanced Pipeline Analytics

The current Revenue Overview is a good start. Add:

- **Conversion rate by source**: How did the lead arrive? (Google Ads, email, phone, referral, social media, repeat customer)
- **Average days in each stage**: Lead → Quoted → In Production → Completed
- **Revenue attribution**: When a project completes, tag which source generated it
- **Stale pipeline alerts**: Projects stuck in a stage beyond the average time get flagged

This data feeds directly into the Cairn context endpoint and allows the business brain to answer: "Is Google Ads at £3/day actually producing traceable B2B revenue?"

### 5. Google Ads Integration (Phase 2)

**Not in first build.** But the CRM data model should be ready for it.

Add a `lead_source` field to projects with enum values:
- `google_ads`
- `email_inbound`
- `phone`
- `referral`
- `social_media`
- `repeat_customer`
- `website_form`
- `other`

And optional fields:
- `utm_source`, `utm_medium`, `utm_campaign` (for web-originated leads)
- `google_ads_click_id` (gclid, for future Google Ads API integration)

This allows manual attribution now and automated attribution later.

### 6. Client Relationship Intelligence

Extend the Clients section to track:

- **Lifetime value**: Sum of all completed project values
- **Repeat rate**: How many projects has this client commissioned?
- **Last contact date**: Auto-updated from email integration
- **Preferred contact method**: Email / Phone / In person
- **Notes / context**: Free text that Cairn can index (e.g., "Prefers to deal with Jo", "Budget-conscious but loyal", "Seasonal — orders before Easter and Christmas")
- **Linked Ledger data**: If the client has Xero invoices, link to Ledger revenue data

### 7. Project Reference System

Implement a human-readable project reference: `NBNE-YYYY-NNN`

Example: `NBNE-2026-042` for the 42nd project created in 2026.

This reference:
- Appears in all email subjects (for matching)
- Appears on quotes and invoices
- Is searchable in CRM and by Cairn
- Is auto-generated on project creation

---

## Data Model Additions

### emails table
```sql
CREATE TABLE emails (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id TEXT UNIQUE NOT NULL,        -- IMAP Message-ID header
    project_id UUID REFERENCES projects(id),
    from_address TEXT NOT NULL,
    to_address TEXT NOT NULL,
    subject TEXT,
    body_plain TEXT,
    body_html TEXT,
    classification TEXT,                     -- new_enquiry, existing_project, supplier, spam
    is_inbound BOOLEAN DEFAULT true,
    is_read BOOLEAN DEFAULT false,
    is_cairn_draft BOOLEAN DEFAULT false,    -- true if Cairn drafted this
    is_approved BOOLEAN DEFAULT false,       -- true if human approved for sending
    approved_by TEXT,                        -- staff who approved
    received_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### cairn_recommendations table
```sql
CREATE TABLE cairn_recommendations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type TEXT NOT NULL,                      -- recommendation, observation, alert
    priority TEXT NOT NULL,                  -- high, medium, low
    message TEXT NOT NULL,
    project_id UUID REFERENCES projects(id),
    source_modules TEXT[],                   -- e.g., {'crm', 'ledger'}
    is_actioned BOOLEAN DEFAULT false,
    actioned_by TEXT,
    actioned_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Additions to projects table
```sql
ALTER TABLE projects ADD COLUMN IF NOT EXISTS
    reference TEXT UNIQUE,                   -- NBNE-2026-042
    lead_source TEXT,                        -- google_ads, email_inbound, etc.
    utm_source TEXT,
    utm_medium TEXT,
    utm_campaign TEXT,
    google_ads_click_id TEXT,
    last_contact_at TIMESTAMPTZ,
    days_in_current_stage INTEGER GENERATED ALWAYS AS (
        EXTRACT(DAY FROM NOW() - updated_at)
    ) STORED;
```

### Additions to clients table
```sql
ALTER TABLE clients ADD COLUMN IF NOT EXISTS
    lifetime_value DECIMAL(10,2) DEFAULT 0,
    project_count INTEGER DEFAULT 0,
    preferred_contact TEXT,                  -- email, phone, in_person
    notes TEXT,                              -- free text, Cairn-indexable
    last_contact_at TIMESTAMPTZ;
```

---

## Environment Variables (New)

```env
# Cairn API
CAIRN_API_KEY=<shared secret across all modules>

# Email (IONOS)
CAIRN_EMAIL_ADDRESS=cairn@nbnesigns.com
CAIRN_EMAIL_IMAP_HOST=imap.ionos.co.uk
CAIRN_EMAIL_IMAP_PORT=993
CAIRN_EMAIL_SMTP_HOST=smtp.ionos.co.uk
CAIRN_EMAIL_SMTP_PORT=587
CAIRN_EMAIL_PASSWORD=<secure>

# Existing
NEON_DATABASE_URL=<existing>
LLAMA_API_KEY=<existing, for AI insights>
```

---

## Build Order

### Phase 1 — Cairn API + Semantic Memory (Priority: Immediate)
1. Enable pgvector and pg_trgm extensions on Neon
2. Create `crm_embeddings` table with HNSW and GIN indexes
3. Build embedding pipeline (nomic-embed-text via Ollama, API fallback)
4. Index all existing projects, clients, materials, and KB entries
5. Implement `GET /api/cairn/search` — hybrid BM25 + cosine similarity with RRF
6. Migrate existing RAG Knowledge Search to unified `crm_embeddings` index
7. Implement `GET /api/cairn/context` endpoint
8. Implement `POST /api/cairn/memory` endpoint
9. Surface Cairn recommendations in Live Recommendations panel
10. Add project reference system (NBNE-YYYY-NNN)
11. Add `lead_source` field to projects

### Phase 2 — Email Integration (Priority: High)
1. Set up cairn@nbnesigns.com on IONOS (Toby — manual step)
2. Build IMAP polling service (Vercel Cron, 5-min intervals)
3. Implement email classification (Llama or Claude API)
4. Build email-to-project matching logic
5. Embed all ingested emails into `crm_embeddings` (source_type='email')
6. Build email timeline view on project pages
7. Build Cairn draft review/approve/send UI
8. Auto-update `last_contact_at` on projects and clients from email activity

### Phase 3 — Pipeline Intelligence (Priority: Medium)
1. Implement lead source tracking and attribution
2. Build conversion funnel analytics (source → stage → completion)
3. Add stale pipeline alerts
4. Extend client records with lifetime value and repeat rate
5. Connect to Ledger context for margin-aware pipeline prioritisation

### Phase 4 — Google Ads Integration (Priority: Future)
1. UTM parameter capture from web forms
2. Google Ads API connection for spend/click data
3. Automated lead source attribution from gclid
4. ROAS calculation per campaign feeding into Cairn context

---

## Cross-Module Queries Cairn Should Be Able To Answer

Once CRM, Ledger, and Manufacture are all feeding Cairn:

1. **"What are our options for an internal illuminated sign in a boardroom?"** — CRM semantic search finds past projects, quotes, materials, and prices. Cairn synthesises into client-facing options with guideline pricing. **This is the primary use case.**
2. **"What's our most profitable type of B2B project?"** — CRM knows project types, Ledger knows margins
3. **"Should we quote this job?"** — CRM knows pipeline load, Manufacture knows capacity, Ledger knows cash position
4. **"Which leads came from Google Ads and were they profitable?"** — CRM knows source, Ledger knows final margin
5. **"Who hasn't ordered in 6 months but used to be regular?"** — CRM client history + Ledger transaction history
6. **"Draft a follow-up for all quotes older than 14 days"** — CRM pipeline + email integration
7. **"What should Jo focus on today?"** — CRM follow-ups + Manufacture production needs + email triage
8. **"What materials did we use on the Glendale Show signs and what did they cost?"** — CRM semantic search + Ledger cost data
9. **"Have we done any work for golf clubs before?"** — CRM semantic search across projects, emails, client records

---

## Constraints

- **Human-in-the-loop for all outbound communication.** Cairn drafts, humans approve.
- **No direct database access between modules.** CRM calls Ledger via API, never queries Ledger's database.
- **Email data is sensitive.** Encrypt at rest in Neon. Retention policy: active project emails indefinite, orphaned emails deleted after 12 months.
- **GDPR compliance.** Client data export and deletion must be functional. Privacy policy must cover email processing.
- **Existing Vercel/Neon stack.** Do not migrate to a different platform. Build within existing constraints.

---

## Port Allocation (NBNE Module Registry)

| Module       | Backend Port | Frontend Port | Domain                     |
|-------------|-------------|---------------|----------------------------|
| Phloe       | 8000        | 3000          | phloe.co.uk               |
| Ledger      | 8001        | 3001          | ledger.nbnesigns.co.uk    |
| Manufacture | 8002        | 3002          | manufacture.nbnesigns.co.uk|
| CRM         | Vercel      | Vercel        | crm.nbnesigns.co.uk       |
| Render      | 8004        | 3004          | render.nbnesigns.co.uk    |
| Cairn API   | 8765        | —             | localhost (sovereign)      |

Note: CRM runs on Vercel (serverless), not on the sovereign server. The Cairn API endpoint is exposed via the Vercel deployment. If CRM migrates to the sovereign server in future, assign port 8003/3003.

---

## Success Criteria

The CRM upgrade is complete when Cairn can:

1. ✅ Semantic search across all CRM data via `/api/cairn/search` using hybrid BM25 + cosine similarity with RRF
2. ✅ Answer "what are our options for X?" with past projects, materials, methods, and guideline prices — in a client-facing conversation on Toby's phone
3. ✅ Query CRM state via `/api/cairn/context` and receive structured pipeline data
4. ✅ Push recommendations that appear in the CRM UI
5. ✅ Read inbound emails from cairn@nbnesigns.com and classify them
6. ✅ Match emails to existing projects or create new leads
7. ✅ Embed all project data, emails, quotes, and client records into a unified pgvector index
8. ✅ Draft follow-up emails that Jo/Toby can review and send
9. ✅ Answer "which B2B leads came from Google Ads?" (manual attribution initially)
10. ✅ Calculate client lifetime value and identify repeat customers
11. ✅ Cross-reference with Ledger to prioritise pipeline by margin, not just value
