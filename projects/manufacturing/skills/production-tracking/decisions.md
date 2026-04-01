# Production Tracking Decisions

## 2026-03-31 — Initial design

- Pipeline stages match existing ORDERS sheet columns: Designed, Printed, Processed, Cut, Labelled, Packed, Shipped
- ORDERS sheet has boolean columns for each stage — production-tracking models will use the same pattern with timestamps and operator tracking added
- RECORDS sheet (2,458 rows since Dec 2023) provides historical production data that can be imported later (Phase 7)
