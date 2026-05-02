---
id: epson
nickname: Epson
brand: Epson
manufacturer: Seiko Epson Corporation, Japan
distributor: Epson UK Ltd
model: SureColor SC-F500
product_code: C11CJ17301A0 (UK)
sister_models:
  - SC-F501 (identical except fluorescent yellow + magenta inks)
  - SC-F530 / F560 / F570 / F571 (US/RoW market variants — same chassis lineage)
launch_date: 2020 (Epson's first 24" dye-sub printer)
niche: 24" desktop dye-sublimation printer for soft-goods and coated hard substrates
stream: printing (sublimation)
status: live
ownership: TBD — confirm owned outright vs leased
year_acquired: TBD
print_width_max_mm: 610  # 24"
print_resolution_max_dpi: 2400 × 1200
print_head: Epson PrecisionCore MicroTFP, 4-channel
nozzles: 3200 total (800 per channel × 4 channels)
nozzle_verification: yes (built-in)
ink_chemistry: Epson UltraChrome DS dye sublimation
ink_series: T49N (F500 — non-fluorescent) / T49P (F501 — fluorescent)
ink_colours: CMYK only (4-colour)
ink_supply: refillable bottles, 140 ml
refill_during_print: yes (one of the F500's selling features)
maintenance_box: C13S210057 (Epson genuine, ~£35-40 UK)
maintenance_box_shared_with: SC-T2100 / T3100 / T5100 plotters + SC-F501
media_modes: auto-switch between cut sheet (A3, A4) and roll
roll_width_supported_mm: 610
paper_thickness_supported_mm: 0.05 - 0.21
paper_path: rear roll feed + manual cut-sheet feed
auto_cutter: yes (replaceable cutter blade)
display: 4.3" colour touchscreen (glove-operable)
connectivity: USB 3.0, Ethernet, Wi-Fi
software_bundled:
  - Epson Edge Print (RIP, supplied)
  - Epson printer driver (Windows + macOS)
  - Epson Accounting Tool (cost tracking — useful, often forgotten)
  - EpsonNet Config (network)
  - Web Config (browser-based admin)
operating_systems: Windows 7/8/8.1/10 (and 11) + macOS 10.11+
power_consumption_print_w: 22
weight_kg: 29
dimensions_mm: 970 × 811 × 245 (W × D × H)
nbne_variant: TBD — F500 (T49N) or F501 (T49P fluorescent)? Touchscreen system info or ink bottle labels confirm
heat_press_partner: TBD — see "Sublimation is a workflow, not a machine" note below
factory_warranty: TBD
location:
serial:
purchased:
primary_user:
technical_owner:
supplier_likely: YPS (Your Print Specialists, Newcastle) — third NBNE machine likely from YPS alongside Roland MG-300 and EWS Application Table. See supply-chain file for the supplier-concentration insight.
notes: |
  NBNE's dye-sublimation printer. Sits in a completely different
  substrate niche from any other NBNE printer: where Rolf, Mao,
  Mutoh, Mimaki, and Roland all handle rigid or vinyl substrates
  with UV-cured ink, the Epson handles POLYESTER and POLYMER-COATED
  items via a dye-paper-and-heat-press workflow.

  Use cases:
    - Personalised soft goods (T-shirts, hoodies, cushions, tote
      bags, polyester fabric panels)
    - Coated hard substrates (mugs, phone cases, mouse mats,
      sublimation-coated MDF, coated metal — ChromaLuxe etc.,
      coated tiles)
    - Soft signage / polyester banner work (short runs)
    - Small-format awards and recognition products
    - Test prints / proofing for sublimation-coated signage

  NOT used for: cotton (doesn't accept sublimation dye, hard
  chemistry constraint), uncoated hard substrates, vinyl, paper-
  based promotional items, anything where UV-print or vinyl-print
  is the right answer.

  SUBLIMATION IS A WORKFLOW, NOT A MACHINE. The SC-F500 produces a
  printed dye-transfer paper. The heat press converts that paper
  into a finished product (typically 180-200°C, 30-60 sec, with
  pressure). WITHOUT THE HEAT PRESS, THE EPSON PRODUCES NOTHING
  USEFUL. The heat press deserves equal billing in NBNE's machine
  registry. Open gap #6 — until the press(es) are identified by
  make / model / size, the heat-press content lives in
  `epson-heat-press.md` rather than its own machine record. When
  identified, split out into its own canonical-list nickname.

  Common-noun risk: "Epson" is a brand. "Sublimation" is generic.
  The model code SC-F500 disambiguates retrieval.

  PRINT HEAD ECONOMICS DIFFER FROM MUTOH/ROLAND. New SC-F500 in
  the UK is ~£3,000-£4,000 — about an order of magnitude below the
  Roland or Mutoh. Print-head replacement is £800-£1,500 via Epson
  authorised service: a meaningful fraction of a new machine's
  cost. For this class of printer, "fix vs replace" is a real
  decision when the head dies, not just "fix at any cost".
aliases:
  - the epson
  - epson sc-f500
  - sc-f500
  - scf500
  - surecolor
  - f500
  - sub printer
  - the sublimation printer
  - dye-sub
  - the small printer
manuals_path: /opt/nbne/manuals/Epson/
ratified: 2026-04-30
research_dossier: 2026-05-02 (Toby + chat-Claude — Layer 2/3/5/6 + Heat Press + Substrates ingested)
---

# Epson — SureColor SC-F500 dye-sublimation printer

The Epson is a 24" desktop dye-sublimation printer made by Seiko
Epson Corporation, Japan. CMYK only (no white, no varnish — that's
not how sublimation works), refillable 140ml ink bottles (T49N
non-fluorescent series), Epson Edge Print RIP, PrecisionCore
MicroTFP head with 3200 nozzles, auto-switch roll vs cut sheet,
maintenance-box consumable, full touchscreen control.

Sublimation is a TWO-step workflow: print onto transfer paper →
heat-press against polyester or polymer-coated substrate. The dye
sublimates from solid to gas, penetrates the substrate, re-
solidifies as part of it. Without the heat press, the SC-F500
produces nothing useful. Open gap #6 — the specific heat press
NBNE has needs identifying.

Substrate constraint is severe: polyester or polymer-coated only.
Cotton doesn't work. Acrylic doesn't work. This is hard chemistry,
not a recommendation. Customer education on substrate suitability
is part of operator skill.

## Manual coverage

Layer 2/3/5/6 + Heat Press + Substrates ingested 2026-05-02 from
the chat-Claude dossier:
- `epson-manuals-index.md` — Epson EU PDF user guide URL +
  Edge Print + driver docs + DS Transfer paper specs +
  community resources
- `epson-procedures.md` — daily nozzle check, head cleaning
  escalation, ink refill workflow, maintenance box management,
  cutter blade care, weekly + monthly maintenance, critical
  operating rules
- `epson-heat-press.md` — heat-press identification gap, generic
  heat-press procedures (pre-press setup, pressing workflow, peel
  timing), critical heat-press operating rules. Split into its
  own machine record when NBNE's specific press(es) are known.
- `epson-supply-chain.md` — T49N inks, maintenance box, cutter
  blade, sublimation paper, UK suppliers. Includes the YPS
  supplier-concentration insight (third NBNE machine likely
  from same dealer as Roland and EWS).
- `epson-substrates.md` — substrate supplier directory (Xpres,
  ChromaLuxe, Sublimation Solutions UK, etc.) + per-substrate
  recipe library (append-only entries dated; time / temp /
  pressure / ICC profile / supplier batch).
- `epson-tips.md` — sublimation-specific gotchas (washed-out
  paper, mirror image, ICC profiles, polyester content,
  coating quality), printer-specific (auto-switch reliability,
  Wi-Fi attack surface, Accounting Tool), heat press tips,
  workflow + safety.

Searchable via `search_manuals(query=..., machine="Epson")`.

## Maintenance log

_Per-event entries to be appended over time, dated. Cleaning
cycles, head replacements, maintenance box changes, cutter blade
swaps all live here. Heat press service events live alongside
once that machine is identified and split out._

## Open gaps — prioritised

### Critical

1. **Heat press identification** — make, model, size, age of
   each heat press NBNE has. WITHOUT THIS THE WORKFLOW
   DOCUMENTATION IS HALF-COMPLETE. Could be flat platen
   (typical 38×38, 40×50, 50×60 cm), mug press, hat / cap press,
   tile / 3D vacuum press, or several of these.
2. **F500 vs F501 variant** — touchscreen system info or ink
   bottle labels (T49N for F500 / T49P for F501). Affects ink
   ordering and colour-saturation expectations.

### Important (operational + commercial)

3. Serial number — rear of printer
4. Year of acquisition / install date
5. Original supplying dealer — likely YPS given the EWS / Roland
   connection. If YPS is the common thread across three NBNE
   machines, that's a significant supplier relationship to
   acknowledge.
6. Ownership — owned outright or on a finance plan?
7. Heat press service history (once identified)
8. Print head hours / printer usage statistics — visible in Web
   Config or system info screen
9. Last full nozzle check / cleaning cycle date
10. **Edge Print version + ICC profile library location** — same
    drill as VerteLith / VersaWorks / Hulk post-processor.
    **URGENT** — single-point-of-failure on workshop PC. Find,
    back up, ingest into Deek.
11. Substrate supplier directory — who supplies NBNE's mug
    blanks, phone cases, polyester garments, MDF boards? Feeds
    `epson-substrates.md`.
12. Primary user for sublimation work + technical owner.
13. Workshop environment — temperature, humidity, dust exposure.
    Sublimation paper is hygroscopic; storage matters.

### Highest priority

**#1 (heat press identification)** unblocks the workflow side.
**#10 (Edge Print + ICC profile backup)** is the one-shot data-
loss risk. **#11 (substrate suppliers)** is the part of the
sublimation supply chain most often undocumented.

## Tribal knowledge

Same advice as the other machines: 30 min with whoever runs the
Epson day-to-day (TBD), recorded, transcribed, ingested. The
sublimation-specific knowledge that doesn't exist anywhere else:
substrate-specific recipes, supplier quirks, heat-press
calibration drift, customer-education scripts on polyester
content. Most valuable Layer 6 entries to capture early.
