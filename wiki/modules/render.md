# Render

## What It Does
AI-driven product design and publishing system for NBNE's Origin Designed range.
Takes a product concept through to live listings on Amazon, Etsy, eBay, and the
NBNE website (app.nbnesigns.co.uk/shop). Staff refer to it internally as "new
products." This is the most critical piece of software NBNE has developed.

## Who Uses It
- **Gabby** — daily product creation, QA approval, Amazon flatfile generation
- **Toby Fletcher** — product design, listing strategy, publishing decisions

## Tech Stack
- Backend: Flask 3.0 / Python, Gunicorn (1 worker)
- Database: render_* tables in Cairn PostgreSQL (`deploy-cairn-db-1`, database `cairn`)
- Connection string (from container): `postgresql://cairn:cairn_nbne_2026@cairn-db:5432/cairn`
- Image generation: Playwright (headless Chromium), Pillow
- AI content: Claude Sonnet (listings), DALL-E 3 (lifestyle images), GPT-4o (chat)
- Hosting: Hetzner 178.104.1.152, port 8025 (migrating to nbne1)
- GitHub: NBNEORIGIN/render
- Local path: D:\render

## Database Tables (render_ prefix, all in cairn DB on deploy-cairn-db-1)
| Table | Purpose |
|-------|---------|
| render_products | M-number catalogue, QA status, AI content |
| render_blanks | Physical sign substrate dimensions (5 sizes) |
| render_product_content | AI-generated titles, descriptions, bullets, search terms |
| render_product_images | Generated image URLs per product |
| render_users | Staff authentication (5 users) |
| render_sales_imports | Amazon sales report audit trail |
| render_sales_data | Aggregated sales metrics |
| render_batches | Background job tracking |
| render_publish_log | Cross-channel publish history (etsy, amazon, ebay, phloe) |
| render_catalogue_listing | Parent listing: brand, title base, bullets, browse nodes, variation theme |
| render_catalogue_variant | Child SKU: EAN, dimensions, price, Amazon/Etsy/eBay status + ASIN |
| render_ean_pool | Spare EAN numbers; atomic assignment via SELECT FOR UPDATE SKIP LOCKED |
| render_spapi_log | Full request/response log for every SP-API call |

## Connections
- **Publishes to:** Etsy (direct API, draft listings), eBay (Inventory API + Marketing), Amazon (SP-API Listings Items API — live), Phloe shop (auto on QA approve)
- **Feeds data to:** [[modules/manufacture]] (ASIN mapping), [[modules/amazon-intelligence]] (published listings)
- **Receives data from:** [[modules/amazon-intelligence]] (improvement queue for content-weak listings)
- **Context endpoints:**
  - `GET /api/cairn/context` — product pipeline state, publish counts, recent activity (Flask)
  - `GET /render/cairn/context` — catalogue summary: listing/variant counts, Amazon status breakdown, EAN pool remaining, recent publishes (Cairn FastAPI — PR #5)

## Publishing Channels

### Etsy (Direct API)
- OAuth 2.0 PKCE flow at /etsy/oauth/connect
- Creates draft listings (staff review before activating)
- Shop ID: 11706740, Taxonomy: 2844 (Signs)
- Rate limited: 5 QPS
- Route: `POST /api/etsy/publish`

### eBay (Direct API)
- Inventory API + Marketing API (auto-promote at 5% CPS)
- Category: 166675 (Signs & Plaques)

### Amazon (SP-API — Listings Items API v2021-08-01)
- **Live as of 2026-04-08** — replaces XLSX flatfile manual upload
- Seller ID EU: ANO0V0M1RQZY9, Marketplace: A1F83G8C2ARO7P (UK)
- Self-contained LWA token refresh in `amazon_api.py` (no Cairn token dependency)
- Publish sequence: PUT parent SKU → PUT each child (0.2s apart, 5 req/s limit)
- Preflight check blocks on: missing EAN, missing credentials; warns on: live variants, missing images
- Routes:
  - `GET /api/amazon/listings/preflight/<id>` — pre-publish checklist
  - `POST /api/amazon/listings/publish/<id>` — submit parent + all children
  - `POST /api/amazon/listings/poll-asins` — promote pending→live when ASIN available
  - `GET /api/amazon/listings/log` — recent SP-API call log

### NBNE Website (Phloe auto-publish)
- Auto-triggers when product QA status changes to 'approved'
- Pushes to app.nbnesigns.co.uk/shop via Django API
- JWT auth, tenant: mind-department
- Route: `POST /api/phloe/publish`

## Catalogue UI (Catalogue tab)
- Summary stat pills: total listings, variants, live/pending/unpublished Amazon counts
- Per-listing card with expandable variant table
- EAN status per variant: "Assign EAN" button if unassigned (atomic assignment)
- "Publish to Amazon" button with inline preflight + result summary
- "Poll ASINs" button to retrieve ASINs for pending variants
- "Download CSV" — all variants with marketplace status

## EAN Pool
- Seeded via: `docker exec render-app-1 flask seed-eans /tmp/eans.csv`
- Copy CSV: `docker cp eans.csv render-app-1:/tmp/eans.csv`
- **EAN pool is currently empty** — must seed before first Amazon publish
- Assignment is atomic (SELECT FOR UPDATE SKIP LOCKED); EAN_ALREADY_IN_USE errors must be reported to Toby

## Current Status
- Build phase: Production (Hetzner port 8025), migration to nbne1 still pending
- Last significant change: Amazon SP-API publisher + catalogue schema (2026-04-08)
- render-db-1 container is idle (not yet decommissioned — confirm data migration complete first)
- Known issues: eBay OAuth re-auth pending; EAN pool empty

## Key Concepts
- **Product publishing pipeline:** Concept → SVG render → AI content → QA approve → auto-publish to all channels
- **Catalogue vs Products:** render_products = design/QA tool; render_catalogue_* = marketplace publishing DB (EAN, ASIN, status)
- **5 blanks:** dracula (9.5cm), saville (11cm), dick (14cm), barzan (19cm), baby_jesus (29cm)
- **3 finishes:** silver, gold, white
- **QA gate:** Non-negotiable — no product publishes without QA approval
- **Draft-only Etsy:** All Etsy listings created as draft, never directly active

## Related
- [[modules/manufacture]] — M-number and blank data for product definitions
- [[modules/amazon-intelligence]] — listing health drives improvement queue
- [[modules/etsy-intelligence]] — Etsy listings health scoring (read-only analytics)
- [[modules/ledger]] — revenue tracking from all marketplace channels
