# Etsy Intelligence — Core Context

## What this is
Etsy listing health and sales intelligence for NBNE. Mirrors the Amazon Intelligence
pattern: ingest data via API, store in PostgreSQL, health score listings, produce
reports, expose a Cairn context endpoint for the business brain dashboard.

## NBNE Etsy presence
- **Etsy NBNE Print and Sign** — main store (signs, memorial plaques, custom products)
- **Etsy Copper Bracelets Shop** — secondary store
- Sales channels also include eBay Origin Designers (future integration)

## API access
- Etsy API v3: https://api.etsy.com/v3
- App: publisher (Personal Access)
- Rate limit: 5 QPS / 5K QPD
- Credentials stored in Cairn memory (reference_etsy_api.md) — load via env vars

## Architecture
Code lives inside the Cairn repo (same pattern as Amazon Intelligence):
- Core logic: `core/etsy_intel/`
- API routes: `api/routes/etsy_intel.py` (mounted at `/etsy/*`)
- Database: `etsy_intelligence` tables in Cairn's PostgreSQL on nbne1
- Connection: postgresql://cairn:cairn_nbne_2026@192.168.1.228:5432/claw

## Decision Log

### 2026-04-04 — Project registered in Cairn
**Context**: Toby provided Etsy API credentials, wants Etsy integration mirroring AMI
**Decision**: Register as Cairn project, code inside Cairn repo following AMI pattern
**Rejected**: Standalone repo (unnecessary — AMI proved embedded pattern works)

### 2026-04-04 — Phase 1 implementation complete
**Context**: Built full Etsy Intelligence module mirroring AMI pattern
**Decision**: API-driven ingestion (unlike AMI's CSV uploads). Etsy API v3 with x-api-key auth, 5 QPS rate limiting via asyncio semaphore, offset/limit pagination. 4 database tables (etsy_shops, etsy_listings, etsy_sales, etsy_listing_snapshots) in claw PostgreSQL on nbne1. Health scoring 0-10 with 13 Etsy-specific checks. Sync service fetches shops, listings, and receipts (receipts require OAuth — graceful degradation). Routes at /etsy/* with Cairn context endpoint.
**Files**: core/etsy_intel/{__init__,db,api_client,sync,scoring,reports}.py, api/routes/etsy_intel.py, api/main.py, web-business context route
**Rejected**: CSV upload approach (Etsy has a proper API unlike Amazon), separate database (AMI proved shared claw DB with prefixed tables works), synchronous API client (Etsy rate limiting needs async)
**Note**: Receipt/sales endpoint requires OAuth token — currently degrades gracefully with API key only. Shop IDs must be set via ETSY_SHOP_IDS env var since /users/me/shops also requires OAuth.

### 2026-04-11 — Cross-module `/etsy/sales` read endpoint
**Context**: The manufacture app's new Sales Velocity feature (Phase 2B) needs 30-day Etsy sales data to feed its per-M-number velocity calculation. Per the Cairn hard rule "Never access another module's database directly", manufacture must not query the `etsy_sales` table itself — it needs an HTTP endpoint.
**Decision**: Added `GET /etsy/sales?days=N&shop_id=X` returning pre-aggregated rows (one per listing_id with summed quantity + first/last sale date). Joins `etsy_sales` → `etsy_listings` on listing_id to attach the stored SKU. Gated by `Depends(verify_api_key)` so the cross-module caller must send `X-API-Key: $CLAW_API_KEY`. The other `/etsy/*` routes remain unauthenticated — retrofitting auth to all of them was deemed out of scope for this feature.
**Defensive behaviour**: rows where `etsy_listings.sku` is NULL or contains a comma (indicating `sync.py::_parse_receipts` collapsed a multi-SKU variation via `skus[0]`) are excluded and counted in `skipped_null_sku` / `skipped_multi_sku` response fields. Toby confirmed during planning that NBNE's Etsy listings use one-listing-per-variation with separate M-numbers, so the single-SKU-per-listing model is correct and the `skipped_multi_sku` counter should stay at zero in practice; if it starts counting up, it signals a data-quality regression upstream.
**Files**: `api/routes/etsy_intel.py` (+98 lines for the endpoint), `tests/test_etsy_intel_sales.py` (new file, 16 tests covering auth, response shape, query params, empty-set, DB error).
**Rejected**: (1) Schema migration to add `external_sku` to `etsy_sales` with upstream parser change — dropped after Toby confirmed one-listing-per-variation model makes it unnecessary. (2) Cross-module direct DB access (explicitly forbidden). (3) Raw-row response (not pre-aggregated) — rejected because manufacture's aggregator only needs summed-per-listing figures, and aggregating at the SQL layer saves wire weight.
**Outcome**: Committed to `feat/etsy-sales-endpoint` branch. Not yet deployed — awaiting Toby's sign-off before pushing to origin and rolling out on Hetzner nbne1.
**Delegation decision**: Self — cross-project API contract with security-sensitive auth retrofit. The SQL and FastAPI glue alone would have been Qwen/DeepSeek work, but the decision on whether to gate only the new endpoint vs backfill auth to all /etsy/* required principal-developer judgement on scope.
**Blocker if deploy not ordered**: manufacture Phase 2B.3 EtsyAdapter cannot start until this endpoint is live on nbne1.
