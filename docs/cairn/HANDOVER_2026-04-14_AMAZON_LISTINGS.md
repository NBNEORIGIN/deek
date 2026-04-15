# Handover — Amazon Listing Intelligence Pipeline

**Date:** 2026-04-14
**Sessions covered:** Amazon Listings Session 1 + Session 2, CRM brief draft
**Status:** Pipeline live in production on Hetzner. Remaining items are operational.

---

## What shipped

Full rebuild of the Amazon Listing Intelligence module, from auth through
to real-time notifications and clean snapshots. Commits on `master`, pushed,
deployed, and running.

### Session 1 — Phases 0-2 (infrastructure)

| Deliverable | Detail |
|---|---|
| All 3 SP-API regions active | `ACTIVE_REGIONS = ['EU', 'NA', 'FE']` in `core/amazon_intel/spapi/scheduler.py` |
| Ads API v2→v3 fix | `get_advertising_profiles()` migrated from deprecated `/v2/profiles` to `/v3/profiles` |
| `ami_listing_content` table | Full Catalog Items API content per ASIN+marketplace, with content hash change detection |
| `ami_listing_embeddings` table | 768-dim pgvector embeddings, 4 field types per ASIN (title/bullets/description/combined) |
| `ami_listing_content_history` table | Field-level change tracking |
| `ami_notification_events` table | SQS notification audit log |
| `core/amazon_intel/spapi/catalog.py` | Catalog Items API v2022-04-01 fetching, parsing, upserting |
| `core/amazon_intel/spapi/embeddings.py` | Embedding pipeline reusing `core/wiki/embeddings.py` provider chain |
| `core/amazon_intel/spapi/notifications.py` | SQS long-poll processor + SP-API subscription management |
| AWS infrastructure | IAM user, IAM role, 3 SQS queues (eu-west-2, us-east-1, ap-southeast-2) |
| SP-API notification subscriptions | 18 live: 6 types × 3 regions (ANY_OFFER_CHANGED, REPORT_PROCESSING_FINISHED, FEED_PROCESSING_FINISHED, FULFILLMENT_ORDER_STATUS, FBA_OUTBOUND_SHIPMENT_STATUS, B2B_ANY_OFFER_CHANGED) |
| Grantless auth | `spapi_get_grantless` / `spapi_post_grantless` / `spapi_delete_grantless` in `client.py` for Notifications API |
| Docs | `docs/cairn/amazon_notifications_setup.md`, `docs/cairn/amazon_consumers.md` |
| Tests | 25 unit tests in `tests/test_catalog_enrichment.py` |

### Session 2 — Phases 3-5 (data migration)

| Deliverable | Detail |
|---|---|
| `build_snapshots()` rewritten | Reads from `ami_listing_content` first, falls back to `ami_flatfile_data` for un-enriched ASINs. Performance from `ami_daily_traffic` (already from Sprint 1) |
| `query_amazon_intel` tool updated | Schema docs refreshed, `_ALLOWED_TABLES` expanded to 16 tables including all new ones |
| Full backfill executed | **5,244 ASINs enriched**: UK 4,112 + US 1,053 + AU 79 |
| UK embeddings complete | **16,036 embeddings** generated across 4,112 UK ASINs, zero errors |
| Broken snapshots nuked | TRUNCATED 28,392 inflated rows from `ami_listing_snapshots` |
| Clean snapshots rebuilt | **4,258 snapshots** from authoritative Catalog API data, 1,036 with performance data |

### Bug fixes during Session 2

1. `get_asins_for_enrichment()` — invalid `SELECT DISTINCT ... ORDER BY` SQL; switched to `GROUP BY + MIN()`
2. `_find_for_marketplace()` — hardened against non-dict items, added fallback to first dict entry
3. `_extract_variations()` — Catalog API returns `parentAsins` as plain strings, not dicts; handle both formats
4. `deploy/Dockerfile.api` — added `boto3>=1.35.0` to the image pip install list

---

## Live production state

**Cairn API:** `deploy-cairn-api-1` on Hetzner `178.104.1.152:8765`, commit `8f3b14d` or later
**Database:** 16 `ami_*` tables in Cairn PostgreSQL

**Current row counts (2026-04-14):**

| Table | Rows |
|---|---|
| `ami_listing_content` | 5,244 |
| `ami_listing_embeddings` | 16,036 |
| `ami_listing_snapshots` | 4,258 (clean rebuild) |
| `ami_notification_events` | 3 (test events, verified) |
| `ami_listing_content_history` | 0 (populates on changes) |

**Verified endpoints:**

- `GET /ami/catalog/content/{asin}?marketplace=UK` — single ASIN content
- `GET /ami/catalog/content?marketplace=UK` — list enriched
- `GET /ami/catalog/search?q=...&marketplace=UK` — semantic search
- `GET /ami/snapshots?max_score=5` — underperformers
- `GET /ami/notifications/events` — notification event log
- `GET /ami/health` — module health

SQS round-trip tested on all 3 regions, test events received and stored.

---

## AWS infrastructure reference

**Account:** NBNE `915077852106`, region `us-east-1` primary

| Resource | Identifier |
|---|---|
| IAM user | `cairn-spapi-notifications` |
| IAM role | `cairn-spapi-notification-publisher` (trusts `437568002678`) |
| SQS EU | `https://sqs.eu-west-2.amazonaws.com/915077852106/cairn-spapi-notifications-eu` |
| SQS NA | `https://sqs.us-east-1.amazonaws.com/915077852106/cairn-spapi-notifications-na` |
| SQS FE | `https://sqs.ap-southeast-2.amazonaws.com/915077852106/cairn-spapi-notifications-fe` |
| SP-API destination | `3c64c732-c6f9-40d4-96f8-89451ffdea53` (shared across regions) |

Env vars in `/opt/nbne/cairn/deploy/.env` on Hetzner.

---

## Outstanding items (not blocking)

### Priority 1 — Security

- [ ] **Rotate AWS access key** `AKIA5KDXD5PFAX36GX6T` — the secret was pasted
  in chat. IAM → Users → cairn-spapi-notifications → Security credentials →
  Create new key → update Hetzner `.env` → delete old key.

### Priority 2 — Operational

- [ ] **NA/FE embeddings** — UK is complete (16,036 vectors), US/AU ASINs
  (1,132 combined) have listing content but no embeddings yet. The next
  scheduled sync cycle will generate them, or run manually:
  `POST /ami/catalog/embed?marketplace=US` and `marketplace=AU`.

- [ ] **sku_mapping cleanup** — ~350 ASINs in `ami_sku_mapping` 404 from the
  Catalog API (deleted/draft products). Consider cleaning these rows so the
  enrichment stops retrying them each cycle. Query: ASINs in
  `ami_sku_mapping` with no matching row in `ami_listing_content` after a
  full backfill run.

- [ ] **Ads profile discovery** — `GET /ami/spapi/advertising/profiles?region=EU`
  returns profile IDs. Add to `.env` as `AMAZON_ADS_PROFILE_ID_{EU/NA/AU}` to
  enable automated ad reporting via `sync_advertising()`.

- [ ] **Monitor first notification arrivals** — the 18 SP-API subscriptions are
  live but we have not yet seen a real production notification (only test events).
  Check `ami_notification_events` after Amazon activity to confirm real events flow.

---

## Architecture notes for future sessions

### Scheduler flow (4× daily via cron)

```
cron → POST /ami/spapi/sync → for each ACTIVE_REGION:
    sync_inventory (Reports API → ami_sku_mapping, ami_flatfile_data)
    sync_analytics (legacy, no writes)
    sync_daily_traffic (Reports API → ami_daily_traffic)
    sync_orders (Reports API → ami_orders)
    sync_advertising (if profile_id configured → ami_advertising_data)
    catalog_enrichment (Catalog API → ami_listing_content, 100 ASINs/cycle, 24hr skip)
    embed_all_listings (→ ami_listing_embeddings, only changed content)
    build_snapshots (→ ami_listing_snapshots)
    compute_velocity (→ ami_velocity)
```

### Content source priority in snapshots

`build_snapshots()` in `core/amazon_intel/snapshots.py`:

1. `ami_listing_content` (Catalog API — authoritative)
2. `ami_flatfile_data` fallback for ASINs not yet enriched
3. `_source` field tracks which source was used per snapshot

### Revenue source of truth

Never SUM from `ami_listing_snapshots` or `ami_business_report_legacy`. Use
`ami_orders` for revenue and `ami_daily_traffic` for sessions/traffic.

### Grantless vs seller-authorized auth

- **Grantless** (`sellingpartnerapi::notifications` scope): Notifications API
  destinations endpoints. Uses `client_credentials` grant.
- **Seller-authorized** (refresh token): everything else, including
  Notifications API subscriptions (which require seller consent).

Both patterns are in `core/amazon_intel/spapi/client.py`.

---

## Related deliverable: CRM Project State brief

During this session we also produced `docs/cairn/CAIRN_CRM_PROJECT_STATE_CC_PROMPT.md`
— the Brief 2 implementation prompt for extending the CRM with:

- Waiting state machine + duration tracking
- Expected value × close probability weighted prioritisation
- Typed `NextAction` enum (explicitly excludes any external-communication types)
- `ami_listing_content`-style `project_open_loops` for tracking offered-but-unclosed items
- Unified `project_timeline_events` log replacing email trawling
- Prioritised-actions dashboard panel

Ready to copy into a fresh Claude Code session. No work started on it yet.

---

## Key file inventory

```
core/amazon_intel/spapi/
  catalog.py              # NEW — Catalog Items API enrichment
  embeddings.py           # NEW — pgvector embeddings + semantic search
  notifications.py        # NEW — SQS + SP-API notification subscriptions
  scheduler.py            # MODIFIED — all 3 regions, enrichment wired in
  advertising.py          # MODIFIED — v2→v3 profile discovery
  client.py               # MODIFIED — added grantless auth helpers

core/amazon_intel/
  db.py                   # MODIFIED — 4 new tables + pgvector extension
  snapshots.py            # REWRITTEN — reads ami_listing_content, fallback to flatfile

core/tools/
  ami_tools.py            # MODIFIED — schema docs + ALLOWED_TABLES
  registry.py             # MODIFIED — tool description

api/routes/
  amazon_intel.py         # MODIFIED — 15 new endpoints under /ami/catalog/* and /ami/notifications/*

deploy/
  Dockerfile.api          # MODIFIED — boto3 added

docs/cairn/
  amazon_notifications_setup.md   # NEW — AWS setup guide
  amazon_consumers.md             # NEW — legacy table consumer inventory
  CAIRN_CRM_PROJECT_STATE_CC_PROMPT.md  # NEW — next session brief

tests/
  test_catalog_enrichment.py      # NEW — 25 tests, all passing
```

---

## Session verdict

Pipeline is production-ready and running. The 4× daily cron will keep everything
current automatically. Next session can either:

(a) Start the CRM Project State work via the new brief, or
(b) Clean up the operational items above (AWS key rotation, sku_mapping
    cleanup, ads profile discovery), or
(c) Monitor first real SP-API notifications and build a notification → action
    processor (would feed naturally into the CRM work).

All commits pushed to `origin/master`. No pending uncommitted changes related
to this work.
