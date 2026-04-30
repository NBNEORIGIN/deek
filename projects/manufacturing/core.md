# Manufacturing / Origin Designed — DEEK Agent Core Context
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

Machine names — canonical nicknames as used in chunk_name and search filters.
RATIFIED 2026-04-30 by Toby + review-Claude. These are the spellings every doc
should use; case-sensitive. See projects/manufacturing/machines/<nickname>.md
for per-machine identity cards (Layer 1 — brand, model, niche, aliases).

  Print + cut
    Rolf     = Refine Color 6090 — UV flatbed, twin Epson i1600 (CMYK + white)
    Mao      = Refine Color ZZ1S — UV small-format, Epson XP600 head
    Mimaki   = Mimaki 6042 MkII — UV flatbed, B&W prints (live, EOL)
    Mutoh    = Mutoh XPJ-461UF — UV flatbed, small-format rigid (lease)
    Roland   = Roland MG-300 — UV roll-to-roll print-and-cut, 30" wide
    Epson    = Epson SC-F500 — dye sublimation
  Cut + engrave
    Beast    = Thunder Laser Nova 63 — CO2 laser cutter/engraver
    Fiber Laser = (model TBC) — fibre laser
  CNC routing
    Hulk     = Piranha 8'×4' — 9kW spindle, ATC, vacuum table
    Avid     = Avid Pro 8'×4' — Clearpath servos, Masso controller
  Additive
    Jeffrey  = Bambu Labs H2S — FDM 3D printer
    Peter    = Bambu Labs P1S — FDM 3D printer
  Application + finishing
    Application Table = EWS 3000×1750 — vinyl/graphics application
    LSealer  = semi-automatic L-sealer (brand TBC) — shrink-wrap sealing
    Heat Tunnel = (brand TBC) — shrink-wrap shrinking
  Metal + coating
    welder   = HITBOX HIM250DPP — double-pulse MIG (live, new)
    brake    = box-and-pan brake (in-build)
    oven     = IR-powered modular powder coating oven (planned)

Common-noun nickname risk: Beast, Hulk, Mao, Peter, Jeffrey, brake, oven,
welder, Fiber Laser all collide with everyday English. Each machine card MUST
include brand + model + alias list so BM25 / semantic retrieval can disambiguate.

Previous (now retired) listings: HULKY → Hulk (canonical). ROLF as Mimaki UV
or as Roland — both wrong; ROLF is the Refine Color 6090.

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
Application not yet built. DEEK is being used to design and build it.
The Excel workbook (Shipment_Stock_Sheet.xlsx) is the authoritative
reference for domain understanding and data structure.
Key Excel sheets: ORDERS, MASTER STOCK, ASSEMBLY, DIBOND PLACEMENT,
                  SUB PLACEMENTS, RECORDS, PROCUREMENT
