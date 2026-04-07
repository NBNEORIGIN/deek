# Manufacture — FBA Restock Module

## What It Does
Generates per-marketplace FBA replenishment plans by downloading Amazon's
inventory planning report (GET_FBA_INVENTORY_PLANNING_DATA) via SP-API and
computing optimised send quantities using the Newsvendor algorithm.

**Phase 1 (complete):** SP-API sync + Newsvendor calculation + production order creation

## Who Uses It
Ben (production lead) reviews and approves restock plans. Approved plans
create production orders directly in the Manufacture pipeline.

## Tech Stack
- Backend: Django 5.x at `manufacture/backend/restock/`
- Algorithm: Newsvendor model (`newsvendor.py`) — no scipy, uses rational approximation
- SP-API: Delegates to Cairn AMI at `/ami/spapi/report/*` — no Amazon API credentials in Manufacture
- SKU resolution: Local Manufacture `SKU` table first, Cairn `/ami/sku-mapping/lookup` fallback
- UI: Next.js at `frontend/src/app/restock/page.tsx`

## Key Concepts
- **Newsvendor critical ratio**: Cu/(Cu+Co) — underage cost / (underage + overage cost)
- **Cu**: price × margin (lost sale value)
- **Co**: price × FBA storage rate × horizon (holding cost = days × £0.02/unit/day)
- **Safety stock**: Added for `out_of_stock` and `reorder_now` items — z×σ×√(lead_time)
- **Confidence score**: Degrades with low velocity (<5 units/30d), missing margin, missing days-of-supply
- **Zero-velocity rule**: Items with <0.5 mean demand/horizon always get 0 recommendation

## Workflow
```
User clicks "Sync GB" →
  POST /api/restock/GB/sync/ →
    Manufacture spapi_client.py calls Cairn POST /ami/spapi/report/request →
      Cairn requests GET_FBA_INVENTORY_PLANNING_DATA from Amazon SP-API
    Background thread polls Cairn /ami/spapi/report/{id}/status every 30s (5-15 min)
    When DONE: downloads CSV bytes →
      parser.py normalises rows →
      assembler.py resolves SKUs to M-numbers + runs Newsvendor →
      RestockItem records stored
  Frontend polls /api/restock/GB/status/ every 10s
  When complete: table loads with Amazon rec + Newsvendor rec side-by-side
User edits "Send qty" per item, selects rows →
  POST /api/restock/approve/ stores approved_qty
  POST /api/restock/create-production/ creates ProductionOrder + stages
```

## Connections
- **Reads from**: Cairn AMI `/ami/spapi/report/*` (SP-API report download)
- **Reads from**: Manufacture `SKU` + `Product` tables (SKU→M-number, margin when available)
- **Falls back to**: Cairn `/ami/sku-mapping/lookup` (for SKUs not in local Manufacture DB)
- **Writes to**: Manufacture `ProductionOrder` + `ProductionStage` tables
- **Exposes**: `/api/restock/*` for UI

## API Endpoints
```
GET  /api/restock/marketplaces/         list marketplaces + last sync info
GET  /api/restock/history/              all sync runs
POST /api/restock/{mp}/sync/            trigger SP-API download (background)
GET  /api/restock/{mp}/status/          job status (pending/running/complete/error)
GET  /api/restock/{mp}/                 latest plan + items (filterable)
POST /api/restock/approve/              store approved quantities
POST /api/restock/create-production/    create production orders
POST /api/restock/upload/               manual CSV upload (no SP-API)
```

## Supported Marketplaces
GB (EU region), DE (EU), FR (EU), US (NA), CA (NA), AU (FE)

## Report Schema
CSV columns: Country, Product Name, FNSKU, Merchant SKU, ASIN, Condition,
Price, Sales last 30 days, Units Sold Last 30 Days, Total Units, Inbound,
Available, Days of Supply at Amazon Fulfillment Network,
Total Days of Supply (including units from open shipments),
Alert, Recommended replenishment qty, Recommended ship date, Unit storage size

Alert values: `out_of_stock`, `reorder_now`, blank

## Decision Log

### 2026-04-07 — SP-API delegation via Cairn HTTP
Direct SP-API calls from Manufacture would duplicate credentials and infrastructure.
All Amazon API calls go via Cairn AMI HTTP endpoints. Manufacture has no `AMAZON_*` vars.

### 2026-04-07 — Local SKU table first, Cairn fallback
Manufacture's own `SKU` model already has the SKU→M-number mapping (seeded from spreadsheet).
Local lookup is faster and avoids HTTP dependency. Cairn `/ami/sku-mapping/lookup` is used
only for SKUs not found locally.

### 2026-04-07 — Newsvendor without scipy
scipy is a heavy dependency for one function. Implemented `_norm_ppf()` as a rational
approximation (Abramowitz and Stegun formula). Accurate to ~1e-4 for 0.001 < p < 0.999.

### 2026-04-07 — Both recommendations shown side-by-side
Amazon's recommendation is a good baseline but doesn't account for NBNE's margin
structure or lead times. Both are shown; user approves before any production order is created.
