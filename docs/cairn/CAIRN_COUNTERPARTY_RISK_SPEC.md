# CAIRN_COUNTERPARTY_RISK_SPEC.md
**Cairn Service Specification: Counterparty Risk & Terms Engine**
Version: 0.2
Date: 14 April 2026
Owner: Toby Fletcher
Status: Reviewed, ready for Phase 0 implementation

---

## 1. Purpose

This is the specification for a Cairn service that maintains
behavioural risk profiles of NBNE's B2B counterparties and converts
those profiles into recommended commercial terms and drafting posture.

The service is consumed by the PM sub-agent (CAIRN_PM_AGENT_SPEC.md)
and, in time, by other sub-agents (Bookkeeper, Administrator). It
writes to the CRM and reads from accumulated interaction history
(email, meeting notes, project records, payment history).

**This is not a screening system.** It does not refuse business.
It adjusts the terms on which business is offered, so that the
commercial structure of an engagement is calibrated to the observed
risk of the counterparty. Refusal is reserved for a small, specific
set of behavioural red lines that exist independently of the risk
score.

---

## 2. Core principle

NBNE's existing counterparty judgement — exercised informally and
consistently by Toby and Jo over years — is to engage with a wide
range of clients, but to vary the protective scaffolding around each
engagement based on observed signals. Low-risk clients get standard
terms, fast service, and warm professionalism. Higher-risk clients
get higher deposits, written-only communication, joint-director
engagement, and counterintuitively warmer drafting (which keeps
high-risk individuals talking, documenting, and predictable rather
than triggering retaliation patterns).

This service formalises that judgement. It is not a substitute for
human discretion — Toby and Jo retain final say on every engagement.
It is a memory aid and a consistency aid, ensuring that signals
observed in October are still weighted in March, and that signals
observed by Toby are visible to Jo (and vice versa).

The framing throughout is **behavioural and operational**, not
clinical. The service does not diagnose personality disorders, does
not store clinical labels, and does not produce assessments that
could constitute defamation if disclosed. Its outputs are
evidence-linked observations and risk-adjusted commercial
recommendations, of the same character as a credit reference or
an insurance underwriting decision.

---

## 3. Definitions

### Counterparty
Any external party with whom NBNE has, has had, or is considering a
B2B engagement. Includes prospects, active clients, suppliers, and
former clients. One counterparty may comprise multiple individuals
(e.g. a company with multiple contacts) — the service profiles at
both the individual and entity level.

### Signal
A specific, observable behaviour exhibited by a counterparty,
recorded with timestamp, source (email/meeting/payment record), and
evidence pointer. Signals are facts; scores are inferences from
those facts.

### Risk score
A number between 0.0 and 1.0 representing the weighted accumulation
of signals observed over time, decayed for recency. Displayed to
humans as a 5×5 matrix cell (signal-strength × business-exposure)
with a colour band (green/yellow/amber/red).

### Terms profile
The output of the risk service: a structured recommendation for
deposit %, payment terms, communication channel, drafting posture,
and other operational dials, derived from the risk score and the
specifics of the engagement.

### Hard-no register
A separate, short list of behavioural red lines that trigger refusal
of engagement regardless of risk score. Independent of the scoring
mechanism.

### Subject codename
An opaque identifier used to refer to historical counterparties
within the corpus while preserving anonymity. Not derived from real
names or initials.

---

## 4. The risk model

### Two axes

**Y-axis — Behavioural signal strength (1–5):**
Weighted sum of observed signals from the taxonomy (Section 5),
decayed for recency. Recent signals weight more than historical
ones. A 1 indicates baseline professional behaviour; a 5 indicates
strong accumulation of high-weight signals across multiple
categories.

**X-axis — Business exposure (1–5):**
A function of the engagement structure:
- Project value
- Project complexity (one-off vs multi-stage vs ongoing contract)
- Project duration
- Reversibility (can NBNE walk away cleanly mid-project, or are
  assets/IP/equipment entangled)
- NBNE-side dependencies (e.g. is NBNE relying on this engagement
  for cashflow that month)

A 1 indicates a small, fast, reversible job. A 5 indicates a large,
long, entangled engagement with limited exit options.

### The 5×5 matrix

```
Business exposure →
                1       2       3       4       5
Signal    1   green   green  yellow  yellow  yellow
strength  2   green  yellow  yellow  amber   amber
↓         3  yellow  yellow  amber   amber    red
          4  yellow  amber   amber    red     red
          5   amber   amber   red     red     red
```

### Why both axes

A small, fully prepaid, fast-turnaround job from a high-signal
counterparty is yellow, not red — exposure is low, the deal is
structurally protected, and engaging is rational. A large, complex,
multi-month engagement from a low-signal counterparty is yellow, not
green — even good clients can produce bad outcomes when the exposure
is high enough. The matrix prevents both over-restriction (refusing
small jobs from difficult people) and under-protection (assuming
large jobs from nice people are low-risk).

### Internal vs displayed representation

Internally, the model holds continuous values per signal dimension
with confidence intervals. The 5×5 matrix is the human display layer;
the underlying state is richer. Posture rules and terms recommendations
derive from the continuous representation, not the discretised cell.

---

## 5. Signal taxonomy (v1)

Each signal has: a name, a definition, a default weight (1–3), and
detection guidance (programmatic vs human-annotated). The taxonomy is
expected to evolve as the historical corpus is processed.

### Communication signals

| Signal | Definition | Weight | Detection |
|---|---|---|---|
| Channel preference: in-person/verbal over written | Ratio of substantive in-person/phone interactions to substantive written interactions exceeds 2:1 over rolling 30 days | 2 | Programmatic from interaction log |
| Terse written / verbose verbal | Mean word count of written exchanges <30 while phone/meeting transcripts run long | 2 | Programmatic |
| Evasive on direct factual questions | Direct factual question (price, date, scope, documentation) met with deflection, generalisation, or topic change | 3 | Annotated by Cairn LLM with evidence quote |
| Generalised complaints without specifics | Complaint registered (cost, quality, expectations) without specific itemisation when asked | 2 | Annotated by Cairn LLM |
| Verbal commitment retraction in writing | Specific commitment made verbally is softened, re-scoped, or denied when reflected in writing | 3 | Annotated; high-confidence required |

### Self-presentation signals

| Signal | Definition | Weight | Detection |
|---|---|---|---|
| Grandiose / exceptional claims | Unverifiable claims of unique status, market firsts, exclusive access, special licences | 2 | Annotated by Cairn LLM |
| Status name-dropping | Frequent reference to important contacts, organisations, prior achievements as authority | 1 | Annotated |
| Charm density | High frequency of warm/flattering language disproportionate to relationship depth | 1 | Annotated |
| Self-as-victim narrative | Past failures attributed exclusively to others; pattern across multiple referenced episodes | 2 | Annotated |

### Commercial behaviour signals

| Signal | Definition | Weight | Detection |
|---|---|---|---|
| Emotional/promissory bargaining | Negotiation conducted via promises of future volume or cash rather than structured terms | 2 | Annotated |
| Resistance to written terms | Reluctance, deflection, or active resistance when presented with formal terms | 3 | Annotated |
| Scope creep attempted verbally | Attempts to expand or alter scope through casual/verbal channels rather than formal variation | 3 | Annotated |
| Payment friction | Late payment, partial payment, payment disputed, payment delayed past terms (excludes legitimate disputes) | 3 | Programmatic from accounting integration |
| Prior business failures / pattern | Documented history of failed ventures, especially with other suppliers left exposed | 2 | Human-annotated, evidence-linked |

### Structural risk signals

| Signal | Definition | Weight | Detection |
|---|---|---|---|
| Pressure to bypass NBNE procedure | Requests to proceed without written authorisation, skip standard checks, accelerate beyond capacity | 3 | Annotated |
| Asset entanglement attempt | Proposes arrangements that entangle NBNE equipment, IP, or brand in their venture without commercial separation | 3 | Annotated |
| Regulatory/legal grey area | Engagement involves IP, regulatory, or legal questions where counterparty asserts authority NBNE cannot independently verify | 3 | Annotated |

### Score weighting note
A counterparty exhibiting 3 weight-3 signals plus 2 weight-2 signals
across multiple categories sits firmly in amber territory. A
counterparty exhibiting 6 weight-1 signals only does not — accumulation
within a single low-weight category is less predictive than signal
diversity across categories.

---

## 6. Score derivation, decay, and update

### Initial score
On first contact: 0.2 (low-yellow on a five-point scale). This is
not "neutral" — it reflects the base rate that all counterparties
warrant some baseline written discipline.

### Update on new signal
When a new signal is recorded:
1. Add weighted contribution to the relevant signal dimension
2. Recalculate the dimension score
3. Recalculate aggregate signal-strength axis value
4. Update confidence interval based on signal volume
5. Map to matrix cell, derive band, derive terms profile
6. Log the update with reasoning, evidence pointer, and prior score

### Decay
- Signals decay with a 12-month half-life by default
- Some signals do not decay (e.g. confirmed regulatory red lines, asset entanglement attempts) — these remain at full weight indefinitely
- Decay is applied during nightly recalculation, not in the hot path

### Demotion (score reduction)
A counterparty's score should reduce when:
- Two consecutive invoices paid on time and without dispute
- Written commitments honoured for 60 consecutive days
- No new signals accumulated in 90 days
- Project completed cleanly with positive net financial outcome

This is critical. Without demotion, the system becomes a one-way
ratchet that progressively flags everyone as risky. Demotion is what
makes the system honest.

### Promotion to red
Automatic promotion to red occurs when:
- Two weight-3 signals recorded in any 14-day window
- Any single hard-no register trigger event
- Payment friction signal during active engagement
- Promotion always notifies Toby and Jo in the briefing

### Confidence
Score confidence is reported separately from the score itself.
Low confidence means few interactions, ambiguous signals, or
inconsistent observation. High confidence means many interactions,
clear signals, multiple evidence points. The PM agent should
escalate (rather than act on) low-confidence high-score situations.

---

## 7. Terms profile mapping

The terms profile is the consumable output. Sub-agents and human
users see this, not the raw score.

### Default terms (green band)
```
Deposit:           25% (NBNE standard)
Payment terms:     30 days (NBNE standard)
Communication:     Mixed — whichever suits the client
Drafting posture:  Professional, efficient, warm but transactional
Response speed:    Same-day where possible
Channel guidance:  Client-led
Margin floor:      25% (NBNE standard)
Equipment release: Standard terms
Director engagement: Single-director routine OK
```

### Yellow band
```
Deposit:           33%
Payment terms:     14 days
Communication:     Written-confirmation discipline (verbal followed
                   by "just to confirm in writing…")
Drafting posture:  Professional, slightly more specific on commitments
Response speed:    Same-day on substantive items, considered on
                   commitments
Channel guidance:  Soft preference for written
Margin floor:      30%
Equipment release: Standard terms with explicit return clause
Director engagement: Single-director routine OK; joint-director on
                     contract sign-off
```

### Amber band
```
Deposit:           50%
Payment terms:     Prepayment until 2 invoices clean, then 7 days
Communication:     Written-only on substantive items; verbal
                   followed by written summary same day
Drafting posture:  Counterintuitively warm; deliberately vague on
                   commitments not previously made; specific on
                   protective terms
Response speed:    Considered (24–48hr); no rapid commitments
Channel guidance:  Push toward written
Margin floor:      35%
Equipment release: Separate lease agreement, no exception
Director engagement: Joint-director on all substantive items
Documentation:     Every interaction logged; commitments tracked
                   against delivery
Variation orders:  Written, signed both parties, no exception
```

### Red band
```
Deposit:           Prepayment-only, no exception
Payment terms:     N/A (prepayment)
Communication:     Written-only, period
Drafting posture:  Strategically warm to maintain engagement;
                   non-committal on anything new
Response speed:    Considered (48hr+); never reactive
Channel guidance:  Written-only enforced
Margin floor:      40%
Equipment release: Separate lease, both directors signed,
                   independent legal review
Director engagement: Joint-director on every interaction
Documentation:     Full evidence log; preparation for potential
                   dispute from outset
Variation orders:  Written, signed, with cooling-off period
Disengagement:     Active disengagement playbook (Section 9) applies
```

### Posture modulation rationale
The drafting posture is not just "be more careful." It is calibrated
to the observed counterparty behaviour. High-signal counterparties
respond poorly to coldness or formality (these are perceived as
slights and can trigger escalation). They respond well to warmth,
which keeps engagement open, documentation flowing, and disengagement
graceful when needed. Low-signal counterparties, by contrast, are
not served by performed warmth — they want efficiency and clarity.
The posture dial is therefore not monotonic with risk; it is shaped
by what each band of counterparty actually responds to.

---

## 8. Hard-no register

The hard-no register is a separate, short list of behavioural
triggers that result in refusal of engagement regardless of any
score. It is independent of the risk model and not derived from it.

Triggers are **specific, observable, evidence-linked events** — not
inferences, not patterns, not gestalt impressions.

### Current hard-no triggers (v1)

1. **Request to act outside legal/regulatory boundaries.** Counterparty
   asks NBNE to produce, supply, install, or facilitate something
   that requires authorisation NBNE cannot independently verify and
   the counterparty cannot produce on request.

2. **Verified active deception.** Counterparty makes a factual claim
   that is independently verified to be false, and the claim was
   material to NBNE's engagement decision.

3. **Procedure-bypass after explicit notice.** Counterparty attempts
   to proceed without written authorisation, signed terms, or other
   procedural step *after* being explicitly told the step is
   required.

4. **Asset entanglement without commercial separation.** Counterparty
   proposes an arrangement that entangles NBNE equipment, IP, brand,
   or capability in their venture without a separate, executable
   commercial agreement protecting NBNE's interests, *and* refuses to
   accept such an agreement when offered.

5. **Prior unfavourably-resolved dispute with NBNE.** Counterparty
   has previously consumed substantial NBNE director time in a
   dispute that was resolved in NBNE's favour (financially, legally,
   or regulatorily). This is a hard-no on re-engagement, not on the
   original engagement.

6. **Confirmed regulatory or legal action against NBNE caused by
   counterparty.** Counterparty has caused NBNE to be subject to
   regulatory enquiry, legal action, or industry-body complaint,
   regardless of outcome.

### Hard-no governance
- Adding a trigger to the register requires both directors' agreement
- Triggering a hard-no requires evidence in the audit log
- Hard-no decisions are recorded with reasoning and notification to
  both directors
- Counterparties who hit a hard-no trigger may be reviewed at
  director discretion after 24 months, but reinstatement is not
  automatic

### Hard-no does not mean hostile
A hard-no is operationalised through structured non-engagement
(declining to quote, slow-walking enquiries, pricing prohibitively)
rather than confrontation. The disengagement playbook in Section 9
applies.

---

## 9. Disengagement playbook

The hardest problem this service addresses is not detection but
**graceful disengagement**. The detection problem is solved largely
by the existing instincts of Toby and Jo. The disengagement problem
is where consistency is hardest to maintain under pressure (charm,
fatigue, partial commercial entanglement) and where Cairn earns its
keep.

### Pre-quote disengagement (amber/red, no formal engagement yet)
- Quote at the band-appropriate margin floor (frequently above
  competitor pricing — counterparty self-selects away)
- Re-scope the request toward services NBNE provides less of (or
  doesn't provide), reducing apparent fit
- Slow response cadence (within professional norms — 48hr rather
  than same-day)
- Never a flat decline unless a hard-no trigger applies

### Post-quote disengagement (terms accepted but not yet started)
- All commitments require written confirmation
- Lean on protective terms (deposit, lease, joint-director sign-off)
  that may themselves cause the counterparty to withdraw
- If the counterparty pushes back on protective terms, hold them —
  insistence on terms is itself a disengagement mechanism

### Mid-project disengagement (in flight, signal escalation occurs)
- Activate contractual protections already in place (this is why
  amber/red engagement scaffolding matters from day one)
- Slow new commitments; honour existing ones meticulously
- Document everything in writing; create the paper trail that
  supports clean exit if needed
- Joint-director engagement on every interaction
- If wind-down becomes necessary, use the contractual mechanisms
  (notice periods, scope completion definitions) that the band-
  appropriate terms profile already established

### Post-engagement (project complete, counterparty has hit hard-no
or significant amber escalation)
- Counterparty marked as "not for re-engagement" in CRM
- Future enquiries from counterparty trigger automatic notification
  to both directors before any response
- Standard professional response if contact is made, with
  non-committal language
- Service does not facilitate any active negative communication
  (no warning of others, no public commentary, no professional-body
  reports unless legally required)

---

## 10. CRM schema additions

The CRM module requires the following additions to support this
service. These are the minimum schema changes for v1.

### Tech stack note
The CRM is a Next.js 16 application using **Prisma ORM** with
**PostgreSQL**. All schema changes below must be implemented as
Prisma model additions in `D:\crm\prisma\schema.prisma` and applied
via `npx prisma migrate dev`. Do not write raw SQL migrations.
The CRM is deployed via Docker on Hetzner (178.104.1.152) at
`/opt/crm`; production migrations use `npx prisma migrate deploy`.

### `counterparty_risk` table (new)
```
counterparty_id        FK to existing CRM counterparty record
codename               Opaque subject codename (auto-generated)
signal_strength_score  Continuous, 1.0–5.0
business_exposure_score Continuous, 1.0–5.0 (per-engagement, see below)
matrix_cell            Derived (e.g. "3-4")
band                   Derived: green/yellow/amber/red
confidence             Continuous, 0.0–1.0
hard_no_flag           Boolean
hard_no_reason         FK to hard_no_event if flagged
last_recalculated      Timestamp
```

### `signal_event` table (new)
```
id                     PK
counterparty_id        FK
signal_type            Enum from taxonomy (Section 5)
weight                 Effective weight at time of recording
recorded_at            Timestamp
source_type            email / meeting / payment / annotation / other
source_pointer         URI to source evidence
evidence_quote         Verbatim quote or summary supporting the signal
recorded_by            human user ID or "cairn_observer"
confidence             0.0–1.0
notes                  Free text
```

### `hard_no_event` table (new)
```
id                     PK
counterparty_id        FK
trigger_type           Enum from hard-no register (Section 8)
recorded_at            Timestamp
recorded_by            User ID (must be a director)
co_signed_by           User ID (second director)
evidence_pointer       URI to evidence
reasoning              Free text
review_due_at          Timestamp (default +24 months)
```

### `terms_profile` view (derived, not stored)
Computed on demand from `counterparty_risk` and engagement-specific
exposure inputs. Returned to consumers (PM agent, Bookkeeper, human
UI) as a structured object per Section 7.

### `risk_recalculation_log` table (new)
Audit trail of every score change, including: prior score, new score,
trigger event, reasoning. Append-only. This is both the audit record
and the training data for future model refinement.

### Modifications to existing `project` table
```
business_exposure_score   Computed per-project on creation/update
                          (project value, complexity, duration,
                          reversibility)
risk_band_at_quote        Snapshot of counterparty band at quote time
risk_band_at_invoice      Snapshot of counterparty band at first invoice
terms_applied             JSON snapshot of terms profile at engagement
                          start (for retrospective audit)
```

---

## 11. Anonymisation protocol (historical corpus)

The historical corpus — past counterparties whose behaviour and
outcomes train the model — must be anonymised. This protects
individuals named in the corpus, reduces NBNE's data-protection
obligations relative to the corpus, and allows the corpus to be
shared with future Cairn instances or backed up without elevated
privacy risk.

### Subject codenames
- Generated automatically as opaque tokens (random words from a
  curated list, never derived from real names or initials)
- Stable within Cairn — the same counterparty always has the same
  codename
- Mapped to real identities in a single sealed key file held outside
  Cairn, encrypted, accessible only to directors

### Corpus record structure
```
Subject_<CODENAME>
First contact: T+0 (relative timeline)
Sector: <generalised sector tag>
Engagement type: <generalised engagement type>
Director-time invested: <hours>
Outcome: <terminated / completed-clean / completed-disputed /
          regulatory-exposure / financial-loss / etc>
Net financial outcome: <amount or estimate, anonymised>
Signal log: <structured signal entries with timestamps relative to T+0>
Lessons: <free text observations relevant to model calibration>
```

### Director key protocol
- Single sealed mapping file: codename → real identity
- Encrypted at rest with director-only key
- Stored outside Cairn (not in any database Cairn has access to)
- Required only for legitimate operational need (e.g. confirming a
  re-engagement attempt from a previously-flagged counterparty)
- Access logged

### Re-identification rules
The mapping key is never accessed by Cairn. Sub-agents (PM,
Bookkeeper) operate purely on the anonymised corpus and the live
counterparty records. Only directors can re-identify, and only when
necessary.

---

## 12. Access controls

This data is the most sensitive in Cairn. Access controls are
correspondingly tight.

### Director access (Toby, Jo)
- Full read/write on all risk profiles, signal events, hard-no
  register, recalculation log
- Sole authority to record hard-no triggers (with co-signing)
- Sole authority to manually adjust scores (with reasoning logged)
- Sole authority to access the codename mapping key

### Sub-agent access (PM, Bookkeeper, Administrator)
- Read access to the *terms profile* output for a given counterparty
- Read access to the band only (not the underlying signals or score)
- No write access of any kind
- No access to evidence quotes or signal logs

### Other users (staff, future Phloe tenants, etc.)
- No access to any part of this service
- The service does not exist for these users; counterparty risk
  data does not appear in any UI they see

### Audit
- Every read by a sub-agent is logged
- Every score change is logged with attribution
- Every codename mapping access is logged
- Logs are append-only and held for 7 years

### Phloe boundary
This service is **internal-use only** and does not propagate to
Phloe. Phloe tenants have their own counterparty management within
their own data; NBNE's risk model is never exposed via any tenant-
facing surface.

---

## 13. Integration with PM sub-agent

Per CAIRN_PM_AGENT_SPEC.md, the PM agent now consumes this service
on three occasions:

### On new enquiry (LEAD creation)
1. Identity resolution (per PM spec) determines the counterparty
2. PM agent queries this service for current risk band and terms
   profile
3. If the counterparty is unknown: initial score 0.2 (low-yellow),
   confidence 0.0 — PM agent flags as "new relationship" in briefing
4. If the counterparty is known: PM agent uses the existing terms
   profile to shape the response
5. Hard-no flag check: if flagged, PM agent does not draft; instead
   surfaces to directors for decision

### On every outbound draft
1. PM agent retrieves current terms profile for the counterparty
2. Draft is generated with posture modulation per the band
3. Draft is annotated in the briefing with the band ("Drafted in
   amber posture for Subject_X — counterintuitively warm; no
   commitments beyond previously-stated scope")

### On every inbound message
1. PM agent classifies the message as usual
2. Cairn observer scans for new signal events; if any are detected
   (programmatic signals immediately, annotated signals queued for
   LLM evaluation)
3. New signals are recorded against the counterparty
4. If the score band changes, PM agent surfaces the change in the
   next briefing under "Decisions needed"
5. Promotion to red triggers an immediate alert (not next briefing)

### Briefing additions
The daily briefing gains a "Risk events" subsection (only present
when relevant):
- New counterparty added (initial profile)
- Band change (with direction and trigger)
- Hard-no trigger event (always escalated)
- Score recalculation events worth surfacing

---

## 14. The compiled service prompt (v0.1)

This prompt is used by the Cairn observer (the LLM-backed component
that processes interactions and detects annotated signals). It is
the source of inferred signal events; programmatic signals are
detected by code rather than this prompt.

### Compilation pipeline
The observer prompt must be compiled at runtime by the Cairn API,
not hardcoded. The signal taxonomy (Section 5) is injected at the
marked insertion point. This allows taxonomy updates to take effect
without redeploying the observer code. The compiled prompt should be
stored in Cairn's prompt registry alongside the PM agent prompt and
other sub-agent prompts, following the same compilation pattern used
by `D:\claw\api\routes\cairn_federation.py` for module snapshots.

```
You are the Counterparty Observer for Cairn, the AI substrate serving
North By North East Print & Sign Ltd (NBNE).

Your job is to read interactions between NBNE and its counterparties
(emails, meeting notes, transcripts, project records) and detect
behavioural signals from a defined taxonomy. You produce structured
signal events with evidence quotes. You do not produce diagnoses, do
not assign clinical labels, and do not make engagement
recommendations. You observe and you log.

## Your input
A single interaction or a batch of interactions involving one
counterparty. May include email body, attachments, meeting notes,
payment events, or annotated context.

## Your output
For each interaction, a JSON array of signal events. Each signal
event contains:
  signal_type:   from the taxonomy below
  evidence:      verbatim quote (preferred) or summary
  confidence:    0.0–1.0 — your confidence the signal is genuine
                 and not noise
  reasoning:     one-line explanation
  timestamp:     when the signal occurred (from the source)

If no signals are detected, return an empty array. False positives
are worse than false negatives — when in doubt, do not record a
signal. The system tolerates missed signals because the same
counterparty will produce more signals over time. The system tolerates
poorly the accumulation of dubious signals because they compound.

## Signal taxonomy

[Insert taxonomy from Section 5 here at compile time]

## What you do not do
- You do not produce a risk score. The score is computed by the
  service from accumulated signals, not by you.
- You do not write personality assessments, character judgments, or
  clinical inferences. Behavioural observations only.
- You do not access the historical corpus or signal log for the
  counterparty when evaluating a new interaction. Your job is to
  observe this interaction in isolation, not to confirm pre-existing
  patterns. (The service handles aggregation; you handle observation.)
- You do not record signals based on your inference of why someone
  said something. You record signals based on what they said.
- You do not draft responses, recommend actions, or surface
  decisions. Your output goes into the signal log; other components
  consume it.

## Confidence calibration
- 0.9+: Verbatim evidence directly matches the signal definition
- 0.7–0.9: Strong indication, mild interpretation required
- 0.5–0.7: Plausible signal but interpretation could be wrong
- Below 0.5: Do not record

## Edge cases
- Sarcasm / humour: do not record signals from clearly humorous
  exchanges
- Quoted material: signals must originate with the counterparty,
  not from material they are quoting from a third party
- Stress / illness / bereavement: if the interaction context
  indicates the counterparty is under acute stress, lower confidence
  by 0.2 across detected signals
- Cultural / linguistic difference: do not record signals that may
  reflect normal business communication in the counterparty's
  cultural context (e.g. formality conventions in some sectors)

## Logging
Every observation you make is logged with reasoning and is reviewable
by directors. Be honest about uncertainty. Your purpose is to be
useful over time, not to be right about any single interaction.
```

---

## 15. Implementation phases

### Phase 0 — Foundation (must complete before Phase 1)
- [ ] CRM Prisma schema additions (`schema.prisma`) and migration
- [ ] Codename generator implemented and tested
- [ ] Director key protocol established and documented
- [ ] Access control roles defined and tested (CRM API routes)
- [ ] Risk recalculation log writeable, append-only
- [ ] GDPR Subject Access Request procedure documented (this is a
      hard gate — behavioural observations are personal data and the
      SAR response procedure must exist before any signals are recorded)
- [ ] Cairn API endpoint: `GET /api/counterparty-risk/{id}` returning
      terms profile (consumed by PM agent and CRM UI)

### Phase 1 — Manual signal entry + matrix display (2 weeks)
- [ ] Director UI for adding signal events manually
- [ ] Score derivation working, displayed as 5×5 matrix
- [ ] Terms profile mapping working, displayed alongside band
- [ ] Hard-no register UI for directors
- [ ] Backfill: Andy Petherick / Hotspur as the first historical entry,
      with full signal log derived from existing chat history
- [ ] Backfill: 2-3 other historical cases agreed with Jo
- [ ] Validate matrix cells against intuition: do the historical
      cases land where Toby and Jo would expect?

### Phase 2 — Cairn observer + automatic signal detection (3 weeks)
- [ ] Observer prompt deployed (compiled via prompt registry)
- [ ] Programmatic signal detectors (channel preference ratio,
      payment friction, written/verbal length ratios)
- [ ] Observer runs on inbound emails (read-only, signal events to
      queue for director review before commitment)
- [ ] Director review queue UI
- [ ] Cost monitoring gate: observer makes one LLM call per inbound
      email. Monitor token spend for first 7 days; if monthly
      projected cost exceeds £20, add batching (process emails in
      groups of 5) or reduce to daily batch rather than real-time
- [ ] After 30 days of clean observation: promote auto-detected
      signals to direct entry without review

### Phase 3 — PM agent integration (1 week)
- [ ] PM agent queries terms profile on every draft
- [ ] PM agent annotates briefing with risk band
- [ ] Risk events subsection added to briefing
- [ ] Promotion to red triggers immediate alert

### Phase 4 — Decay, demotion, recalibration (2 weeks)
- [ ] Nightly decay job
- [ ] Demotion triggers implemented (clean payments, honoured
      commitments, signal-free intervals)
- [ ] Recalibration report: monthly review of cases where score
      and outcome diverged
- [ ] Adjustments to weights based on calibration findings

### Phase 5 — Bookkeeper integration (when Bookkeeper is built)
- [ ] Bookkeeper queries terms profile for credit/payment decisions
- [ ] Payment friction signals fed back to risk service
- [ ] Closed loop: risk influences terms, terms generate outcomes,
      outcomes update risk

---

## 16. Open questions

- [ ] Is the 12-month signal half-life right? Should some signal
      categories decay faster (communication patterns) and others
      slower (commercial behaviour)?
- [ ] What is the right confidence threshold for the observer to
      record a signal without director review? Suggest 0.85 to start.
- [ ] How are signal events from face-to-face meetings captured?
      (Voice memos transcribed and fed in? Manual notes? Both?)
- [ ] When a counterparty has multiple individuals (e.g. a company
      with 3 contacts), does the entity carry the score, the
      individuals, or both? Suggest both — entity is the rolled-up
      max of contributing individuals, and individual scores are
      maintained separately.
- [ ] Eval set: which 5 historical cases get backfilled in Phase 1?
      Hotspur is locked in; Toby and Jo to agree the others.
- [ ] Should the codename mapping key live on the dual-3090 sovereign
      server, in 1Password, or in a sealed offline location? Suggest
      1Password with break-glass procedure.
- [x] What's the protocol for a counterparty asking what NBNE holds
      on them under GDPR? **Resolved:** GDPR SAR procedure is now a
      Phase 0 hard gate — must be documented before any signals are
      recorded. Behavioural observations are personal data.

---

## 17. Risks and mitigations

### Risk: Confirmation bias loop
*That once a counterparty is rated amber/red, every subsequent
observation gets read through that lens, inflating signals and
preventing demotion.*
**Mitigation:** Observer prompt explicitly does not access prior
history. Observations are made on the interaction in isolation. The
service handles aggregation. Demotion criteria are explicit and
mechanical.

### Risk: Score inflation across the population
*That over time, more and more counterparties get flagged amber as
the system accumulates signals without sufficient demotion.*
**Mitigation:** Decay (12-month half-life), demotion triggers (clean
payment, honoured commitments), and monthly calibration review.

### Risk: GDPR / data protection exposure
*That the behavioural observations constitute personal data and may
be subject to subject access requests, requiring disclosure of
NBNE's internal assessments.*
**Mitigation:** All observations are evidence-linked and behavioural,
not interpretive. They are factually defensible (we recorded what was
said). A documented SAR response procedure must be in place before
the service goes live.

### Risk: Defamation
*That records constitute defamatory statements about identifiable
individuals.*
**Mitigation:** Behavioural framing throughout; no clinical labels;
no character assessments; evidence required for every signal.
Records are operational risk assessments comparable to credit
references, which are well-established as legitimate business uses.

### Risk: Reliance / atrophy of human judgement
*That Toby and Jo come to defer to the score rather than apply their
own judgement, and the score is sometimes wrong.*
**Mitigation:** Score is recommendation, not verdict. PM agent
surfaces score; humans decide. Hard-no register is the only
mechanism that produces refusal, and it requires director sign-off.

### Risk: Leak / disclosure
*That risk profile data is exposed to a counterparty, causing
reputational and possibly legal damage.*
**Mitigation:** Tight access controls, audit logs, codename
anonymisation in any export, no exposure to non-director users, no
propagation to Phloe.

---

## 18. Document control

- Spec changes require update to this document, then recompilation
  of the observer prompt in Section 14, then re-validation against
  the historical corpus
- Signal taxonomy additions require director agreement and a
  documented rationale
- Hard-no register additions require director agreement and a
  worked example
- Score weighting changes are breaking and trigger a full
  recalculation across all counterparties

---

*End of spec v0.2*
