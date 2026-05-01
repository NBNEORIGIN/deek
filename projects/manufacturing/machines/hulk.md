---
id: hulk
nickname: Hulk
brand: Piranha (UK reseller brand)
model_class: 1325 ATC class — 8' × 4', vacuum + ATC
oem_manufacturer: uncertain — likely Jinan, Shandong cluster
oem_candidates:
  - Jinan Queen CNC Machinery Co., Ltd (publicly claims original UK-agent Piranha design)
  - Jinan Jntech Machinery Ltd
  - other Jinan-area OEMs supplying Pure CNC / Magic CNC / Opus
purchase_history: bought second-hand from Opus CNC, Durham, ~April 2024
purchase_relationship: |
  Opus CNC Ltd — Units A-D Roeburn House,
  Mandale Business Park, Durham DH1 1TH (0191 386 5303)
niche: CNC routing — sheet goods, signage substrates
stream: cnc
status: live
year_of_manufacture: TBD (likely pre-2020 given Syntec rather than current Opus PRO controller)
year_acquired_by_nbne: 2024
ownership: owned outright (second-hand)
working_area_mm: ~1300 × 2500 (confirm exact — could be 1325 × 2500)
controller: Syntec (model TBD — most likely 6MB or 6MD)
controller_screen: 8" LCD
spindle: HSD ES 929A — Italian (Hi-Speed Drives, Pesaro), auto-tool-change. Confirmed 2026-05-01 by operator inspection (NOT a HQD as the dossier originally assumed)
spindle_max_rpm: 24000   # confirm — typical for HSD ES 929 series
servo_drives: Yaskawa (Sigma series typical for class). Confirmed 2026-05-01 by operator inspection (NOT Delta as originally assumed)
servo_motors: Yaskawa (Sigma series typical)
atc_mechanism: carousel — confirmed 2026-05-01 by operator inspection (NOT linear-rack)
atc_positions: 12 — confirmed 2026-05-01 by operator inspection
cam_software: VCarve Pro (Vectric) — confirmed 2026-05-01 as NBNE's CAM
toolholder_standard: ISO30 (HSD ES 929 standard; confirm)
collet_standard: ER32 (typical for ISO30 ATC class)
table: vacuum + extraction
extraction: yes (presence confirmed)
phase: 3-phase 380V (typical for class)
factory_warranty: expired (second-hand)
location:
serial:
purchased: 2024-04
technical_owner: Ivan
primary_user:
notes: |
  NBNE's primary CNC router and the production workhorse for sheet-
  based signage fabrication: ACM (Dibond, Aluclad), foamex/Foamboard,
  MDF, plywood, acrylic, foam, hardwoods. ATC + vacuum + 8'×4' bed.
  Avid (Avid Pro 8'×4') is NBNE's other CNC router; the Hulk is
  preferred for full-sheet jobs and anything requiring ATC, with
  Avid as a finer-work / ATC-not-required alternative.

  NOT used for: metal cutting (other than non-ferrous routing in
  limited circumstances), small precision parts (Avid is the better
  fit), or flexible materials. PVC routing forbidden — releases
  chlorine, corrodes the machine, hazardous to operators (same rule
  as the Beast).

  Common-noun risk: "Hulk" is everyday English; brand ("Piranha"),
  model class ("1325 ATC"), and the alias list disambiguate retrieval.

  Provenance is uncertain — the Piranha brand is shared across at
  least three UK resellers (Opus CNC, Magic CNC, Pure CNC) and was
  applied to imported Chinese CNCs from one or more OEMs in the
  Jinan, Shandong cluster. The original UK agent who designed the
  Piranha specification has died, so there is no single source of
  truth for "is this a Piranha QN2030 from Jinan Queen, or a
  different OEM?". See `hulk-provenance.md` (chunk_type='manual')
  for the full audit trail and the 12-item open-gaps list to close
  this out from the machine itself.
aliases:
  - hulk
  - the hulk
  - hulky                 # stale — old project doc used HULKY; canonical is Hulk
  - piranha
  - piranha cnc
  - piranha 1325
  - piranha atc
  - the big router
  - 8x4 router
  - atc cnc
  - syntec router         # commonly referred to by its controller
manuals_path: /opt/nbne/manuals/Hulk/
ratified: 2026-04-30
research_dossier: 2026-05-01 (Toby + chat-Claude — Layers 2/3/5/6 + Provenance ingested)
---

# Hulk — Piranha 1325 ATC CNC router

The Hulk is NBNE's primary CNC router. 8' × 4' bed, vacuum hold-down,
extraction, automatic tool change, HQD 9kW air-cooled ATC spindle on
ISO30 toolholders running ER32 collets, Syntec controller. Bought
second-hand from Opus CNC of Durham in spring 2024, owned outright,
factory warranty expired.

The Piranha brand is a **UK reseller mark, not an OEM**. Documentation
must be assembled from three sources rather than a single canonical
manual:

1. **Syntec controller** — well-documented across the woodworking-CNC
   industry; the 6 Series Mill Operation Manual + the 60WA Wood
   Operation Manual are the practical references.
2. **HQD 9kW ATC spindle** — commodity, well-documented; the
   GDL70-24Z spec sheet covers run-in, lubrication, air supply.
3. **Commodity components** — Delta servos, vacuum pumps, linear
   rails, ball screws are all standardised parts.

Plus tribal knowledge from Ivan and from prior owner experience.

## Manual coverage

Layer 2/3/5/6 + Provenance ingested 2026-05-01 from the chat-Claude
research dossier:
- `hulk-manuals-index.md` — catalogue + URLs for the Syntec, HQD, Delta, and Vectric reference docs (PDFs to be downloaded + uploaded separately)
- `hulk-procedures.md` — daily startup / shutdown / spindle warm-up + maintenance schedule + critical operating rules
- `hulk-supply-chain.md` — consumables + periodic-replacement parts + UK suppliers (Opus, Magic, Pure, Becker, Delta) + lead times
- `hulk-tips.md` — operator gotchas (controller, spindle, ATC, vacuum, materials, software/post-processor preservation)
- `hulk-provenance.md` — append-only OEM-identification audit trail

Searchable via `search_manuals(query=..., machine="Hulk")`.

## Maintenance log

_Per-event entries to be appended over time, dated. The hulk-tips
file is the dated append-only home for operator lessons; this is
for machine-specific maintenance events (bearing replacement,
controller parameter restore, ATC alignment, etc.)._

## Open gaps — to fill from inspection + Opus enquiry

1. Serial number — chassis plate, controller, AND spindle (capture all three)
2. Syntec controller exact model (6MB-E? 6MD? 60W-E?) — visible on bezel or system info screen
3. ~~Spindle confirmation — definitely HQD GDL70-24Z? Check spindle plate~~ **CLOSED 2026-05-01: HSD ES 929A (Italian premium spindle, NOT HQD).** See `hulk-provenance.md`.
4. ~~Servo brand confirmation — Delta? Yaskawa? Estun?~~ **CLOSED 2026-05-01: Yaskawa.**
5. ~~ATC type — linear rack vs carousel? How many positions?~~ **CLOSED 2026-05-01: carousel, 12 positions.**
6. Toolholder standard confirmed — ISO30 vs BT30 (HSD ES 929 typically ships ISO30 — confirm)
7. Vacuum pump make/model — Becker? Other? Capacity?
8. Year of manufacture — Opus CNC may know
9. Original purchaser of the second-hand machine before NBNE — one prior owner or several?
10. Working area exact dimensions — 1300×2500 vs 1325×2500 vs other?
11. **Existing post-processor location and content** — find, back up, ingest into Deek as raw text. HIGHEST PRIORITY: easy to lose, painful to rebuild.
12. **Existing parameter file backup** — does one exist? If not, extract one immediately. Same priority as #11; together these two are the data that, if lost, costs the most time and money to reconstruct.

Highest-priority items: **#11 (post-processor) and #12 (parameter backup)** before
anything else; **#1 (serials)** unlocks all warranty/parts conversations;
**#2 (Syntec model)** determines which manuals are canonical.

## Tribal knowledge

The dossier flags a high-priority Layer 6 harvest that hasn't happened
yet: **30 minutes with Ivan and a recorder**, capturing everything he
knows about the Hulk that isn't in any manual. That session is
genuinely more valuable than any of the official documentation, and
easier to lose if Ivan moves on. Once captured, append the transcript
to `hulk-tips.md` (or as a new dated entry) and re-run the upload.
