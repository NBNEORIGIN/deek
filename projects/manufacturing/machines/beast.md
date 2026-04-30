---
id: beast
nickname: Beast
brand: Thunder Laser
model: Nova 63
manufacturer: Dongguan Thunder Laser Equipment Co., Ltd
manufacture_country: China
uk_distributor: Thunder Laser UK (info@thunderlaser.co.uk, +44 1495 223811)
niche: CO2 laser cutter and engraver
stream: laser
status: live
year_acquired: 2021
ownership: owned outright (paid in full)
working_area_mm: 1600 × 1000
working_area_in: 63 × 39.4
laser_source: sealed CO2 glass tube
tube_fitted_wattage: 90W
tube_max_wattage: 100W
controller: Ruida (RDC6442G-class)
software: LightBurn (primary) / RDWorks (alternative)
red_dot_pointer: yes (through-lens)
dual_air_assist: yes (high/low pressure)
auto_focus: yes (optical)
chiller: CW-5200 (typical for 100W spec)
exhaust: 6" rear port, turbine fan
table: motorised rise/fall, aluminium blade bed + honeycomb bed
factory_warranty: expired (was 2yr machine, 1yr tube)
location:
serial:
purchased:
notes: |
  NBNE's primary CO2 laser cutter and engraver. Owned outright,
  acquired ~2021 (5 years old as of April 2026). Used for cutting
  and engraving non-metallic substrates: acrylic letters, wood,
  leather, fabric, cardboard, rubber, glass etching, coated metals
  (engrave only), anodised aluminium engraving. NOT used for bare
  metal — those go to the Fiber Laser. PVC and polycarbonate are
  forbidden (chlorine, toxic fumes, lens damage).

  The Nova 63 ships from factory in 100W or 130W only — there is
  no 90W product variant. The current 90W reading is most likely a
  100W tube degraded over its service life (CO2 glass tubes drop
  10-20% before failure). Worth confirming against the controller
  display, the laser-source label, or the spare-tube paperwork.
aliases:
  - beast
  - the beast
  - thunder laser
  - thunderlaser
  - nova
  - nova 63
  - nova-63
  - co2 laser
  - big laser
manuals_path: /opt/nbne/manuals/Beast/
ratified: 2026-04-30
research_dossier: 2026-04-30 (Toby + chat-Claude — Layer 2/3/5/6 ingested)
---

# Beast — Thunder Laser Nova 63 CO2 laser

Beast is NBNE's flagship CO2 laser cutter and engraver: a Thunder
Laser Nova 63 (1600 × 1000 mm working area, sealed CO2 glass tube,
Ruida RDC6442G controller, CW-5200 chiller). Acquired around 2021,
owned outright, factory warranty expired. Runs LightBurn day-to-day,
RDWorks as backup. Has dual air assist, optical auto-focus,
through-lens red dot pointer.

Used for: cutting + engraving non-metallics — acrylic, wood, leather,
fabric, cardboard, rubber, glass etching, anodised aluminium
engraving, coated-metal engraving.

NOT for: bare metal cutting/marking (Fiber Laser), polycarbonate
(yellows, toxic fumes), PVC (chlorine — damages optics + machine,
hazardous to operators).

Operators: TBD. Technical owner: TBD.

## Manual coverage

Layer 2 reference manuals — to download as PDFs and upload separately:

- NOVA Series Unified User's Manual (March 2024 edition, ~71pp, 6.3MB)
- User's Manual for NOVA-63 (December 2019 edition — original)
- OEM laser source manual (SPT C100 / C130)
- Ruida RDC6442G controller manual
- LightBurn documentation pages (export to PDF)

Layer 3 procedures, Layer 5 supply chain, Layer 6 tips — ingested
into chunk_type='manual' from the research dossier compiled by
chat-Claude on 2026-04-30. Searchable via `search_manuals(query=...,
machine="Beast")`.

## Maintenance log

_Per-event entries to be appended over time, dated. The Layer 6 tips
file is the dated append-only home; this is for machine-specific
maintenance events (tube replacement, optics replacement, alignments,
faults)._

## Open gaps — to fill from machine + operator inspection

1. **Serial number** — rear of machine, manufacturer's plate
2. **Lens diameter** — 20 mm or 25 mm (measure or check paperwork)
3. **Tube brand** — SPT vs RE-CI vs aftermarket (tube label / invoice)
4. **Spare tube wattage** — is the spare 90W, 100W, or other?
5. **Chiller model** — CW-5000 / CW-5200 / CW-5300 (rear plate)
6. **Last tube replacement date** — or "original 2021" if never
7. **Last full optics replacement** — when, who, cost
8. **Primary user** — who runs it day-to-day
9. **Technical owner** — who diagnoses faults, manages alignment
10. **LightBurn version** in current use, and whether licence
    credentials are in NBNE password manager
