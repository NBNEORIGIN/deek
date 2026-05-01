---
id: roland
nickname: Roland
brand: Roland DG
manufacturer: Roland DG Corporation, Japan
distributor: Roland DG (UK) Ltd / authorised UK dealer
model: TrueVIS MG-300
model_class: 30-inch UV print-and-cut, roll-to-roll
product_family: TrueVIS MG series (sister model MG-640 is 64")
niche: UV-LED roll-to-roll print-and-cut, 30" wide
stream: printing
status: live-lease
ownership: LEASED via Siemens (bundled with Application Table; lease expires October 2026)
lease_partner_machine: Application Table (EWS)
year_acquired: TBD (likely 2023 given lease term)
print_width_max_mm: 762
print_speed_720x720_sqft_h: 120
print_heads: dual print head
ink_chemistry: Roland ECO-UV EUV5 (and EUVS shrink ink option)
ink_colours_available:
  - C, M, Y, K
  - W (white, high-opacity)
  - Gl (gloss varnish)
  - Or (orange, extended gamut)
  - Re (red, extended gamut)
  - Pr (primer, for adhesion-challenging substrates)
ink_configurations_supported:
  - Dual CMYK (highest speed)
  - CMYK + Or + Re + Wh + Gl (extended gamut + effects)
  - CMYK + Wh + Wh + Gl + Gl (double white + gloss)
  - CMYK + Wh + Wh + Gl + Pr (double white + gloss + primer)
ink_cartridge_sizes_ml: [220, 500]
nbne_ink_config: TBD (find which configuration is fitted)
print_head_temperature_control: yes (head heater for stable ink viscosity)
ink_clogging_prevention: yes (Roland's automatic anti-clog system)
software_bundled:
  - VersaWorks 7 (Roland RIP, current version)
  - Roland DG Connect (cloud monitoring)
  - Roland DG Connect mobile app (ink usage tracking)
optional_accessories:
  - take-up unit (TUC-30 / equivalent — for unattended long runs)
  - extension table ET-30 (for semi-rigid card stock up to 1mm)
  - tension bar TB-30 (for thin films 30 microns+ — note cannot be used with extension table)
substrate_compatibility:
  - vinyls (calendered, glossy, etched, frosted)
  - backlit films (glossy, semi-transparent)
  - clear static cling
  - holographic prism film
  - banner materials (fabric and PVC)
  - thin films (OPP, PET — with tension bar)
  - semi-rigid cardboard (with extension table)
  - paper, photobase
service_contract:
  provider: Roland DG (direct, not via lessor)
  approx_monthly_cost_gbp: 170
  tier_estimate: Silver (TBD — Bronze / Silver / Gold)
  scope: Roland MG-300 only (NOT EWS)
location:
serial:
purchased:
primary_user:
technical_owner:
notes: |
  NBNE's UV roll-to-roll print-and-cut machine. Sits between the
  flatbed UV printers (Rolf, Mao, Mutoh, Mimaki — rigid substrates)
  and the dye-sublimation Epson (textiles). Handles the FLEXIBLE
  substrate niche: vinyl, banners, films, backlit, decals, labels,
  packaging prototypes.

  Use cases:
    - Vehicle and shopfront vinyl with print
    - Window graphics (high-opacity white onto clear)
    - Backlit lightbox panels
    - Stickers, decals, labels at production volume
    - Short-run packaging prototypes
    - In-store promotional graphics
    - Long unattended runs (with optional take-up unit)

  NOT used for: rigid substrates (those go to flatbed UVs) or
  textile work (Epson SC-F500).

  COMMERCIAL CONTEXT — read before any lease/service decision:
  bundled with the EWS Application Table on a Siemens lease that
  expires October 2026 (~6 months away). Roland service contract
  (£170/month, Roland direct) is SEPARATE from the Siemens lease
  (£700/month total). October decision is Path A keep / Path B
  return-and-replace / Path C mixed (only if Siemens contract
  permits unbundling). See `roland-lease.md` for the full
  framework, decision-information checklist, and lease-end audit
  trail.

  Earlier internal docs called this machine a "vinyl cutter" — that
  was wrong; it's a print-AND-cut machine that does both in one
  pass. The cut function alone doesn't capture the value
  proposition.
aliases:
  - the roland
  - roland mg-300
  - mg-300
  - mg300
  - truevis
  - the uv print-cut
  - the 30-inch
  - roll printer
  - the print-cutter
manuals_path: /opt/nbne/manuals/Roland/
ratified: 2026-04-30
research_dossier: 2026-05-01 (Toby + chat-Claude — Layer 2/3/5/6 + Lease ingested)
---

# Roland — Roland DG TrueVIS MG-300 UV print-and-cut

The Roland is NBNE's TrueVIS MG-300 — a 30"-wide UV-LED roll-to-roll
print-and-cut machine made by Roland DG Corporation, Japan. Dual
print heads, EUV5 ECO-UV ink chemistry with extended-gamut and
effect inks (orange, red, white, gloss varnish, primer), VersaWorks
7 RIP, Roland DG Connect cloud monitoring, optional take-up unit
for unattended long runs.

The MG-300's value proposition is integrated print + cut in one
machine: design with cut paths in vector software → RIP in
VersaWorks → print phase → real-time UV cure → reverse + cut on
crop marks → output. Sheet perforated cut handles unattended
long sticker/decal runs without cutting through the backing.

LEASED via Siemens, **bundled with the Application Table (EWS)** on
the same Siemens contract. Lease expires October 2026 — that
decision is genuinely material (~£700/month bundled + £170/month
Roland service). See `roland-lease.md` for the full lease + service
contract context and the October decision framework.

## Manual coverage

Layer 2/3/5/6 + Lease ingested 2026-05-01 from the chat-Claude
dossier:
- `roland-manuals-index.md` — Roland DG official documentation
  (publicly accessible, unlike Mutoh's gated service manuals),
  ECO-UV ink SDS files to source, VersaWorks 7 docs, media SDS
- `roland-procedures.md` — daily start-up + nozzle check + ink
  level monitoring + print-and-cut workflow + maintenance schedule
- `roland-lease.md` — Siemens bundled lease + Roland Care service
  contract + October 2026 decision framework (Path A / B / C) +
  decision-information checklist. **Commercially sensitive.**
- `roland-supply-chain.md` — EUV5 ink, cleaning consumables,
  service-contract-excluded consumables (cap tops, wipers, mist
  filters), UK suppliers, lead times
- `roland-tips.md` — cut registration, blade angle, force drift,
  VersaWorks profile preservation, Roland DG Connect uses

EWS Application Table coverage in `application_table.md` card +
`Application Table/application-table-procedures.md` etc. The
Application Table is on the same lease — see the lease file there
for the EWS-side implications.

Searchable via `search_manuals(query=..., machine="Roland")`.

## Maintenance log

_Per-event entries to be appended over time, dated. The
roland-tips file is the dated append-only home for operator
lessons; this is for machine-specific maintenance events
(head replacement, blade replacement, service visits, jams)._

## Open gaps — to fill before October 2026 lease decision

### Critical (lease-decision blockers)

1. **EWS manufacturer + model** — the bundled Application Table's
   provenance is uncertain. Without this, valuation, replacement
   options, and "is this worth keeping" are all guesses. Read
   the rear plate, the Siemens lease asset schedule, or both.
2. **Lease end-of-term options and fees** — Siemens contract.
   Title transfer fee (lump sum or peppercorn?), return
   conditions, extension options, whether unbundling (Path C) is
   permitted.
3. **Roland service contract tier and renewal date** — confirm
   Bronze vs Silver vs Gold; capture the renewal mechanism
   (auto-renew with notice period?).
4. **Original supplying dealer** — for both Roland and EWS. Often
   the same dealer; relevant for second-hand valuations and
   replacement quotes.

### Important (operational)

5. **Roland MG-300 serial number** (machine plate)
6. **Year of acquisition / install date**
7. **Current ink configuration** — which of the supported configs
   is fitted? Affects ink ordering and replacement planning
8. **Print head hours / count** (visible via Roland DG Connect or
   the machine's service info screen)
9. **Last service visit date** and what was done
10. **Any logged issues** since acquisition
11. **VersaWorks 7 RIP profiles for NBNE substrates** — same
    lesson as Mutoh VerteLith profiles and Hulk post-processor.
    **URGENT — find them and back them up to Drive + ingest into
    Deek.**
12. **Primary user / technical owner** — who runs day-to-day, who
    calls Roland DG when something's wrong

### Highest priority

**#1 (EWS identity), #2 (lease terms), #3 (service tier)** are
the foundation for the October decision. Pull this week.
**#11 (VersaWorks profile backup)** is the one-shot data-loss risk.

## Tribal knowledge

Same advice as the Hulk and Mutoh: 30 minutes with whoever runs
the Roland day-to-day (TBD), recorded, transcribed, ingested. The
print-and-cut workflow above is generic; what NBNE has learned
about substrate-specific cut force, custom profiles, and recurring
small problems is the layer that's irreplaceable.
