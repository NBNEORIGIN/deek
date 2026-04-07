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
- SP-API: Direct LWA calls — `spapi_client.py` calls Amazon directly. Cairn HTTP unreachable from Manufacture container.
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
    spapi_client.py: LWA token exchange → Amazon SP-API
    POST /reports/2021-06-30/reports (reportType=GET_FBA_INVENTORY_PLANNING_DATA)
    Background thread polls GET /reports/2021-06-30/reports/{id} every 30s (5-15 min)
    When processingStatus=DONE: fetches document URL → downloads TSV bytes →
      parser.py: tab-split, maps UK→GB, derives restock alerts from days_of_supply →
      assembler.py: resolves SKUs→M-numbers, skips D2C exclusions, runs Newsvendor →
      RestockItem records bulk-created
  Frontend polls /api/restock/GB/status/ every 10s
  When complete: table loads with Amazon rec + Newsvendor rec side-by-side
User edits "Send qty" per item, selects rows →
  POST /api/restock/approve/ stores approved_qty
  POST /api/restock/create-production/ creates ProductionOrder + stages
```

## Connections
- **Calls**: Amazon SP-API directly using LWA credentials
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
GET/POST/DELETE /api/restock/exclusions/   D2C exclusion list
```

## Supported Marketplaces
GB (EU region), DE (EU), FR (EU), US (NA), CA (NA), AU (FE)

## Report Schema (actual — TSV format)
Tab-separated. Headers are lowercase-hyphenated. Key columns:

| TSV column | Internal key | Notes |
|---|---|---|
| `sku` | `merchant_sku` | |
| `marketplace` | `marketplace` | `UK` in report → mapped to `GB` |
| `your-price` | `price` | |
| `units-shipped-t30` | `units_sold_30d` | blank for zero-velocity items |
| `available` | `units_available` | |
| `inbound-quantity` | `units_inbound` | |
| `days-of-supply` | `days_of_supply_amazon` | |
| `alert` | `amazon_alert_raw` | velocity alert: `Low traffic`, `Low conversion`, blank |
| `Recommended ship-in quantity` | `amazon_recommended_qty` | |

Restock alert (`out_of_stock`, `reorder_now`, blank) is **derived** by parser from `available == 0` or `days_of_supply < 30 + recommended_qty > 0`. Amazon's `alert` column is NOT the restock signal.

## Approximate row counts (2026-04-07)
GB: 514, CA: 210, FR: 44 — US/AU/DE volume TBC

## Decision Log

### 2026-04-07 — Direct SP-API (Cairn HTTP not reachable)
Cairn container is on deploy_default network, Manufacture backend on same network, but HTTP responses timeout cross-network despite TCP connectivity. Root cause unknown (possibly iptables). Rewrote spapi_client.py to call Amazon SP-API directly using LWA credentials stored in Manufacture .env.

### 2026-04-07 — Actual CSV format is TSV not CSV
GET_FBA_INVENTORY_PLANNING_DATA returns tab-separated data with lowercase-hyphenated headers. Marketplace column contains 'UK' not 'GB'. Alert column contains velocity alerts (Low traffic, Low conversion), not restock alerts (out_of_stock, reorder_now). Restock alert derived from days_of_supply < 30 + recommended_qty > 0.

### 2026-04-07 — D2C exclusion list
Personalised items (made-to-order) should never be FBA restocked. RestockExclusion model lets staff permanently exclude M-numbers. Pre-seeded: M0634, M0683, M0682.

### 2026-04-07 — Local SKU table first, Cairn fallback
Manufacture's own `SKU` model already has the SKU→M-number mapping (seeded from spreadsheet).
Local lookup is faster and avoids HTTP dependency. Cairn `/ami/sku-mapping/lookup` is used
only for SKUs not found locally.

### 2026-04-07 — Newsvendor without scipy
scipy is a heavy dependency for one function. Implemented `_norm_ppf()` as a rational
approximation (Abramowitz and Stegun formula). Accurate to ~1e-4 for 0.001 < p < 0.999.

### 2026-04-07 — Heredoc corrupts LWA tokens (| character)
When the NA refresh token was injected into `.env` via a bash heredoc, a single character was silently corrupted (`ETd` → `ETt`), causing 400 errors on all NA-region calls. The `|` in `Atzr|Iw...` tokens makes heredoc and `sed` unreliable. **Always use Python `re.sub` to copy secrets between env files on the server.** See `wiki/infrastructure/hetzner.md` for the safe pattern.

### 2026-04-07 — Both recommendations shown side-by-side
Amazon's recommendation is a good baseline but doesn't account for NBNE's margin
structure or lead times. Both are shown; user approves before any production order is created.
