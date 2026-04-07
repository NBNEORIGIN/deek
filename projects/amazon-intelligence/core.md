# Amazon Listing Intelligence — Core Context

## What this is
Amazon listing health pipeline for NBNE. Ingests data from Seller Central (manual upload or SP-API automated), cross-references with Manufacture margin data, and produces prioritised snapshots with health scores, diagnosis codes, and recommended actions. Live data flows automatically into Cairn chat.

**Phase 1 (complete):** Manual uploads + health scoring + Cairn context endpoint
**Phase 2 (complete):** SP-API automated sync 4x daily + auto snapshot rebuild

## Data sources
1. **All Listings Report** (TSV) — SKU→ASIN bridge. Primary enrichment source. Also contains `open-date` (listing creation in `DD/MM/YYYY HH:MM:SS GMT/BST` format).
2. **Inventory Flatfiles** (.xlsm) — listing content: titles, bullets, images, pricing. Column positions vary between category templates — parser MUST use Row 4 header name matching, not column indices. Sheet: "Template", data from row 7+, skip row with SKU=ABC123.
3. **Business Reports** (.csv) — sessions, conversion, Buy Box %, revenue. Amazon uses en-dash (U+2013) not regular dash in some header names — `_normalise_header()` handles this.
4. **Advertising Reports** (.xlsx/.csv) — ad spend, ACOS, ROAS. ASIN regex must not use `\b` boundary (underscore is `\w`).
5. **SP-API automated** — replaces manual downloads for inventory + analytics. Advertising needs separate auth token (SP-API ≠ Ads API).

## Critical data model: SKU → M-number mapping
One M-number maps to MULTIPLE marketplace SKUs and ASINs. Canonical mapping seeded from `Shipment_Stock_Sheet_-_ASSEMBLY.csv`.

SKU prefix patterns: OD* (1,034), M-number direct (1,043), OP* (432), OM* (194), OC* (36), OPS*, OPL*, RS*, JS-*, FR-*, VN-*

Marketplace ASINs: UK (328), US (310), CA (248), AU (231), DE (19)

## Health scoring (0-10)
Deductions for: low conversion (<8%), low sessions (<50), lost Buy Box (<90%), high ACOS (>25%), low margin (<20%), missing bullets (<5), few images (<6), no description, short title.

## Diagnosis codes
CONTENT_WEAK, KEYWORD_POOR, VISIBILITY_LOW, MARGIN_CRITICAL, QUICK_WIN_IMAGES, QUICK_WIN_BULLETS, BUYBOX_LOST, ZERO_SESSIONS, NO_PERFORMANCE_DATA

## Calibration from real data
- 8,112 snapshots, 5,718 SKU mappings, 44+ uploads
- Only 1% of listings have all 5 bullet points filled
- 92% have a main image, average 4.2 images per listing
- Parent/child: ~11% parents, ~72% children, ~17% standalone
- Score child ASINs, not parents (parents are containers)

## SP-API credentials
- **App**: PrivateApp_API (`amzn1.sp.solution.ae5ca772-6325-47c5-9a1c-6b5b54cdacf7`)
- **Client ID**: `amzn1.application-oa2-client.be933583cbc1430cb46386de8df677cf`
- **Seller IDs**: EU=ANO0V0M1RQZY9, NA=AU398HK55HDI4, AU=A35C7AI7WDWERB
- **Account ID** (Origin Designed UK): A2LT2AMSGU4V54
- Refresh tokens in Hetzner `.env` as `AMAZON_REFRESH_TOKEN_EU/NA/AU`
- Client secret: `AMAZON_CLIENT_SECRET` in `.env`
- `AMAZON_CLIENT_ID` must be explicitly in `.env` — docker-compose `environment:` block overrides Python `os.getenv()` default with empty string if var is set but blank

## Advertising API (separate from SP-API)
Amazon Advertising API uses the same LWA access token but requires separate authorization. SP-API refresh tokens do NOT include advertising scope. To get advertising profile IDs:
1. Authorize app at advertising.amazon.co.uk → API → Developer access
2. Generate dedicated advertising refresh token
3. Call `GET /ami/spapi/advertising/profiles?region=EU` to discover profile IDs
4. Set `AMAZON_ADS_PROFILE_ID_EU` in `.env`
Until then, advertising data continues via manual upload — working fine.

## Architecture

Code lives inside the Cairn repo at `D:\claw`, not standalone.

| Component | Path |
|---|---|
| Core logic | `core/amazon_intel/` |
| SP-API modules | `core/amazon_intel/spapi/` |
| API routes | `api/routes/amazon_intel.py` (mounted at `/ami/*`) |
| Database | 8 `ami_*` tables in Cairn's PostgreSQL |
| Config | `projects/amazon-intelligence/config.json` |

### Data pipeline
```
Stock Sheet CSV ──────────────────────────────────────────────→ ami_sku_mapping
All Listings TSV (manual or SP-API GET_MERCHANT_LISTINGS_ALL) → ami_sku_mapping + ami_flatfile_data
Flatfile .xlsm (manual) ────────────────────────────────────→ ami_flatfile_data ─┐
Business Report (manual or SP-API GET_SALES_AND_TRAFFIC_REPORT) → ami_business_report ─┤→ ami_listing_snapshots
Advertising (manual or Ads API Sponsored Products report) ───→ ami_advertising_data ───┘        ↓
                                                                               /ami/cairn/context
                                                                                        ↓
                                                                             Cairn chat (every message)
```

### Automated sync chain (SP-API)
```
cron (0 0,6,12,18 * * * /etc/cron.d/cairn-spapi)
  └─ POST /ami/spapi/sync
       └─ scheduler.run_full_sync()
            ├─ inventory: GET_MERCHANT_LISTINGS_ALL_DATA → parse_and_store_all_listings()
            ├─ analytics: GET_SALES_AND_TRAFFIC_REPORT (30-day rolling JSON) → ami_business_report_data
            ├─ advertising: Ads API SP search term report (if profile ID configured)
            └─ snapshots: build_snapshots() auto-runs if any data synced
```

### API endpoints (all at /ami/*)
**Data ingestion:** `/upload/flatfile`, `/upload/all-listings`, `/upload/business-report`, `/upload/advertising`, `/uploads`
**SP-API sync:** `/spapi/sync`, `/spapi/sync/inventory`, `/spapi/sync/analytics`, `/spapi/sync/advertising`, `/spapi/status`, `/spapi/advertising/profiles`
**Listings write:** `/spapi/listings/{sku}`, `/spapi/listings/{sku}/price`, `/spapi/listings/{sku}/bullets`, `/spapi/listings/{sku}/title`
**Analysis:** `/snapshots/build`, `/snapshots`, `/snapshots/{asin}`, `/underperformers`, `/report/generate`, `/report/latest`
**Cairn:** `/cairn/context`, `/index-to-memory`
**Utility:** `/health`, `/sku-mapping/sync`, `/sku-mapping/stats`, `/migrate`, `/new-products/ingest`

### Database tables
- `ami_sku_mapping` — SKU→M-number→ASIN canonical map (5,718 rows)
- `ami_uploads` — upload log for all ingestion events
- `ami_flatfile_data` — parsed flatfile content rows
- `ami_business_report_data` — sessions, conversion, revenue per ASIN
- `ami_advertising_data` — ad spend, ACOS, ROAS per ASIN/SKU
- `ami_listing_snapshots` — assembled health snapshots (8,112 rows)
- `ami_new_products` — Dec 2025–Mar 2026 new product reference list
- `ami_weekly_reports` — generated weekly summary reports
- `ami_spapi_sync_log` — SP-API sync run history (status: running/complete/error)

### SP-API report lifecycle
```
POST /reports/2021-06-30/reports → reportId
  poll GET /reports/2021-06-30/reports/{id} every 30s until DONE (max 30min)
GET /reports/2021-06-30/documents/{docId} → presigned S3 URL
GET {url} → download + optional gunzip → bytes → existing parser
```

## Known gotchas
- `GET_SALES_AND_TRAFFIC_REPORT` returns JSON not TSV — separate parser in `spapi/analytics.py`
- `os.getenv('KEY', 'default')` returns `''` when the docker-compose `environment:` block sets the var as `${KEY:-}` — use `os.getenv('KEY') or 'default'` instead
- Advertising API host is `advertising.amazon.com` for all regions (`advertising.amazon.co.uk` 301 redirects)
- Business report snapshots batch insert: use `execute_values` not row-by-row (4,142 rows took >90s individually, 3.4s with batch)
- Duplicate ASINs in same snapshot batch cause unique constraint violations — deduplicate before insert
- `_run_logged(sync_type, region, fn)` passes `region` to `fn` automatically — do not also pass it in `**kwargs`

## Decision Log

### 2026-04-03 — All Listings Report as primary SKU→ASIN bridge
Business report join was 144/4,142 (3.5%) without it. Added parser, join went to 627 matches (4.4x). 3,638 flatfile rows gained ASINs.

### 2026-04-03 — Code embedded in Cairn repo
AMI is tightly coupled to Cairn's FastAPI app. Extracting to standalone service adds infrastructure with no benefit. `include_paths` in config.json scopes Cairn indexer to AMI files only.

### 2026-04-06 — SP-API embedded in core/amazon_intel/spapi/, not standalone service
Shares DB connection, same process, same `.env`. Standalone service only justified if rate limiting becomes a problem (it hasn't).

### 2026-04-06 — Advertising API deferred
SP-API refresh tokens don't include advertising scope. Manual advertising uploads continue to work. SP-API advertising will follow once separate Ads API authorization is completed.

### 2026-04-07 — Auto snapshot build after sync
Snapshots now rebuild automatically after any SP-API sync. Full chain is zero-touch: cron → sync → snapshots → Cairn context → chat.
