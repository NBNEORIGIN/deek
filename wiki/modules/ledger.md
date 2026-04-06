# Ledger

## What It Does
Full-featured bookkeeping and financial management system for NBNE. Tracks revenue
across all sales channels (Amazon UK/US/FR/DE/IT/ES/AU/CA, Etsy, eBay, Shopify,
Xero), expenditure with VAT split and category breakdown, cash position across
multiple bank accounts, procurement and stock alerts, and generates Profit & Loss
reports. Includes invoice OCR via Claude Vision with Contabo S3 document archival
and HMRC 6-year retention tracking.

## Who Uses It
- **Toby Fletcher** — financial oversight, revenue analysis, cash management, P&L review
- **Cairn Business Brain** — polls Ledger's context endpoint for live financial data in dashboard

## Tech Stack
- Backend: FastAPI (Python) + PostgreSQL 16 + SQLAlchemy + Alembic migrations
- Frontend: Next.js 16 + React 19 + Tailwind CSS + Recharts + shadcn/ui
- OCR: Claude Vision (Sonnet) for invoice extraction
- Document storage: Contabo Object Storage (S3-compatible, eu2.contabostorage.com)
- FX rates: Frankfurter API (auto) with manual override
- Hosting: Hetzner (ledger.nbnesigns.co.uk), ports 8016/3016
- Local dev: D:\ledger, ports 8001/3001, DB on localhost:5433

## Connections
- **Feeds data to:** [[modules/cairn]] (context endpoint — revenue, cash, expenditure, margins, procurement alerts),
  [[modules/crm]] (margin data for pipeline prioritisation)
- **Receives data from:** [[modules/phloe]] (booking revenue — Phase 7, planned)
- **Context endpoint:** `GET /api/cairn/context` — revenue MTD/YTD by channel, cash position
  by account, procurement alerts, expenditure MTD, document storage stats, margin calculations

## Current Status
- Build phase: Production (deployed to Hetzner)
- Features live: Revenue tracking (multi-channel, multi-currency), expenditure with VAT,
  cash position (multi-account), CSV imports (Amazon/Etsy/eBay with dedup + FX),
  invoice OCR + S3 archival, P&L reports, procurement alerts + AI chat, Cairn integration,
  dark mode, PWA, mobile-responsive
- Planned: Phloe revenue integration (Phase 7), direct Xero API sync
- Known issues: Single-tenant only (NBNE), no multi-tenant support yet

## Key Concepts
- **Channel revenue:** Gross revenue per sales channel with fee deduction (channel fees, ad spend, postage)
- **Multi-currency:** Auto FX conversion via Frankfurter API, manual override, batch recalculation
- **Import dedup:** Order-ID primary key + content hash for null-order-id rows prevents duplicates
- **Invoice OCR:** Claude Vision extracts supplier, date, amounts, line items, VAT, currency from PDFs/images
- **HMRC retention:** 6-year document retention calculation per tax year
- **Fixed costs:** Recurring monthly expenses (rent, wages, insurance) with smart P&L logic — not double-counted when actual expenditure exists
- **Procurement alerts:** Items below reorder point flagged, AI chat for inventory questions
- **Context endpoint:** Polled by Cairn every 60 minutes for business brain dashboard

## Data Model
- **revenue_transactions** — all sales across channels with FX conversion
- **expenditure** — operational expenses with VAT split, category, document links
- **fixed_costs** — recurring monthly costs
- **cash_snapshots** — bank account balance tracking over time
- **procurement_items** — inventory with reorder points and stock levels
- **import_batches** — audit trail for CSV imports
- **documents** — OCR-extracted invoices with S3 paths and HMRC retention
- **exchange_rates** — FX rates used per import with source tracking

## Related
- [[modules/cairn]] — financial data appears in business brain responses
- [[modules/phloe]] — booking revenue will flow in (Phase 7)
- [[modules/crm]] — margin data helps prioritise the sales pipeline
- [[modules/amazon-intelligence]] — revenue data cross-references with AMI listing health
