# Ledger — Domain Context
# NBNE Financial Management System

## Purpose
Operational financial intelligence. Not accounting. Answers: what did we earn,
what did we spend, what is our cash position, what do we need to reorder?

## Stack
- Backend: FastAPI (port 8001 local / 8016 Hetzner)
- Frontend: Next.js + shadcn/ui + Tailwind (port 3001 local / 3016 Hetzner)
- Database: PostgreSQL (port 5432 local / Docker on Hetzner)
- AI: Claude Sonnet for procurement chat
- Live: https://ledger.nbnesigns.co.uk

## Data Sources & Import Formats

### 1. Amazon — Monthly Transaction CSV (8 regions, 6 languages)

**Import endpoint:** `POST /api/import/amazon` (drag-and-drop on Import page)

The Amazon parser auto-detects region, language, and format from the file:

| Region | Language | Currency | Preamble | Date Format | Decimal |
|--------|----------|----------|----------|-------------|---------|
| UK     | English  | GBP      | 7 lines  | `1 Mar 2026 00:00:50 UTC` | Period |
| US     | English  | USD      | 9 lines  | `Jan 1, 2026 6:03:32 PM PST` | Period |
| CA     | English  | CAD      | 9 lines  | `Jan 2, 2026 4:46:17 a.m. PST` | Period |
| AU     | English  | AUD      | 7 lines  | `2 Jan 2026 7:14:53 am GMT+9` | Period |
| FR     | French   | EUR      | 7 lines  | `2 janv. 2026 14:34:22 UTC` | Comma |
| DE     | German   | EUR      | 7 lines  | `02.01.2026 03:40:01 UTC` | Comma |
| IT     | Italian  | EUR      | 7 lines  | `2 gen 2026 10:30:00 UTC` | Comma |
| ES     | Spanish  | EUR      | 7 lines  | `7 ene 2026 23:19:43 UTC` | Comma |

**Two Amazon accounts:** Origin Designed (primary) and Origin Crafts.
Both use the same import endpoint — they merge into the same channel
(e.g. both FR accounts → `amazon_fr`). If separation needed in future,
add an `account` field.

**FX conversion:** Auto-fetched from frankfurter.app on import, fallback to
hardcoded rates. Stored per batch in `exchange_rates` table. Override via
`PATCH /api/fx/rates/{id}` which recalculates all transactions in that batch.

**Income reconciliation (validated against PDF summary):**
```
Income = product_sales + postage/shipping_credits + gift_wrap_credits
       + promotional_rebates + positive Adjustment 'other' (inventory credits)
```

### 2. Etsy — Monthly Statement CSV

**Import endpoint:** `POST /api/import/etsy`

**Format:** Standard CSV, no preamble. Columns: Date, Type, Title, Info,
Currency, Amount, Fees & Taxes, Net, Tax Details.

**Row types:** Sale, Fee, Tax, Refund, Deposit, Marketing.
Parser aggregates fees per order, distributes marketing (Etsy Ads) and
listing fees proportionally across orders.

**Note:** Etsy is operated as a sole tradership — no VAT due.

### 3. eBay — Transaction Report CSV

**Import endpoint:** `POST /api/import/ebay`

**Format:** Variable preamble (find header row with 'Transaction creation date').
Columns include per-order fee breakdown: Final value fee (fixed + variable),
Regulatory operating fee, International fee.

**IMPORTANT:** Use the **Transaction Report** from Seller Hub → Payments → Reports.
The **Orders Report** does NOT contain fee data and should not be used.

**Row types:** Order, Refund, Other fee (listing/insertion fees), Payout.
Parser distributes Other fees proportionally across orders.

### 4. Xero — Sales Invoices Export CSV

**Import:** Via `scripts/import_xero_invoices.py` (not yet on Import page)

**Format:** Standard CSV from Xero. Columns: ContactName, InvoiceNumber,
InvoiceDate, LineAmount, TaxAmount, TaxType, etc.

**CRITICAL RULES:**
- EXCLUDE all Amazon contacts (already imported from Amazon CSVs)
- DEDUCT VAT (use LineAmount not Total — management accounts are ex-VAT)
- These are B2B/commercial sales (sign making, local businesses)

**Amazon contacts to skip:** Amazon UK, Amazon US, Amazon CA, Amazon fr,
Amazon DE, Amazon IT, Amazon ES, Amazon SE, Amazon NL, Amazon BE, Amazon AU

### 5. Expenditure — Management Accounts CSV

**Import:** Via `scripts/import_expenditure_csv.py`

**Format:** Exported from the Budget spreadsheet, Sheet 001 Expenditure.
4 preamble rows, headers on row 5. Key columns: Date (col 0), Supplier (col 2),
Description (col 3), Material Type/category (col 4), Cost Ex VAT (col 10),
Payment Method (col 13).

**KNOWN ISSUES:**
- DHL shipping appears BOTH as a monthly estimate (£2,000) AND as line-by-line
  DEXT entries. The estimates must be removed — actual DHL costs are in the
  line items.
- Royal Mail estimates (£5,000/month) should be replaced with actual weekly
  invoices from the Royal Mail business portal.
- DIRTRANS entries are Directors' Dividends — must be categorised as
  'Directors Dividends', not 'Overhead'. Shown below the operating profit line.

### 6. Royal Mail — Business Portal Invoices

**Import:** Manual entry or script. Weekly invoices from Royal Mail portal.

**Format:** Not a CSV export — scraped/copied from the Royal Mail business
portal invoice list. Fields: Invoice number, due date, amount (GBP).

**Actual Q1 2026:** Jan £3,690, Feb £2,947, Mar £5,257 (total £11,894)
vs the £15,000 estimate that was in the spreadsheet.

### 7. Cash Snapshots

**Import:** Manual entry via Cash page or from Sheet 006 JO in the workbook.

**Accounts:** Lloyds Current, Wise GBP, Total Incoming.

---

## P&L Structure

```
Revenue (11 channels)
  - Channel fees (Amazon/Etsy/eBay marketplace fees)
  - Ad costs (Amazon PPC, Etsy Ads)
= GROSS PROFIT (target: >40%)

  - Overhead (salaries, rent, pension, insurance, software, utilities)
  - Shipping (Royal Mail actual weekly invoices)
  - Other (materials from DEXT, actual DHL shipments, supplies)
= OPERATING PROFIT

  - Directors Dividends (£3,000/month)
= RETAINED PROFIT
```

## Revenue Channels (Q1 2026 actual)
- Amazon UK: £84,801 (GBP direct)
- Amazon US: £54,925 (USD @ 0.79)
- Etsy: £11,105 (GBP, sole tradership, no VAT)
- Amazon FR: £9,921 (EUR @ 0.86, two accounts: OD + OC)
- Amazon AU: £8,428 (AUD @ 0.52)
- Amazon CA: £8,365 (CAD @ 0.57)
- Xero B2B: £8,359 (GBP, ex-VAT commercial sign work)
- eBay: £2,962 (GBP)
- Amazon DE: £2,543 (EUR @ 0.86, two accounts: OD + OC)
- Amazon IT: £865 (EUR @ 0.86)
- Amazon ES: £143 (EUR @ 0.86)

## Hard Rules
- Never store money as floats — always NUMERIC/Decimal
- Never auto-delete imported data
- Cairn context endpoint must always return valid JSON even if data is empty
- All CSV imports are idempotent (dedup by channel + order_id + content hash)
- Xero imports MUST exclude Amazon contacts and deduct VAT
- DHL: never import both estimate AND line-by-line actuals
- Royal Mail: use actual weekly invoices, not monthly estimates
- DIRTRANS = Directors Dividends, shown below operating profit line
- One logical change per commit
