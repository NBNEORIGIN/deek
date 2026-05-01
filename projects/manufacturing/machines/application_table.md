---
id: application_table
nickname: Application Table
brand: EWS
brand_provenance: UNCERTAIN — see provenance note
manufacturer: TBD (research needed)
model: TBD
working_area_mm: 3000 × 1750
niche: vinyl / graphics application, lamination, mounting, pre-masking, large-substrate finishing
stream: finishing
status: live-lease
ownership: LEASED via Siemens (bundled with Roland MG-300 on same Siemens lease; expires October 2026)
lease_partner_machine: Roland (TrueVIS MG-300)
year_acquired: TBD
features_typical_for_class:
  - flatbed glass top (typically backlit / LED illuminated)
  - pneumatic-pressure roller
  - self-healing cutting mat (consumable)
  - integrated air supply (typical 6 bar working pressure)
  - media roll holders (typically on glide beam and short side)
location:
serial:
purchased:
primary_user:
technical_owner:
notes: |
  The downstream finishing partner to NBNE's vinyl workflow. The
  3000 × 1750 mm working area is large enough for full vehicle
  graphics panels, large window displays, and oversized banner
  mounts. Standard signage workflow: print on Roland → cut → weed
  → apply transfer tape → apply to substrate on Application Table
  → trim. Eliminates manual squeegee work for any application
  bigger than a hand-held panel.

  EWS PROVENANCE GAP: "EWS" did not surface as a known UK flatbed
  applicator brand in the chat-Claude research — established UK
  options at this size are ROLLSROLLER (Sweden), Rollover
  (Norway), Kala AppliKator, Mounter's Mate Workstation, CWT
  (Sweden), Easty. Possible explanations:
    1. House-brand of the supplying UK dealer
    2. Less-indexed continental brand (German / Dutch industrial)
    3. Abbreviation or mis-remembered name (could be a model code
       rather than the manufacturer name)
  Resolve by reading the rear plate (full manufacturer name +
  model + serial), checking the Siemens lease asset schedule, or
  asking the supplying dealer. **Highest priority** — without
  knowing what the table actually is, we can't value it, find
  replacement parts, or make the October 2026 lease decision.

  COMMERCIAL CONTEXT: bundled with the Roland MG-300 on a single
  Siemens lease that expires October 2026. The EWS-specific
  implications of that decision (return condition for the table,
  replacement options at this size, whether the lease structure
  even permits Path C "unbundle and decide separately") live in
  `application-table-lease.md` (slim cross-reference) and the
  full lease decision framework lives in `roland-lease.md`.

  Common-noun risk: "Application Table" is descriptive — could
  collide with "table" as a generic English word. The brand+
  niche+aliases fields disambiguate.
aliases:
  - application table
  - app table
  - the application table
  - ews
  - ews 3000
  - ews application table
  - the big table
  - the flatbed applicator
  - the laminator
manuals_path: /opt/nbne/manuals/Application Table/
ratified: 2026-04-30
research_dossier: 2026-05-01 (Toby + chat-Claude — generic procedures + lease cross-ref ingested; full ingest blocked on EWS manufacturer ID)
---

# Application Table — EWS 3000 × 1750 vinyl applicator

The Application Table is NBNE's flatbed graphics applicator: a
3000 × 1750 mm working surface with pneumatic pressure roller,
glass-top bed, and integrated air supply, used to apply printed
vinyl and graphics to substrates after they come off the Roland.

EWS-branded — but **the EWS provenance is uncertain**: the brand
did not surface as a known flatbed-applicator manufacturer in
chat-Claude's research. Most likely a dealer house-brand, an
under-indexed continental manufacturer, or an abbreviation /
model code mistaken for the brand. **Resolve before the October
2026 lease decision** — it's the highest-priority open gap.

The Roland MG-300 → Application Table → trim workflow is the
real product for vinyl jobs. Either machine alone is materially
less useful; together they cover the production-volume vinyl
graphics niche.

## Manual coverage

Generic flatbed-applicator content ingested 2026-05-01:
- `application-table-procedures.md` — daily start-up, application
  workflow (taped-edge hinge, peel-back, roller pass), cutting-mat
  + air-supply + roller maintenance
- `application-table-lease.md` — slim cross-reference to the
  Roland's bundled Siemens lease + EWS-specific return-condition
  notes
- `application-table-tips.md` — operator wisdom for flatbed
  applicators generally + lease-end photographs habit

The MANUFACTURER'S OWN DOCUMENTATION is blocked on identifying
the actual EWS manufacturer. Once known, repeat the research
pattern used for Beast / Hulk / Mutoh.

Searchable via `search_manuals(query=..., machine="Application Table")`.

## Maintenance log

_Per-event entries to be appended over time, dated. Cutting-mat
replacements, roller calibrations, air-system service all live
here as dated entries._

## Open gaps — to fill before October 2026 lease decision

### Critical

1. **EWS manufacturer + model** — chassis rear plate, Siemens
   lease asset schedule, or supplying-dealer enquiry.
   **HIGHEST PRIORITY** — gates valuation, parts sourcing, and the
   October 2026 lease decision.
2. **Cutting-mat brand and replacement source** — once
   manufacturer is known, confirm which mat fits.
3. **Air-supply spec** — pressure target, integrated compressor
   make/model, oil-free vs lubricated.

### Important

4. **Year of acquisition / install date** — Siemens lease handover paperwork.
5. **Original supplying dealer** — same as Roland's open gap.
6. **Service history** — has it been serviced? Cutting mat
   replaced? Any issues?
7. **Primary user / technical owner**

## Tribal knowledge

Most application failures are operator technique — the table
magnifies good technique but can't compensate for poor. 30 min
with NBNE's lead applicator, recorded + transcribed, captures
the substrate-specific knowledge no manufacturer manual covers.
