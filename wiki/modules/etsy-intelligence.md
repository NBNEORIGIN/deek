# Etsy Intelligence

## What It Does
Etsy listing health and sales intelligence for NBNE. Mirrors the Amazon Intelligence
pattern but uses the Etsy API v3 instead of CSV uploads. Ingests shop and listing
data, health-scores listings with 13 Etsy-specific checks, and exposes a context
endpoint for the business brain dashboard.

**Important:** This is the read-only analytics module. Listing creation/publishing
is handled by [[modules/render]] via its own Etsy OAuth + API integration.

## Who Uses It
- **Toby Fletcher** — Etsy listing review, sales tracking

## Tech Stack
- Backend: Python (embedded in Cairn FastAPI at core/etsy_intel/)
- Database: 4 etsy_* tables in Cairn's PostgreSQL on nbne1
- API routes: /etsy/* (mounted in api/routes/etsy_intel.py)
- Etsy API: v3, API key auth (read-only), 5 QPS rate limiting via asyncio semaphore

## Connections
- **Feeds data to:** [[modules/cairn]] (context endpoint), [[modules/manufacturing]] (sales velocity cross-module read via `/etsy/sales`)
- **Receives listings from:** [[modules/render]] (Render creates listings, Etsy Intel tracks health)
- **Context endpoint:** `GET /etsy/cairn/context` — listing health, sales data
- **Sales read endpoint:** `GET /etsy/sales?days=N&shop_id=X` — pre-aggregated 30-day sales by listing, consumed by manufacture's Sales Velocity module. Gated by `X-API-Key` header (all other `/etsy/*` routes are currently unauthenticated).

## Current Status
- Build phase: Phase 1 complete (API-driven ingestion)
- Last significant change: 2026-04-11 — added `/etsy/sales` cross-module read endpoint for manufacture Sales Velocity feature
- Known issues: Receipt/sales endpoint requires OAuth token — currently degrades gracefully with API key only

## Architecture: Two Etsy Systems
| System | Location | Auth | Purpose |
|--------|----------|------|---------|
| Etsy Intelligence (this) | core/etsy_intel/ | API key only | Read listings, score health, track sales |
| Render Etsy Publisher | D:\render\etsy_api.py | OAuth 2.0 PKCE | Create/update listings, upload images |

These must stay separate. Etsy Intel reads and scores. Render writes and publishes.

## Key Concepts
- **Shop:** NorthByNorthEastSign (ID: 11706740)
- **Health scoring (0-10):** 13 Etsy-specific checks mirroring AMI pattern
- **API-driven:** Unlike AMI's CSV uploads, Etsy Intelligence pulls data directly from the Etsy API
- **Graceful degradation:** Sales data requires OAuth; listings work with API key only

## Related
- [[modules/amazon-intelligence]] — sister module for Amazon marketplace
- [[modules/render]] — creates Etsy listings via direct API publish
- [[modules/cairn]] — context endpoint feeds business brain
