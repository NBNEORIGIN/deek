# CSV Import Decisions

## 2026-03-31 — Initial design

- Phase 1 uses manual CSV/TSV uploads from Seller Central (SP-API deferred to Phase 6)
- Four report types identified: FBA Manage Inventory, Sales & Traffic, Restock Inventory, Personalisation downloads (ZIP)
- Seed imports use openpyxl to read directly from Shipment_Stock_Sheet.xlsx
- Runtime imports will accept CSV/TSV uploads via the web UI
- MASTER STOCK header is at row 2 (rows 0-1 are summary rows) — parser must skip them
