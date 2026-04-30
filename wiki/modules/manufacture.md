# Manufacture

## What It Does
Production management system for NBNE's Origin Designed product range. Tracks
product definitions (M-numbers), production pipeline stages, FBA shipments, stock
levels across multiple sales channels, and machine assignments. Currently being
built to replace the master Excel workbook.

## Who Uses It
- **Toby Fletcher** — product design, production planning, stock management
- **Production staff** — daily make-list, machine assignments, shipment packing

## Tech Stack
- Backend: Django + PostgreSQL (planned)
- Frontend: Next.js (planned)
- Hosting: Hetzner (manufacture.nbnesigns.co.uk, ports 8015/3015)
- Current authority: Excel workbook (Shipment_Stock_Sheet.xlsx)

## Connections
- **Feeds data to:** [[modules/amazon-intelligence]] (M-number + margin data),
  [[modules/cairn]] (context endpoint)
- **Receives data from:** [[modules/render]] (ASIN mapping)
- **Context endpoint:** `GET /api/cairn/context` — make list, machine status, stock alerts

## Current Status
- Build phase: All phases deployed to manufacture.nbnesigns.co.uk
- Last significant change: Full deployment (March 2026)
- Known issues: Excel workbook remains authoritative reference during transition

## Key Concepts
- **M-number:** Master product reference (M0001, M0002, etc.) — permanent, never modified once assigned
- **Blank:** Physical substrate a product is printed on, named after infamous people:
  DONALD (circular), SAVILLE (aluminium), DICK (acrylic), STALIN (large format),
  MYRA, IDI, TOM (memorial stake), JOSEPH, HARRY, AILEEN
- **Machine names (RATIFIED 2026-04-30 — canonical, case-sensitive):**
  - **Print + cut:** Rolf (Refine Color 6090, UV flatbed), Mao (Refine Color ZZ1S, UV small-format),
    Mimaki (Mimaki 6042 MkII, UV flatbed B&W, EOL), Mutoh (Mutoh XPJ-461UF, UV flatbed small-format, lease),
    Roland (Roland MG-300, UV roll-to-roll print-and-cut), Epson (Epson SC-F500, dye sublimation)
  - **Cut + engrave:** Beast (Thunderlaser Nova 64 CO2 laser), Fiber Laser (model TBC)
  - **CNC routing:** Hulk (Piranha 8'×4'), Avid (Avid Pro 8'×4')
  - **Additive:** Jeffrey (Bambu Labs H2S), Peter (Bambu Labs P1S)
  - **Application + finishing:** Application Table (EWS 3000×1750), LSealer (semi-auto L-sealer), Heat Tunnel
  - **Metal + coating:** welder (HITBOX HIM250DPP MIG, new), brake (box-and-pan, in-build), oven (IR powder coat, planned)
  - Earlier docs listed ROLF as a Mimaki UV or as a Roland — both wrong. ROLF is the Refine Color 6090.
    HULKY → Hulk (canonical). See `projects/manufacturing/machines/<nickname>.md` for full identity cards.
- **Production pipeline:** Designed → Printed → Processed → Cut → Labelled → Packed → Shipped
- **Sales channels:** UK, US, CA, AU, EBAY, ETSY, FR
- **FBA:** Fulfilled By Amazon — stock held in Amazon warehouse DIP1

## Related
- [[modules/amazon-intelligence]] — listing health uses M-number data
- [[modules/etsy-intelligence]] — Etsy listings map to M-numbers
- [[modules/render]] — publishes product designs to marketplaces
