# Manufacture — FBA Restock Module

## What It Does
Generates per-marketplace FBA replenishment plans by downloading Amazon's
inventory planning report (GET_FBA_INVENTORY_PLANNING_DATA) via SP-API and
computing recommended send quantities using a 90-day demand minus on-hand formula.

**Phase 1 (complete):** SP-API sync + restock calculation + production order creation

## Who Uses It
Ben (production lead) reviews and approves restock plans. Approved plans
create production orders directly in the Manufacture pipeline.

## Tech Stack
- Backend: Django 5.x at `manufacture/backend/restock/`
- Algorithm: 90d demand − on_hand (`newsvendor.py` — name retained to avoid DB/API changes)
- SP-API: Direct LWA calls — `spapi_client.py` calls Amazon directly. Cairn HTTP unreachable from Manufacture container.
- SKU resolution: Local Manufacture `SKU` table first, Cairn `/ami/sku-mapping/lookup` fallback
- UI: Next.js at `frontend/src/app/restock/page.tsx`
- Daily cron: `sync_restock_all` management command, 6am UTC

## Restock Formula (current)

```python
recommended = max(0, units_sold_30d * 3 - (units_available + units_inbound))
```

- **90d demand** = `units_sold_30d × 3` (report only provides 30-day sales figure)
- **on_hand** = `units_available + units_inbound` (FBA available + inbound to FBA)
- Zero-velocity items → 0
- If on_hand already covers 90d demand → 0
- Confidence = 1.0 if sold_30d ≥ 5, else 0.5

**Examples (GB, 2026-04-07):**
| M-number | sold_30d | avail | inbound | Rec |
|---|---|---|---|---|
| M0001 | 104 | 188 | 40 | 84 (312 − 228) |
| M0003 | 212 | 520 | 80 | 36 (636 − 600) |
| M0006 | 28 | 17 | 10 | 57 (84 − 27) |

UI column label: **"Rec. Qty (90d)"**

## Key Concepts
- **90-day target**: Stock level we want to maintain = 3 × last-30-day sales
- **on_hand**: Everything already at FBA or in transit to FBA
- **Confidence score**: 1.0 (≥5 units/30d) or 0.5 (low data — treat as guide only)
- **Zero-velocity rule**: Items with 0 units sold → recommend 0
- **Amazon rec also shown**: Side-by-side; user decides which to use when approving

## Workflow
```
[Daily 6am cron] python manage.py sync_restock_all →
  All 6 marketplaces synced sequentially (10s gap between each)
  Logs to /var/log/manufacture-restock.log

User clicks "Sync GB" (manual trigger also available) →
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

Table features:
  - Click column headers to sort (asc/desc toggle)
  - DoS range filter: All / Critical (<14d) / Low (<30d) / OK (30–90d) / Overstocked (>90d)
  - P checkbox column: check = mark as personalised (D2C only, excluded from FBA)
                       uncheck = restore to FBA. Bidirectional, works per M-number.
  - Personalised rows shown at opacity-50

User edits "Send qty" per item, selects rows →
  POST /api/restock/approve/ stores approved_qty
  POST /api/restock/create-production/ creates ProductionOrder + stages
```

## D2C Workflow (page.tsx at /d2c)
```
Zenstores CSV upload:
  POST /api/imports/upload/ {report_type: 'zenstores', file}
  Returns: {changes: [...], skipped: [...]}
  Renders parsed orders as data table: order_id, sku, m_number, description, qty, channel, flags

Personalised products panel:
  Shows all RestockExclusion entries (read-only — manage via restock planner P checkbox)
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

## Row counts per marketplace (confirmed 2026-04-07)
| Marketplace | Region | Rows |
|---|---|---|
| GB | EU | 514 |
| US | NA | 229 |
| CA | NA | 210 |
| AU | FE | 330 |
| DE | EU | 40 |
| FR | EU | 44 |

All 6 marketplaces synced successfully as of 2026-04-07.

## Stale running reports — recovery pattern
If a sync dies mid-run (e.g. container restart, bad token), `RestockReport` stays `status='running'`
indefinitely. This blocks the UI from showing any data for that marketplace.

**Fix:**
```python
# Run inside the container
RestockReport.objects.filter(status='running').update(
    status='error', error_message='Stale — abandoned run'
)
```

Shell command:
```bash
docker exec docker-backend-1 python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from restock.models import RestockReport
n = RestockReport.objects.filter(status='running').update(status='error', error_message='Stale')
print(n, 'fixed')
"
```

Root cause of initial US stale reports: NA refresh token corrupted by heredoc copy (d→t),
causing 400 on LWA token exchange. Fixed using Python re.sub. See hetzner.md for safe pattern.

## Decision Log

### 2026-04-07 — Newsvendor inventory subtraction + DoS gate
Q* is an optimal stock level (total desired inventory), not an order quantity. Without subtracting
on-hand inventory, the algorithm would recommend sending units that are already at FBA.
Added Gate 1 (DoS gate: skip if days_of_supply >= 37d horizon, no urgent alert) and Gate 2
(subtract units_available + units_inbound from gross Q*). Example: M0003 with 521 units and
106 DoS now correctly returns 0. assembler.py passes both fields into NewsvendorInput.

### 2026-04-07 — Daily sync via management command
`sync_restock_all` management command syncs all 6 marketplaces sequentially at 6am UTC via cron.
10s pause between marketplaces for SP-API rate limiting. Logs to /var/log/manufacture-restock.log.
Manual sync via UI still available for ad-hoc use.

### 2026-04-07 — Restock table sort and DoS filter
Column sort: SortHeader component, click to sort asc/desc. Client-side sort on sortedItems.
DoS filter: dropdown with 5 ranges, applied client-side on filteredItems before sort.
Default sort: alert descending (urgent items first).

### 2026-04-07 — Personalised checkbox (P column)
Single checkbox per row toggles RestockExclusion via POST/DELETE /api/restock/exclusions/.
Bidirectional: check = D2C only (FBA excluded); uncheck = restore to FBA.
Works at M-number level — all SKUs for an M-number are excluded together.
Personalised rows rendered at opacity-50 to indicate exclusion visually.

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

### 2026-04-07 — D2C page Zenstores upload returns changes[] not items[]
The `apply_zenstores` endpoint returns `{changes: [...], skipped: [...]}` — the key is `changes`,
not `items`. Frontend must use `data.changes || []` to extract parsed orders.

### 2026-04-07 — Stale running reports blocked US/DE/AU display
After fixing the NA refresh token, 3 RestockReport records were stuck in `status='running'`
from failed earlier attempts. The UI showed empty data for those marketplaces until records were
manually updated to `status='error'`. Lesson: add an age-based stale detection to the sync view
endpoint (e.g. running reports older than 30 minutes should be auto-expired). Until that is
implemented, use the manual recovery script documented in the Stale running reports section above.

### 2026-04-07 — All 6 marketplace tokens confirmed working
After full sync: GB 514, US 229, CA 210, AU 330, DE 40, FR 44 rows.
EU region (GB/DE/FR) and FE region (AU) were unaffected by the token corruption.
NA region (US/CA) was blocked by the corrupted refresh token; fixed and both now sync cleanly.

### 2026-04-07 — Replaced Newsvendor with 90d demand − on_hand formula
The probabilistic Newsvendor model (critical ratio, normal CDF, safety stock, Cu/Co costs) was
replaced with a simpler, more intuitive formula:

  `recommended = max(0, units_sold_30d × 3 − (units_available + units_inbound))`

**Why:** The Newsvendor model required margin data (not available) and produced confusing results
that were hard to explain or challenge. The 90-day target is immediately legible: "we sold X last
month, we want 3 months of stock, we already have Y, so send Z."

**Retained:** `newsvendor.py` filename and `newsvendor_qty` DB column/API field kept to avoid
migrations and API churn. UI column label updated from "Newsvendor" to "Rec. Qty (90d)".

**Note on tests:** `test_newsvendor.py` tests the interface contract — expected values must be
updated if the formula changes again. The `NewsvendorInput` dataclass retains all old fields
(price, margin, cv, etc.) but they are now unused; clean up in a future refactor if desired.
