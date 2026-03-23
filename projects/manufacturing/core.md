# Manufacturing / Origin Designed — CLAW Agent Core Context
# Version: 1.0

## What this is
The manufacturing management system for NBNE's Origin Designed product
range. Currently a complex Excel/Google Sheets workbook being replaced
by a proper Django application. Manages product definitions, production
runs, FBA shipments, and stock levels across multiple channels.

## Non-negotiable rules

1. Never modify an M-number once assigned — they are permanent references.
   M-numbers are the single source of truth for product identity.

2. Stock levels are sacrosanct — never auto-update without explicit
   user confirmation.

3. FBA (Fulfilled By Amazon) shipments have strict Amazon labelling
   requirements. Never mark a shipment as complete without label verification.

4. Always distinguish between DIP1 (Amazon fulfilment warehouse) and
   local/3PL stock. They are tracked separately.

5. Channel prices are never stored as literals in code.
   All pricing lives in the database.

## Domain vocabulary — MEMORISE THESE

M-number: master product reference (M0001, M0002, etc.)
  The canonical identifier for a product design.
  One M-number can have multiple SKUs (UK, US, CA, AU, eBay, Etsy, etc.)

Blank: the physical substrate/template a product is printed on.
  Named after infamous people for internal memorability:
  DONALD  = circular push/pull sign shape
  SAVILLE = rectangular aluminium composite (A4-ish landscape)
  DICK    = landscape acrylic plaque
  STALIN  = large format aluminium panel
  MYRA    = specific proprietary shape
  IDI     = push/pull door sign variant
  TOM     = memorial garden stake
  JOSEPH  = standing display/counter format
  HARRY   = specific rectangular format
  AILEEN  = specific format

Machine names (also named, internal references):
  ROLF    = UV flatbed printer (Mimaki UV)
  MIMAKI  = sublimation/dye-sub printer
  MUTOH   = wide format inkjet
  ROLAND  = vinyl cutter / print-and-cut
  EPSON   = sublimation printer (SC-F500)
  HULKY   = specific large-format printer

Production pipeline stages (in order):
  Designed → Printed → Processed → Cut → Labelled → Packed → Shipped

Sales channels: UK, US, CA, AU, EBAY, ETSY, FR

FBA = Fulfilled By Amazon (stock held in Amazon warehouse DIP1)

## Target architecture
Django backend with models for:
  Product (M-number, description, blank, material, stock)
  SKU (channel-specific identifiers linking to Product)
  ProductionOrder (what to make, progress through pipeline)
  Shipment (FBA batches, tracking, labelling status)
  Procurement (materials, reorder points, supplier)

## Current state
Application not yet built. CLAW is being used to design and build it.
The Excel workbook (Shipment_Stock_Sheet.xlsx) is the authoritative
reference for domain understanding and data structure.
Key Excel sheets: ORDERS, MASTER STOCK, ASSEMBLY, DIBOND PLACEMENT,
                  SUB PLACEMENTS, RECORDS, PROCUREMENT
