# Amazon Intelligence

## What It Does
Amazon listing health pipeline for NBNE. Ingests data from Seller Central via SP-API
(automated, 4x daily) or manual upload, cross-references with Manufacture margin data,
and produces health-scored listing snapshots with diagnosis codes. Live data flows
automatically into Cairn chat via the context endpoint — no manual steps required.

**Phase 1 (complete):** Manual uploads + health scoring + Cairn context endpoint
**Phase 2 (complete):** SP-API automated sync + auto snapshot rebuild after every sync

## Who Uses It
- **Toby Fletcher** — listing health review, improvement prioritisation, Amazon performance questions via Cairn chat
- **Cairn Business Brain** — polls `/ami/cairn/context` on every chat message for live Amazon data

## Tech Stack
- Backend: Python (embedded in Cairn FastAPI at `core/amazon_intel/`)
- SP-API: `core/amazon_intel/spapi/` — client, inventory, analytics, advertising, listings write, scheduler
- Database: 8 `ami_*` tables in Cairn's PostgreSQL on Hetzner
- API routes: `/ami/*` (mounted in `api/routes/amazon_intel.py`)
- Cron: `/etc/cron.d/cairn-spapi` on Hetzner — midnight, 6am, noon, 6pm UTC

## Connections
- **Feeds data to:** [[modules/cairn]] (context endpoint every chat message), [[modules/render]] (improvement queue)
- **Receives data from:** [[modules/manufacture]] (M-number + margin data)
- **Context endpoint:** `GET /ami/cairn/context`

## Current Status
- Fully automated — SP-API syncs inventory + analytics 4x daily, snapshots rebuild automatically
- 8,112 snapshots, 5,718 SKU mappings, 44+ uploads processed
- Advertising sync pending separate Amazon Ads API authorization (manual upload working in meantime)
- Listings write API live — can update prices, titles, bullets per SKU via SP-API

## Automated Sync Chain
```
cron (4x daily) → POST /ami/spapi/sync
  ├─ inventory: GET_MERCHANT_LISTINGS_ALL_DATA → ami_sku_mapping + ami_flatfile_data
  ├─ analytics: GET_SALES_AND_TRAFFIC_REPORT (30-day rolling) → ami_business_report_data
  ├─ advertising: Ads API SP search term report (when profile ID configured)
  └─ snapshots: auto-rebuild → ami_listing_snapshots → /ami/cairn/context → Cairn chat
```

## Key Concepts
- **SKU → M-number mapping:** One M-number maps to multiple marketplace SKUs and ASINs
- **Health scoring (0-10):** Deductions for low conversion, low sessions, lost Buy Box, high ACOS, missing content
- **Diagnosis codes:** CONTENT_WEAK, KEYWORD_POOR, VISIBILITY_LOW, MARGIN_CRITICAL, QUICK_WIN_IMAGES, QUICK_WIN_BULLETS, BUYBOX_LOST, ZERO_SESSIONS, NO_PERFORMANCE_DATA
- **All Listings Report:** Primary SKU→ASIN bridge (boosted join rate from 3.5% to 15%)
- **SP-API vs Ads API:** Two separate Amazon authorization systems — SP-API tokens don't include advertising scope

## SP-API Credentials (Hetzner .env)
- App: PrivateApp_API
- Seller IDs: EU=ANO0V0M1RQZY9, NA=AU398HK55HDI4, AU=A35C7AI7WDWERB
- `AMAZON_CLIENT_ID`, `AMAZON_CLIENT_SECRET`, `AMAZON_REFRESH_TOKEN_EU/NA/AU` in deploy/.env
- Advertising profile IDs: discover via `GET /ami/spapi/advertising/profiles?region=EU` once Ads API authorized

## Related
- [[modules/manufacture]] — M-number and margin data source
- [[modules/render]] — receives improvement queue for content-weak listings
- [[modules/etsy-intelligence]] — sister module for Etsy marketplace
