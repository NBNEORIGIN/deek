# DEEK_MODULES.md
# NBNE Business Module API Specifications
# Deek Context Endpoint Definitions
# North By North East Print & Sign Ltd
# Last updated: 16 April 2026

---

## Architecture Principle

Every NBNE business module owns its own data and exposes it via API.
No module has direct database access to another module.
Deek is the memory layer that sits above all modules.

The business brain queries each module's context endpoint to assemble a
picture of business state, indexes responses into memory, and reasons over
the combined picture.

```
                        DEEK
                   (memory + brain)
                         |
         ┌───────────────┼───────────────┐
         │               │               │
     Manufacture       Ledger        Marketing
       API              API             API
         │               │               │
      Render           CRM            Phloe
       API              API             API
```

---

## Module Evals

Every module ships with an `evals/` directory containing at minimum one
`contract.json` file. The contract evals exist to make the module's API
boundary automatically enforceable rather than only documented — this
is the mechanism that turns the Architecture Principle above into an
enforced invariant rather than a written one.

### Contract eval structure

```json
{
  "module": "beacon",
  "version": "0.1",
  "tests": [
    {
      "id": "contract-001",
      "prompt": "Return current Google Ads spend for tenant X",
      "assertions": [
        "response is valid JSON",
        "response contains only fields declared in module API schema",
        "no direct database query appears in module trace",
        "no cross-module import appears in call stack",
        "response time < 2000ms"
      ]
    }
  ]
}
```

### Assertion categories

Contract evals cover the structural rules this protocol already mandates:

- **Isolation**: no direct DB access outside the module's own schema.
- **Boundary**: no imports from sibling modules.
- **Schema**: responses conform to the declared API schema.
- **Determinism**: identical input produces identical output (for pure handlers).
- **Locale**: responses respect tenant locale config (see locale-awareness work).

### Human-reviewed before loop

Assertions are authored or reviewed by a human before WIGGUM is permitted
to loop against them. Auto-generated assertions are flagged
`reviewed: false` in the eval file and WIGGUM refuses to run improvement
loops against unreviewed sets. This prevents optimising for a bad rubric
overnight. WIGGUM loop contract is defined in NBNE_PROTOCOL.md.

### Tiered evals

- `contract.json` — structural. Run on every commit. Fast. Binary.
- `behaviour.json` — domain-correct outputs. Run nightly via WIGGUM.
- `quality.json` — qualitative. Not automated. Reviewed manually.

WIGGUM self-improvement loops operate only on tiers 1 and 2. Tier 3
remains human-judged.

---

## The Business Value Chain

**Make → Measure → Sell**

1. Manufacture — what do we make and how many?
2. Ledger — are we making money doing it?
3. Marketing — are we reaching the right people?

The brain's most valuable output is cross-chain reasoning:
"SAVILLE silver 300mm is selling well on Amazon UK (Ledger), margin is 34%,
we have blanks for 150 units (Manufacture), current ad ROAS is 4x (Marketing)
— make these first this week."

---

## Priority 1 — Manufacture Context Endpoint

**Module**: Manufacture (Django 5.x / Next.js / PostgreSQL)
**Purpose**: What do we make today and how many?
**Priority**: Highest — no revenue without production

### Endpoint

```
GET /api/deek/context
Authorization: Bearer <DEEK_API_KEY>
```

Legacy aliases `/api/cairn/context` and `CAIRN_API_KEY` are accepted
during the rename transition window.

### Response Schema

```json
{
  "module": "manufacture",
  "generated_at": "2026-03-29T22:00:00Z",
  "make_list": [
    {
      "m_number": "M2280",
      "blank_name": "SAVILLE",
      "description": "No Entry Without Permission",
      "machine": "MIMAKI",
      "priority_score": 0.94,
      "units_recommended": 48,
      "stock_current": 12,
      "stock_target": 60,
      "sales_velocity_7d": 8.4,
      "batch_size": 12,
      "reason": "below target, high velocity"
    }
  ],
  "machine_status": [
    { "id": "rolf",              "name": "Rolf",              "status": "available|running|maintenance|offline", "current_job": null, "queue_depth": 0 },
    { "id": "mao",               "name": "Mao",               "status": "available", "current_job": null, "queue_depth": 0 },
    { "id": "mimaki",            "name": "Mimaki",            "status": "running",   "current_job": "M2280 batch x48", "queue_depth": 2 },
    { "id": "mutoh",             "name": "Mutoh",             "status": "available", "current_job": null, "queue_depth": 0 },
    { "id": "roland",            "name": "Roland",            "status": "available", "current_job": null, "queue_depth": 0 },
    { "id": "epson",             "name": "Epson",             "status": "available", "current_job": null, "queue_depth": 0 },
    { "id": "beast",             "name": "Beast",             "status": "available", "current_job": null, "queue_depth": 0 },
    { "id": "fiber_laser",       "name": "Fiber Laser",       "status": "available", "current_job": null, "queue_depth": 0 },
    { "id": "hulk",              "name": "Hulk",              "status": "available", "current_job": null, "queue_depth": 0 },
    { "id": "avid",              "name": "Avid",              "status": "available", "current_job": null, "queue_depth": 0 },
    { "id": "jeffrey",           "name": "Jeffrey",           "status": "available", "current_job": null, "queue_depth": 0 },
    { "id": "peter",             "name": "Peter",             "status": "available", "current_job": null, "queue_depth": 0 },
    { "id": "application_table", "name": "Application Table", "status": "available", "current_job": null, "queue_depth": 0 },
    { "id": "lsealer",           "name": "LSealer",           "status": "available", "current_job": null, "queue_depth": 0 },
    { "id": "heat_tunnel",       "name": "Heat Tunnel",       "status": "available", "current_job": null, "queue_depth": 0 },
    { "id": "welder",            "name": "welder",            "status": "available", "current_job": null, "queue_depth": 0 },
    { "id": "brake",             "name": "brake",             "status": "in_build",  "current_job": null, "queue_depth": 0 },
    { "id": "oven",              "name": "oven",              "status": "planned",   "current_job": null, "queue_depth": 0 }
  ],
  "stock_alerts": [
    {
      "blank_name": "DONALD",
      "current_stock": 4,
      "reorder_point": 20,
      "alert": "reorder_required"
    }
  ],
  "summary": "14 products below target. Mimaki busy. Rolf, Mao, Epson available. 2 blank reorders required."
}
```

Schema notes:

- ``id`` is the stable machine identifier — snake_case, never changes. Consumers
  key on ``id``. Adding a status (``in_build`` / ``planned``) is additive; existing
  consumers unaware of those just treat the machine as not-yet-routable.
- ``name`` is the display nickname — case-sensitive, matches the canonical
  spelling in ``projects/manufacturing/core.md`` and the ``machine`` filter on
  Deek's ``search_manuals`` tool. Renames are a coordinated change (every
  consumer + ingested manual chunk uses this string).
- The list above is the full live + planned set as of 2026-04-30. Earlier
  versions of this schema only listed three machines (ROLF, MIMAKI, EPSON);
  consumers should now expect ~18 entries with statuses spanning the full
  ``available|running|maintenance|offline|in_build|planned`` range.

### Domain Vocabulary (use verbatim in all code and docs)

Blank names: DONALD, SAVILLE, DICK, STALIN, BARZAN, BABY_JESUS
Machine nicknames (RATIFIED 2026-04-30, canonical case): Rolf, Mao, Mimaki,
  Mutoh, Roland, Epson, Beast, Fiber Laser, Hulk, Avid, Jeffrey, Peter,
  Application Table, LSealer, Heat Tunnel, welder, brake, oven.
  Full identity cards in ``projects/manufacturing/machines/<id>.md`` in
  the deek repo. Earlier abbreviated list (ROLF, MIMAKI, EPSON) was stale.
Product identifier: M-number (e.g. M2280)
QA states: pending, approved, rejected

---

## Priority 2 — Ledger Context Endpoint

**Module**: Ledger (Django / Next.js / PostgreSQL, port 8001/3001)
**Purpose**: Are we making money?
**Priority**: Second — measures the value of what Manufacture produces

### Endpoint

```
GET /api/deek/context
Authorization: Bearer <DEEK_API_KEY>
```

### Response Schema

```json
{
  "module": "ledger",
  "generated_at": "2026-03-29T22:00:00Z",
  "cash_position": {
    "current_balance": 14280.50,
    "currency": "GBP",
    "trend_7d": "improving",
    "days_runway": 94
  },
  "revenue": {
    "mtd": 8420.00,
    "ytd": 31840.00,
    "by_channel": {
      "amazon_uk": 4210.00,
      "amazon_us": 1840.00,
      "amazon_de": 620.00,
      "amazon_fr": 380.00,
      "amazon_ca": 290.00,
      "amazon_au": 180.00,
      "etsy": 680.00,
      "ebay": 220.00
    }
  },
  "margins": {
    "overall_gross": 0.38,
    "by_channel": {
      "amazon_uk": 0.34,
      "etsy": 0.48,
      "ebay": 0.29
    },
    "top_performers": [
      {
        "m_number": "M2280",
        "margin": 0.41,
        "revenue_mtd": 840.00
      }
    ]
  },
  "procurement": {
    "alerts": [
      {
        "supplier": "blank_supplier",
        "item": "SAVILLE blanks",
        "urgency": "high",
        "suggested_order_qty": 500,
        "estimated_cost": 380.00
      }
    ],
    "outstanding_invoices": 2,
    "outstanding_value": 1240.00
  },
  "postage": {
    "cost_mtd": 620.00,
    "cost_per_order_avg": 2.84
  },
  "summary": "Cash healthy at £14,280. Etsy margin strongest at 48%. 2 procurement alerts. Amazon UK driving 50% of MTD revenue."
}
```

---

## Priority 3 — Marketing Context Endpoint

**Module**: Marketing (CRM + Phloe Google Ads module)
**Purpose**: Are we reaching the right people?
**Priority**: Third — connects production and measurement to sales

### Endpoint

```
GET /api/deek/context
Authorization: Bearer <DEEK_API_KEY>
```

### Response Schema

```json
{
  "module": "marketing",
  "generated_at": "2026-03-29T22:00:00Z",
  "advertising": {
    "platforms": [
      {
        "name": "google_ads",
        "spend_mtd": 420.00,
        "roas": 4.2,
        "impressions_7d": 48200,
        "clicks_7d": 840,
        "ctr": 0.017,
        "top_campaign": "Phloe booking awareness - Northumberland"
      },
      {
        "name": "amazon_ppc",
        "spend_mtd": 280.00,
        "roas": 3.8,
        "top_asin": "B0XXXXXXXX"
      }
    ],
    "total_spend_mtd": 700.00,
    "blended_roas": 4.0
  },
  "crm": {
    "active_pipeline": 8,
    "pipeline_value": 24800.00,
    "new_leads_7d": 3,
    "follow_ups_due": 2,
    "top_opportunity": {
      "client": "Miter Industrial",
      "value": 8400.00,
      "stage": "proposal_sent",
      "next_action": "follow up on serialised warehouse tag pilot"
    }
  },
  "phloe": {
    "active_tenants": 4,
    "bookings_7d": 84,
    "revenue_7d": 1240.00,
    "churn_risk": []
  },
  "summary": "Blended ROAS 4.0x. CRM pipeline £24,800 with 2 follow-ups due. Phloe 84 bookings this week. Miter Industrial pilot is highest value opportunity."
}
```

---

## Cross-Module Brain Query

Once all three endpoints are live, Deek assembles them into a single business
state snapshot for the brain:

```
GET /api/deek/context  (on each module)
→ assemble into business_state
→ index into pgvector memory
→ brain reasons over business_state
→ produces recommendations
```

Example brain output:
```json
{
  "date": "2026-03-29",
  "recommendations": [
    {
      "priority": 1,
      "action": "Make 48x SAVILLE silver M2280 on ROLF today",
      "reasoning": "Below stock target, 8.4/day velocity, 41% margin, ROLF available",
      "modules": ["manufacture", "ledger"]
    },
    {
      "priority": 2,
      "action": "Follow up Miter Industrial proposal",
      "reasoning": "£8,400 pipeline, proposal sent, no response logged in CRM",
      "modules": ["marketing"]
    },
    {
      "priority": 3,
      "action": "Reorder DONALD blanks",
      "reasoning": "4 units remaining, reorder point 20, procurement alert active",
      "modules": ["manufacture", "ledger"]
    }
  ],
  "summary": "Production day: prioritise SAVILLE on ROLF. One CRM follow-up critical. Two procurement orders needed."
}
```

---

## Implementation Notes

**Authentication**: All context endpoints use a shared `DEEK_API_KEY` environment
variable (legacy alias `CAIRN_API_KEY` also accepted during transition). Set in
each module's `.env`. Deek passes this as a Bearer token.

**Cadence**: Deek polls context endpoints:
- Manufacture: every 30 minutes during working hours
- Ledger: every 60 minutes
- Marketing: every 4 hours

**Caching**: Each module caches its context response for the poll interval.
Do not recompute on every request — pre-compute and cache.

**Graceful degradation**: If a module endpoint is unreachable, Deek uses the
last cached response and flags it as stale. The brain reasons over stale data
with a warning rather than failing.

**Port allocation** (no conflicts with existing services):
- Phloe: 8000/3000
- Deek API: 8765
- Ledger: 8001/3001
- Manufacture: 8002/3002
- Render: 8003/3003
- CRM: 8004/3004

---

## Build Order

These context endpoints should be added to each module as it reaches
production-readiness. The schema above is the target — implementations
can return a subset initially, expanding over time.

- [ ] Manufacture context endpoint — Phase 0 scaffolding, then after Ben interview
- [ ] Ledger context endpoint — after four core modules are built
- [ ] Marketing context endpoint — after CRM improvement + Phloe ads module

Hardware dependency: the business brain that reasons over all three requires
the dual RTX 3090 setup (48GB VRAM) for a 72b-class local model. Build the
endpoints now. Run the brain when the hardware is ready.

---

## Cost Tracking Module

**Module**: Deek internal (not a separate app -- built into Deek API)
**Purpose**: Track API and local model costs per prompt, per session, per project
**Priority**: Implement alongside the MCP server (low complexity, high value)

### Context Endpoint

```
GET /api/deek/context  (internal -- Deek queries itself)
```

### Response Schema

```json
{
  "module": "cost_tracking",
  "generated_at": "2026-03-30T08:00:00Z",
  "today": {
    "total_cost_gbp": 1.24,
    "by_model": {
      "qwen_local": 0.00,
      "deepseek": 0.18,
      "claude_sonnet": 0.84,
      "claude_opus": 0.22,
      "openai_fallback": 0.00
    },
    "by_project": {
      "claw": 0.44,
      "phloe": 0.62,
      "render": 0.18
    },
    "prompts_run": 34,
    "local_ratio": 0.41
  },
  "this_month": {
    "total_cost_gbp": 18.40,
    "projected_monthly_gbp": 22.10,
    "vs_last_month": -0.12
  },
  "hardware_roi": {
    "api_cost_saved_by_qwen_gbp": 4.20,
    "note": "Estimated saving vs routing all qwen tasks to DeepSeek"
  },
  "summary": "£1.24 today. 41% of prompts handled locally at £0. Monthly projection £22. Qwen saving ~£4/month vs full API routing."
}
```

### Hardware ROI Tracking

The cost log enables direct hardware ROI calculation:

```
Monthly API cost without local models   = X
Monthly API cost with local models      = Y
Monthly saving                          = X - Y
RTX 3090 cost                          = £800 (approx)
Payback period                         = £800 / (X - Y) months
```

This calculation updates automatically as the cost log grows.
It feeds the business brain's hardware investment recommendations.

See the **Hardware Configuration** section below for the current and
target hardware profiles that feed this calculation.

---

## Hardware Configuration

Deek reads `DEEK_HARDWARE_PROFILE` from environment to determine routing
behaviour. The routing matrix itself lives in CLAUDE.md under Task Breadth
Classifier. This section describes the hardware states it resolves against.

### Profile: `dev_desktop` (current)

**Installed**: RTX 3050 8GB. Second PCIe slot available.

**Local models** (via Ollama):
- `gemma4:e4b` — general reasoning, conversational, PA-style queries.
  Spills ~68% to CPU/RAM at current allocation. Still quick on short
  responses because CPU inference is the bottleneck rather than GPU.
- `qwen2.5-coder:7b` — code generation. Fits fully in VRAM. The only
  local model that runs 100% on GPU on this profile.
- `deepseek-coder-v2:16b` — harder code reasoning. Heavy CPU spill;
  reserve for tasks where quality matters more than latency.
- `nomic-embed-text` — embeddings for pgvector hybrid retrieval.

**Constraint**: models cannot be loaded simultaneously at full
performance — Ollama swaps them. If VRAM pressure becomes acute,
dedicate the card to one model and route remaining work to API.

### Profile: `dual_3090` (target — parts on order)

**Planned**: 2× RTX 3090, 48GB VRAM total. Consumer Nvidia cards do
not support NVLink — the cards run as separate devices and cannot
tensor-split a single model. Plan workloads per card, not across them.

**Planned allocation**:
- Card 1: Qwen 2.5 72B (or Coder 32B at Q4) for principal local reasoning.
- Card 2: Gemma 4 resident + embeddings models + headroom for
  ComfyUI/FLUX/Wan2.1 workloads feeding Render and Studio.

On arrival, pull:
```
ollama pull qwen2.5-coder:32b
ollama pull deepseek-coder-v2:16b
ollama pull mxbai-embed-large
```

Then set `DEEK_HARDWARE_PROFILE=dual_3090`. Expected monthly API cost
drop is material (see Cost Tracking Module above); let the cost log
confirm actual saving against projection.

### Why profile matters

The same task routes differently depending on which profile is active:

- On `dev_desktop`, escalation to Claude happens sooner — Toby's
  wall-clock time is more expensive than Claude tokens when local
  compute is slow.
- On `dual_3090`, local compute is fast enough that decomposed
  multi-domain work stays local, and Claude is reserved for tight
  coupling or long coherence only.

Both routing tables are in CLAUDE.md. This section is just the
hardware description those tables resolve against.
