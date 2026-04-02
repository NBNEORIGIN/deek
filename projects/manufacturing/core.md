# Manufacturing / Origin Designed — Cairn Agent Core Context
# Version: 1.2

## What this is
The manufacturing management system for NBNE's Origin Designed product
range. Currently a complex Excel/Google Sheets workbook being replaced
by a proper Django application. Manages product definitions, production
runs, FBA shipments, and stock levels across multiple channels.

## Non-negotiable rules

1. Never modify an M-number once assigned — they are permanent references.
   M-numbers are the single source of truth for product identity.

2. Stock levels are sacrosanct — never auto-update without explicit
   user confirmation.

3. FBA (Fulfilled By Amazon) shipments have strict Amazon labelling
   requirements. Never mark a shipment as complete without label verification.

4. Always distinguish between DIP1 (Amazon fulfilment warehouse) and
   local/3PL stock. They are tracked separately.

5. Channel prices are never stored as literals in code.
   All pricing lives in the database.

## Domain vocabulary — MEMORISE THESE

M-number: master product reference (M0001, M0002, etc.)
  The canonical identifier for a product design.
  One M-number can have multiple SKUs (UK, US, CA, AU, eBay, Etsy, etc.)

Blank: the physical substrate/template a product is printed on.
  Named after infamous people for internal memorability:
  DONALD  = circular push/pull sign shape
  SAVILLE = rectangular aluminium composite (A4-ish landscape)
  DICK    = landscape acrylic plaque
  STALIN  = large format aluminium panel
  MYRA    = specific proprietary shape
  IDI     = push/pull door sign variant
  TOM     = memorial garden stake
  JOSEPH  = standing display/counter format
  HARRY   = specific rectangular format
  AILEEN  = specific format
  SADDAM  = specific format
  GARY    = specific format
  RICHARD = specific format
  LOUIS   = specific format
  DRACULA = 9.5cm x 9.5cm format
  TED     = specific format
  PRINCE ANDREW = specific format
  BARZAN  = specific format (sublimation placement)
  BABY JESUS = specific format (sublimation placement)

Machine names (also named, internal references):
  ROLF    = UV flatbed printer (Mimaki UV)
  MIMAKI  = sublimation/dye-sub printer
  MUTOH   = wide format inkjet
  ROLAND  = vinyl cutter / print-and-cut
  EPSON   = sublimation printer (SC-F500)
  HULKY   = specific large-format printer

Production pipeline stages (in order):
  Designed → Printed → Processed → Cut → Labelled → Packed → Shipped

Sales channels: UK, US, CA, AU, EBAY, ETSY, FR

FBA = Fulfilled By Amazon (stock held in Amazon warehouse DIP1)

## Target architecture
Django backend with models for:
  Product (M-number, description, blank, material, stock)
  SKU (channel-specific identifiers linking to Product)
  ProductionOrder (what to make, progress through pipeline)
  Shipment (FBA batches, tracking, labelling status)
  Procurement (materials, reorder points, supplier)

## Spreadsheet audit findings (2026-03-31)

MASTER STOCK: ~3,900 rows (header at row 2, rows 0-1 are summaries).
  Many rows likely inactive/discontinued. Filter on import.

ORDERS: has existing pipeline stage columns (Designed→Packed booleans).
  Only ~93 of 2,306 rows have active data. Template for stage tracker.

RECORDS: 2,458 production logs since Dec 2023. Columns: DATE, WEEK,
  SKU, M NUMBER, NUMBER PRINTED, ERRORS, PROCESS, FAILURE REASON.
  Exists but sparse — Ben said no tracking system, but this is partial.

SUB PLACEMENTS / DIBOND PLACEMENT: machine assignment data.
  DIBOND PLACEMENT has ROLF/MIMAKI boolean flags per product.
  SUB PLACEMENTS groups by blank with quantity needs.

Worksheet: unstructured multi-region scratchpad (41 cols, no headers).
  Ivan's make-list logic lives in cell formulas, not parseable as data.

ScratchPad2: clean 2-column lookup (M-number → Optimal Stock 30 Days).
  361 entries.

D2C: 28 active personalised orders. Formula-driven, handle with care.

NEW PRODUCTS: 998-row pipeline with DATE, BLANK, DESIGNED, PUBLISHED.

## Current state (updated 2026-04-02)

Repo: https://github.com/NBNEORIGIN/manufacture
Local: D:\manufacture
Production: https://manufacture.nbnesigns.co.uk (Hetzner 178.104.1.152)
Stack: Django 5.x + DRF, Next.js 14, PostgreSQL
Local DB: manufacture, pw postgres123
Venv: D:\manufacture\.venv

### Deployment
Docker stack at /opt/nbne/manufacture/ on Hetzner.
Ports: backend 8015, frontend 3015.
Nginx: /etc/nginx/sites-enabled/manufacture.conf
SSL: /etc/ssl/cloudflare/nbne/ (Cloudflare origin cert)
DNS: A record manufacture → 178.104.1.152 in Cloudflare
CI/CD: GitHub Actions deploy.yml on push to main
Superuser: toby / toby@nbnesigns.com

### ALL PHASES COMPLETE

Phase 0 — Scaffolding: 8 Django apps, 6 seed import commands.
Phase 1 — Make list + production tracking (220 items, 7-stage pipeline).
Phase 2 — FBA shipments (140 historical, 42,854 units).
Phase 3 — CSV import (4 parsers: FBA Inventory, Sales & Traffic, Restock, Zenstores).
Phase 4 — D2C dispatch queue (Zenstores CSV → dispatch orders).
Phase 5 — Procurement materials (10 items, reorder tracking).
Phase 6 — CANCELLED (SP-API too complex, manual CSV preferred).
Phase 7 — Error tracking (2,453 records, 4.01% error rate).
Deployment — Docker on Hetzner, staff auth via Phloe sync, bug reporting via IONOS SMTP.

### Key technical decisions
- Direct API calls from frontend (no Next.js proxy — was unreliable)
- NEXT_PUBLIC_API_BASE_URL env var for production, fallback to localhost:8000
- DATABASE_URL (dj-database-url) for Docker, individual DB_* vars for local dev
- Django session auth with email login, staff synced from Phloe /api/staff-module/
- Bug reports emailed via IONOS SMTP (smtp.ionos.co.uk:587)
- Composite blanks resolved by first word (DICK,TOM → ROLF)
- Spreadsheet floats need int(float(v)) not int(v)
- ASSEMBLY/SKU ASSIGNMENT have duplicate column headers — first-occurrence only
- Channel values messy — mapped via CHANNEL_MAP dict
- pgdata volume must be recreated if DB_PASSWORD changes
- Shipping is Royal Mail (not Evri)

The Excel workbook (Shipment_Stock_Sheet.xlsx) remains the authoritative
reference for domain understanding.
Key Excel sheets: ORDERS, MASTER STOCK, ASSEMBLY, DIBOND PLACEMENT,
                  SUB PLACEMENTS, RECORDS, PROCUREMENT, ScratchPad2,
                  SKU ASSIGNMENT, D2C, NEW PRODUCTS
