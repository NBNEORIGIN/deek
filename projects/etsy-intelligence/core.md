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
