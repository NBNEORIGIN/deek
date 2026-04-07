# Amazon Intelligence — Analytics Module

## What It Does
Provides clean, idempotent daily sales and traffic data for all NBNE Amazon
marketplaces. Replaces the double-counting 30-day rolling aggregate approach.

## The Counting Problem (solved)
Previous approach stored GET_SALES_AND_TRAFFIC_REPORT (30-day lump) 4x daily.
Any SUM query produced 4x the real figure. Fixed by switching to order-level
storage (ami_orders) and day-granularity traffic (ami_daily_traffic), both
with UNIQUE constraints that prevent double-counting regardless of sync frequency.

## Data Sources
- **ami_orders** — atomic order lines from GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL.
  UNIQUE on (amazon_order_id, order_item_id). Source of truth for all revenue.
- **ami_daily_traffic** — daily sessions/traffic from GET_SALES_AND_TRAFFIC_REPORT
  with dateGranularity=DAY. UNIQUE on (marketplace, asin, date).
- **ami_velocity** — computed daily from ami_orders. Velocity alerts live here.
- **ami_business_report_legacy** — retired. Do not write to. Still read by
  build_snapshots() until Sprint 2.

## Marketplaces
GB (EU), DE (EU), FR (EU), IT (EU), ES (EU), US (NA), CA (NA), AU (FE)

## Sync Schedule
Orders: 4x daily (cron, same as existing: midnight/6am/noon/6pm UTC)
Traffic: 4x daily (sync_daily_traffic, DAY granularity)
Velocity compute: after each full sync (post-snapshots)
Backfill: manual via POST /ami/analytics/backfill?region=EU&days=90

## API Endpoints
- GET /ami/analytics/revenue — revenue by period/marketplace from ami_orders
- GET /ami/analytics/revenue/summary — today/WTD/MTD/YTD with period-over-period %
- GET /ami/analytics/traffic — daily sessions/page_views/Buy_Box from ami_daily_traffic
- GET /ami/analytics/alerts — velocity alerts (VELOCITY_DROP/ZERO_DAYS/SURGE)
- POST /ami/analytics/alerts/{id}/acknowledge
- GET /ami/analytics/top-products
- GET /ami/analytics/data-quality — data freshness and integrity check

## UI
/analytics/revenue in Cairn web interface. Summary tiles, daily bar chart,
top products, velocity alerts feed, Data Quality badge with modal.

## Cairn Chat Integration
/ami/cairn/context now includes a `revenue` section from ami_orders.
Cairn can answer:
- "What did we take today?" → today figure
- "What's our best seller this month?" → top_5_products_30d
- "Are there any listings in trouble?" → active_alerts
- "What's US revenue this month?" → by_marketplace['US']

## Connections
- Reads from: ami_sku_mapping (SKU→ASIN→M-number resolution)
- Reads from: SP-API (orders + traffic reports via scheduler)
- Writes to: ami_orders, ami_daily_traffic, ami_velocity
- Feeds: /ami/cairn/context (Cairn chat), /analytics/revenue (UI)

## Sprint Roadmap
- Sprint 1 (done): Revenue Truth Engine — orders + traffic + velocity + UI
- Sprint 2: Flip build_snapshots() from ami_business_report_legacy to ami_daily_traffic
- Sprint 3: Margin Dashboard (add FBA fee puller)
- Sprint 4: Inventory Age Monitor
- Sprint 5: Returns + Reimbursement Recovery

## Decision Log

### 2026-04-07 — Order-level storage as revenue source of truth
30-day rolling aggregates are the wrong shape for trend analysis and cause
double-counting at any sync frequency > once per 30 days. Order-level storage
with a natural unique key is idempotent by construction.

### 2026-04-07 — PII exclusion from ami_orders
Buyer name, email, address, phone are skipped at parse time and never stored.
ship_country is retained for marketplace inference. No other buyer PII is held.

### 2026-04-07 — Legacy table retained, not dropped
build_snapshots() reads ami_business_report_legacy for sessions/conversion/buy_box.
Flipping snapshots to ami_daily_traffic is Sprint 2 scope — not done here to
keep Sprint 1 bounded and reduce risk to the live system.

### 2026-04-07 — Velocity alerts de-duplicated over 7-day window
Do not re-raise the same alert type for an ASIN+marketplace that already has
an unacknowledged alert of that type within the last 7 days. Prevents alert
fatigue from 4x daily syncs.
