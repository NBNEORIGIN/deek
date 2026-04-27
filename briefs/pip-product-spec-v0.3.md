# PIP — PRODUCT SPECIFICATION v0.3
## Personal Sovereign Memory with AI Interface

**Document type:** Product specification
**Status:** Draft v0.3 — addresses gaps surfaced in v0.2 review
**Authors:** Toby Fletcher (NBNE), drafted with Claude
**Date:** April 2026
**Supersedes:** v0.2 (April 2026)
**Purpose:** Define what Pip is, what it isn't, and the architectural properties it must have. This document precedes any implementation brief. Implementation briefs flow from this spec, not the other way around.

**Changes from v0.2:**

- §5 strict/adaptive split now includes a **capability table** (not just a memory-retention table) — strict mode loses ~70% of Deek's current "intelligence-feeling" features, and that's named explicitly rather than implied
- §5.7 (NEW) — explicit voice / ambient consent boundary
- §6 PMF deliverables include a **second-language reference implementation** as a v1 success criterion, not just the spec doc
- §7.4 (NEW) — formal departure flow specification (the missing edge case at the intersection of employment law + data ownership)
- §9 (REWRITTEN) — commits to a **specific tenant model**: per-Pip Postgres schema in shared cluster, with code-level enforcement. Names the trade-offs and migration path from current shared-schema state
- §10.1 onboarding now includes a **structured baseline-memory interview** as a v1 deliverable — solves the strict-mode cold-start problem
- §13 implementation programme **promotes the impressions layer (Brief 2) into v1 in shadow mode** — without it, a new Pip feels measurably worse than today's Deek
- §15.5 (NEW) — explicit decision on tier-2 role-specific briefs: they migrate to Pip when each staff member gets a Pip, with a structured share-back-to-Deek affordance
- §7.2 (Option B full-stack USB) tightened — explicit non-binding architectural guidance only; removed from anything that influences v1 decisions
- §11.3 economics rewritten as a budget *target* rather than model-specific projection
- §12 marketing line revised: *"The personal AI you can take with you. Sovereign by default. Open standard."*
- §14 multi-Pip federation entry: committed to *out-of-scope-and-unbiased-by* rather than *out-of-scope-but-don't-preclude*
- §15 implementation programme reordered + tenant-awareness refactor sized honestly (8-12 weeks, not "one item among 17")
- §16 success criteria includes the second-implementation round-trip + the audit-trail-of-no-unauthorised-corporate-access criterion sharpened

---

## 1. Definition

Pip is a memory product. It accumulates, organises, and surfaces what its user knows, thinks, and decides over time. It has an AI interface — the user interacts with their memory through conversation, voice, and text — but the memory is the product. The AI is how you get to the memory.

This framing matters. Most personal AI products today are positioned as intelligence services with memory as a side feature; Pip inverts the relationship. The intelligence layer is increasingly commoditised — frontier models double in capability every year and become cheaper as they do — but a user's accumulated personal memory is irreplaceable, scarce, and theirs. Pip's value is the memory; the AI is the interface; the durable asset is the data.

A Pip instance belongs to one user. It accumulates context about that user — work, life, projects, decisions, preferences, conversations, things they've explicitly chosen to remember — over months and years. The user owns this memory in a meaningful sense: it is exportable to a documented open format, transferable to other Pip instances on different infrastructure, and travels with the user when they leave whichever organisation provided the original deployment. It is technically and commercially yours.

Pip is built on the same multi-tenant platform as Deek (NBNE's business AI) and Brian (the sovereign appliance product). The platform is sovereign by default, runs on local hardware where possible, and uses tiered model routing to minimise API costs while preserving access to frontier capabilities when needed.

Pip is offered initially as an employment benefit by NBNE to its staff, and subsequently as a standalone product to professional services firms (via Brian deployments) and individuals (via a future hosted service). The same product, the same architecture, the same data ownership model applies in every context.

## 2. Naming: brand vs instance

**Pip** is the product name. The brand. The thing on the box, the website, the marketing material. When users talk about the product to others — recommending it to friends, evaluating it as employers, comparing it to alternatives — they call it Pip.

**The user's instance can be named anything the user wants.** During onboarding, the user is invited to name their AI. They can call it Pip if they like the name. They can call it after a pet, a fictional character, a grandparent, anything. The name is theirs — it's how they address their AI in voice commands ("Hey Frank," "Hey Lyra," "Hey Bob"), how the AI refers to itself in conversation, how it appears in their interface.

This isn't decoration. The naming choice reinforces the ownership property. *Your* AI is *yours* in every meaningful sense, including what it answers to. Two Pip users won't experience the product identically; they'll experience their relationship with their own named companion, which happens to be the same underlying technology.

**Technical implications.** The wake word for voice access is per-instance configurable. The system prompt internal to each Pip refers to it by the user-chosen name. UI elements showing the AI's name use the user's choice. The default is "Pip" if the user skips the choice during onboarding, but the default is just a starting point.

## 3. What Pip is not

This is at least as important as what Pip is.

- **Not a corporate monitoring tool with friendly framing.** Conversations between a user and their Pip are private. They are not visible to the user's employer, not extracted for management review, not aggregated into organisational dashboards by default. Information flows from a user's Pip to an organisation only when the user explicitly chooses to share it, on a per-item basis.
- **Not a SaaS product that rents memory.** The user's data is not Pip's product. The memory belongs to the user, in a form they can take with them, regardless of who hosts the instance at any given time.
- **Not a general-purpose chatbot.** Pip is opinionated. It accumulates context about its specific user, references that context, and becomes more useful as the relationship lengthens. A first conversation with Pip is less impressive than a hundredth conversation with Pip. This is by design.
- **Not a substitute for human contact, professional support, or formal HR processes.** Pip is a tool. It cannot replace medical advice, legal advice, mental health professionals, or properly-conducted workplace processes. Where conversations indicate a user needs one of these, Pip says so plainly.
- **Not an inferring intelligence by default.** Pip does not draw conclusions about its user and store them as memory unless the user explicitly authorises this behaviour (see §5). Pip remembers what users tell it; it does not silently build a profile of who they appear to be.

## 4. Core properties

Pip's design is anchored in six properties. Every implementation decision must preserve them.

**Memory-first.** The memory is the product. The architecture, the storage layer, the indexing, the retrieval, the cross-linking, the consolidation logic — these are the load-bearing components. The AI interface sits on top of the memory, not the other way around.

**Sovereignty.** The user's data and conversations are stored on infrastructure under their control or under the control of an organisation they trust (initially NBNE for staff, future commercial customers for their staff, eventually the user themselves for hosted-individual subscribers). Inference happens on local GPUs by default; cloud APIs are called only when local models cannot produce an adequate answer, and the user can configure category-aware halts that prevent escalation for sensitive content.

**Portability.** A user's Pip memory can be exported to a documented, open file format and imported into another Pip instance running on different hardware. The export is complete: re-importing produces a Pip with identical accumulated context. The format is published as an open standard — see §6.

**Personalisation.** Each Pip instance accumulates context unique to its user. The same question asked of two different Pips produces different answers, calibrated to each user's history, preferences, vocabulary, and concerns. Generic answers are a failure mode.

**Transferability.** A user can take their Pip with them when they leave whichever organisation provided it. The act of changing employer does not strip them of their accumulated personal AI context. This is a deliberate design property and a marketing differentiator. The departure flow is specified in §7.4.

**Trustworthy memory.** Pip remembers only what the user has explicitly chosen to remember. The user can audit every memory entry, see its provenance, and delete anything they no longer want stored. Inference about the user — drawing conclusions from conversations — is opt-in, separate from explicit memory, clearly tagged, and equally auditable.

## 5. The memory write discipline

This is the most architecturally consequential constraint and worth working through carefully.

### 5.1 The principle

A Pip's durable memory of its user is built only from information the user has explicitly chosen to record. Ephemeral session-level reasoning happens — Pip can't function without making moment-to-moment inferences during a conversation — but those inferences do not persist by default. When the conversation ends, the inferences end.

What persists is what the user explicitly told Pip ("remember that I prefer morning meetings"), what the user explicitly confirmed ("yes, that's right, I do prefer mornings"), what the user explicitly asked Pip to record ("save this email draft for later"), and Pip's own outputs that the user kept rather than discarded.

This produces a memory that grows more slowly than a typical inference-eager personal AI. It also produces a memory the user can fully audit. Every entry came from somewhere they can trace. Nothing was inferred behind their back. That trust property is what justifies the product's existence — a user who can't trust the memory has no reason to choose Pip over the dozen other personal AI products on the market.

### 5.2 Strict mode and adaptive mode

The user chooses, at onboarding, between two modes. They can switch at any time.

**Strict mode** is the default and the trust-first option. Durable memory contains only what the user has explicitly told Pip or explicitly confirmed. No inference is retained. The memory is small, explicit, and entirely auditable. Recommended for users who want maximum control and minimum surprise.

**Adaptive mode** allows Pip to also retain inferences drawn from conversations. *"User seems to prefer concise responses."* *"User has been thinking about the Berwick project frequently this week."* *"User tends to draft emails and then revise them before sending."* These inferences improve responsiveness over time but they are guesses about the user that the user did not explicitly authorise. Inferred memories are tagged as such, dated, and reviewable. Recommended for users who want the AI to feel more like a colleague than a notebook.

### 5.3 Capability differences between modes (NEW)

Strict and adaptive don't just differ in what they retain — they differ in what features are available. This was implicit in v0.2; making it explicit because the gap is material:

| Capability | Strict mode | Adaptive mode |
|---|---|---|
| Retrieval over explicit memory | ✓ | ✓ |
| Conversation history within a session | ✓ | ✓ |
| Source-text + embedding search | ✓ | ✓ |
| Memory write on user-stated content | ✓ | ✓ |
| Memory write on user-confirmed content | ✓ | ✓ |
| Memory write on user-saved content | ✓ | ✓ |
| **Salience-weighted ranking** | flat ranking | weighted by signals |
| **Cross-memory schema consolidation** | off | on (opt-in per category) |
| **Pattern-based suggestions** ("you usually do X on Mondays") | off | on |
| **Drafting-style adaptation** (tone, formatting, length) | off — generic | on — calibrated |
| **Topic/project active-context tracking** | off | on |
| **Salience signals exposed in memory inspection** | n/a | yes |

A user in strict mode will have a Pip that **searches their explicit memory accurately** but does not **build patterns from their use**. The capability gap is meaningful — strict mode is closer to "structured personal note search with conversational interface" than to "AI colleague that knows you." That trade-off is presented honestly at onboarding (§5.4).

### 5.4 The honest framing

The choice is presented to users at onboarding with both sides argued fairly:

> *Strict mode keeps things simple: I remember what you tell me, nothing more. Your memory will be smaller, but you'll know exactly what's in it. You'll get accurate search over what you've explicitly told me, but I won't get noticeably better at predicting what you want over time. Recommended if you want maximum control and treat me more like a notebook than a colleague.*
>
> *Adaptive mode lets me notice patterns and remember them: that you prefer mornings, that this project has been weighing on you, that you tend to draft replies and then revise them. I'll calibrate to your tone, surface things based on what's been on your mind, and feel more like a colleague over time. Some of what I remember will be my interpretation rather than your direct words. You can see and delete anything I've inferred. Recommended if you want me to compound in usefulness.*
>
> *You can switch between modes at any time. Most people start in strict and move to adaptive after a few weeks once they've seen what I do. There's no wrong answer.*

### 5.5 Categories of inference

In adaptive mode, Pip can infer and retain memories in these categories:

- **Behavioural patterns:** when the user works, what tools they reach for, how they respond to messages
- **Preference inferences:** tone, formatting, level of detail, types of suggestions that land
- **Topic interests:** subjects that come up repeatedly, projects that recur, areas the user is thinking about
- **Working-style inferences:** how the user approaches problems, what their drafting process looks like, how they handle uncertainty
- **Emotional/state inferences (default off, separately opt-in):** how the user seems to be feeling, stress signals, energy patterns
- **Relationship inferences (default off, separately opt-in):** how the user appears to relate to specific colleagues, clients, family members

The default adaptive mode includes the first four categories. The fifth and sixth are opt-in separately because they're more sensitive — Pip noticing emotional state or interpersonal tension and *retaining* that observation crosses a different threshold than noticing the user prefers concise summaries. Users can enable these categories selectively and disable them at any time.

**Failure mode named explicitly:** A mis-calibrated emotional or relationship inference (e.g. "user dislikes manager X") doesn't stay neutral — it influences retrieval and biases future answers. The audit-and-correct mechanism in §5.6 mitigates but doesn't eliminate this risk. These categories warrant the higher bar.

### 5.6 Architectural implications

The memory write path needs explicit gating in code. There is a clear distinction between two channels:

- **Session inference:** transient reasoning that affects the current response only. Lives in the conversation context. Discarded when the conversation ends. Always operates regardless of mode.
- **Durable memory:** persisted, retrievable, indexed for retrieval in future conversations. Created only via explicit user action (strict mode) or via authorised inference (adaptive mode).

Every durable memory entry has provenance metadata: when it was created, what created it (user-stated, user-confirmed, user-saved, or inferred), and in inference cases, what evidence triggered the inference. The user can see all of this in their memory inspection view.

Inferred memories have an additional property: they can be challenged. The user can say *"why do you think I prefer concise responses?"* and Pip shows the conversation snippets that led to that inference. The user can confirm (the inference is upgraded to a confirmed memory), reject (the inference is deleted and a counter-pattern is registered so Pip doesn't infer the same thing again), or modify ("actually I prefer concise in email but detailed in chat"). Each of these is a learning event for the system.

**Implementation note.** The current Deek codebase has at least seven memory-write paths that bypass user consent (`_write_toby_memory` in brief replies, in triage replies; the consolidation pipeline; the impressions signal extractor; the conversational normaliser's verdict capture; the autodrafter; the schema distillation cron). Reaching strict mode requires gating every one of these behind an explicit-mode check. This is a multi-file refactor across the brief, triage, memory, and research modules — not a single gate. Implementation brief should size it accordingly.

### 5.7 Voice and ambient capture (NEW)

Voice access introduces a specific consent boundary that policy alone cannot enforce. This section commits to architectural constraints, not policy.

**Wake-word gated.** Voice transcription enters session context **only** when a wake-word is detected. The wake-word detector runs on-device (no cloud round-trip) over a rolling 10-second audio buffer. Audio outside that buffer is never recorded.

**Buffer purge.** The 10-second rolling buffer is overwritten in place. No persistent storage. No cloud transmission. If the wake-word never fires, the buffer's contents never leave the audio subsystem.

**Adaptive mode does not auto-infer from voice.** Even in adaptive mode with all six inference categories enabled, voice-source inferences require per-conversation explicit consent ("is it OK if I remember what you said about feeling tired today?"). Voice is treated as a higher-sensitivity channel because the user has less moment-to-moment control over it than they do over typing.

**Failure modes named:** A voice-trigger false-positive (Pip hears "Pip" when the user said something that sounded like it) means a few seconds of conversation enter session context. Mitigation: the user is shown a transcript of what was captured before any inference is retained, and can purge it.

### 5.8 External information

A subtle distinction. Pip can use information from external sources to *answer* questions — web searches, retrieved documents, API calls, the user's own files. What it does not do is *retain* facts about the user inferred from such sources without the user's explicit action.

Example: the user asks Pip to find the best restaurants near Alnwick Castle. Pip searches the web and answers. Pip does not retain "user is interested in restaurants" as a profile fact. The conversation happened; the answer was given; nothing about the user was added to durable memory.

If the user explicitly says "remember that I want to try Lord Crewe Arms next month," that's an explicit memory and gets stored. The line is consent and intent, not data origin.

**Edge case (NEW): user-pasted content about themselves.** The user pastes their LinkedIn profile, their CV, a personal essay. The data is from outside but the act of pasting is explicit user action. Default behaviour: Pip asks once — *"want me to remember the parts of this that are about you?"* — and the user opts in or out. No silent retention.

## 6. The Personal Memory Format (PMF)

Pip's portability claim is meaningful only if the export format is open, documented, and implementable by other software. Pip implements the *Personal Memory Format* (PMF), a published open standard that NBNE develops and maintains.

### 6.1 Why a standard rather than a proprietary format

A user whose data is locked into a proprietary export format isn't actually portable. They can move their data only if the destination product happens to support the source product's format. As soon as the source product is the only product that reads its own format, the portability claim is theatre.

A published standard means:

- The format is documented openly enough that other products can implement it independently
- Pip's portability claim is verifiable rather than asserted
- An ecosystem becomes possible: backup tools, archive utilities, migration helpers, future personal AI products
- The user's data outlasts any specific product that hosts it
- Pip's positioning is strengthened — *"the only personal AI built on an open memory standard"* is a credibility line that no other product can claim

### 6.2 Format structure

A PMF export is a single archive containing:

```
pmf-export-{user_id}-{timestamp}/
├── manifest.json           # version, user identity, export metadata
├── identity.json           # public key, signed identity claims
├── memory/
│   ├── chunks.jsonl        # all memory chunks, one per line
│   ├── entities.jsonl      # entity nodes
│   ├── edges.jsonl         # entity edges
│   ├── schemas.jsonl       # consolidated schemas
│   └── conversations.jsonl # conversation history with timestamps
├── inferences/
│   └── inferred.jsonl      # adaptive-mode inferences with provenance
├── embeddings/
│   ├── model.json          # embedding model name, version, dimension
│   ├── vectors.bin         # raw embedding vectors, indexed by chunk_id
│   └── source_text.jsonl   # original text per chunk_id (for re-embedding)
├── attachments/
│   └── {sha256}.{ext}      # files the user has shared with Pip
├── config/
│   ├── identity.md         # the user's PIP_IDENTITY equivalent
│   ├── preferences.yaml    # tone, formatting, response style
│   ├── routing.yaml        # tier preferences, sensitivity rules
│   └── mode.yaml           # strict/adaptive mode settings, opt-in categories
└── signature.bin           # signature of manifest.json by user's private key
```

All formats are open: JSON, JSONL, YAML, plain text, standard binary. No proprietary serialisation. A future Pip implementation in a different language must be able to read this format. Equally, a non-Pip product that wishes to be PMF-compatible can implement the format from the published spec.

### 6.3 Always keep source text alongside vectors

The single most important architectural decision in PMF: every memory chunk's original source text is stored alongside its embedding vector. This means embeddings can always be regenerated against any future embedding model.

This costs storage — duplicating text alongside vectors — but the cost is small (a few extra bytes per chunk) and the benefit is permanent flexibility. Without this, an embedding-model change locks every existing user into the old model forever, or forces a one-way migration that breaks export compatibility. With it, embedding-model changes become a routine operational matter rather than a crisis.

**Operational policy:** embedding model changes happen at most annually, with minimum 90-day deprecation notice and a documented migration path. Per-user re-embedding budget is included in the per-user infrastructure cost model.

### 6.4 Versioning

The format is versioned. Version 1 is the baseline. Future versions extend rather than break — a v1 export must always be importable into a v2 destination. Breaking changes require migration tooling provided by the project.

### 6.5 Signing and verification

Every PMF export is cryptographically signed by the user's private key. The destination Pip verifies the signature against the user's public key (presented at import time) before importing. This prevents tampered exports from corrupting a destination instance and provides a chain of provenance for the user's memory.

**Key rotation:** if the user's private key is compromised or lost (and recovered via §8.2 mechanisms), PMF supports re-signing an existing export with a new key. The original public key remains in the manifest as `previous_keys` so the chain of provenance is preserved across rotations.

### 6.6 What's not portable

Some things deliberately don't travel in a PMF export:

- Conversations the user has explicitly deleted (the deletion is honoured)
- Content the user shared with their employer's organisational AI (that content belongs to the employer once shared, separately from the user's own copy)
- Content under any legal hold (GDPR right to be forgotten, regulatory retention requirements)

The user is informed at export time about anything not included.

### 6.7 Reference implementation in a second language (NEW)

PMF is a published standard, but a single implementation does not constitute a standard — it constitutes a format with documentation. To make the standard meaningful, **a v1 deliverable is a reference implementation of PMF export and import in a different language to Pip's primary stack** (Pip is Python; reference implementation is Rust or TypeScript). This implementation:

- Round-trips a Pip export into its own data store and back into a fresh Pip
- Validates manifest signatures correctly across implementations
- Lives in a separate repository (`nbneorigin/pmf-rs` or similar) under the same open licence as the spec
- Is maintained alongside the spec — every PMF version bump requires both implementations to be updated before release

This is the test that proves PMF is a standard rather than a documented internal format.

## 7. Distribution and the USB key

Pip is packaged for distribution in two ways. They serve different markets.

### 7.1 Option A: USB key as data carrier (v1 default)

The user's data and identity live on an encrypted USB key. The Pip runtime lives on a separate host machine — their home computer with Pip installed, an office workstation with Pip installed, a Brian appliance, or a hosted Pip service. The USB key plugs into the host, the user authenticates, the host's Pip software loads the user's memory from the USB key and operates against it. When the user unplugs, the data is unmounted from the host (or remains encrypted on the host as a cached copy if the user prefers).

This is the simpler implementation and the right v1 distribution. The host provides compute; the USB key provides identity and memory.

For NBNE staff, this is the natural fit. The office has a Brian (or Deek's existing infrastructure); staff have USB keys. Plug in at work, work, unplug, take the key home. The compute infrastructure stays in the building (sovereignty intact for the business); the personal data stays with the individual (ownership intact for the user). When the staff member is at home, they can use Pip via a hosted service or on their own home computer if they have one with sufficient compute.

A typical staff USB key is 16-32GB — plenty for a multi-year accumulation of memory plus future growth. The USB key is encrypted with strong full-disk encryption; the user authenticates to unlock it (password, or biometric using the host's hardware where supported).

### 7.2 Option B: USB key with full Pip stack (deferred)

Mentioned only to note that v1 implementation choices should not preclude this future variant. **No further architectural guidance from v0.2 carries over** — keeping the spec free of decisions made for a v3+ scenario that may shift. When Option B becomes a real product, it gets its own spec.

### 7.3 Backup pairing

Every Pip USB key is paired with a backup destination. For NBNE staff, that's an encrypted partition on Brian (or on a designated NAS) that mirrors the USB key's contents on every plug-in event, automatically. The user's responsibility is just plugging the key in periodically.

If the key is lost, the staff member visits the office, generates a new key from the backup, and is back to work in twenty minutes. The lost key's encryption keys are revoked.

The backup pairing is a v1 architectural requirement, not an optional extra. Retrofitting backup is painful; building it in from the start is straightforward. It also serves as the natural place for employer-side opt-in escrow — an employer can hold a recovery copy of staff USB keys in case of catastrophic loss, with the staff member's explicit consent and clear policy about when such escrow may be used.

### 7.4 Departure flow (NEW)

The intersection of "your data travels with you" and "the backup lives on the employer's hardware" needs an explicit runbook, not a per-employer policy. The standard departure flow is:

**T-30 days (notice given).** Pip's UI shows a "leaving Pip" banner. The user can run a PMF export at any time. Pip's owner (employer) is notified that an export will be generated.

**T-7 days.** Pip generates a draft PMF export for the user to verify completeness. User can flag missing content; Pip regenerates with corrections.

**Last day.** Final automated PMF export is generated and made available to the user via an authenticated download link valid for 90 days. The user receives the link via two independent channels (e.g. their Pip + their personal email).

**T+0 to T+90 days.** Encrypted backup on Brian is retained for legal/forensic purposes only. Access requires:
- Explicit HR + legal sign-off (documented justification)
- Audit log entry that's visible to the departed user
- The backup is never read into another Pip instance — only into export tooling that produces a fresh PMF for legal review

**T+90 days.** Automated purge of the backup. Audit log retained.

**Key revocation.** On the last day, the departed user's signing key is revoked from any organisational systems (the user's own copy of the key, on their personal devices, is not revoked — they can continue to use PMF exports signed under that key elsewhere). PMF imports attempting to use the departed user's identity against organisational systems will fail.

**The user keeps their PMF.** Always. A user who never wants to use Pip again still has their export — it's their memory.

## 8. Identity, ownership, and authentication

A Pip user has a persistent identity that exists independently of any specific deployment. The identity is the user's, not the deployment's.

### 8.1 Key-based identity

The identity is anchored on a key pair the user controls. When a Pip instance is provisioned for a user, the user's public key is associated with the instance. PMF exports are signed with the user's key. This means:

- Two Pip instances belonging to the same user share an identity even on different hardware
- An exported memory is cryptographically attributable to the user, allowing any destination instance to verify provenance
- A user moving from one host to another doesn't require trust between the hosts — the user's signature is the chain of authority

For NBNE staff Pips, key generation happens during onboarding. The key pair is generated on the user's first access (PWA or terminal), the public half is registered with Pip, the private half is stored in their device's secure enclave (iOS keychain, Android keystore, Windows Hello, similar) plus mirrored to the encrypted USB key.

### 8.2 Recovery

Three layered mechanisms, in order of preference:

1. **Backup pairing (§7.3) — primary.** The Brian-side backup contains a key wrap that the user can recover by visiting the office and authenticating via a second factor (badge + passphrase, or HR-witnessed identity verification). Covers the routine case.

2. **Optional employer escrow.** With explicit opt-in at onboarding, the employer holds a sealed recovery for catastrophic-loss cases. Used only with explicit staff consent at the time of recovery.

3. **Recovery phrase — last resort.** A 24-word phrase the user keeps somewhere safe. Only required if the user has opted out of both (1) and (2). Recommended for employer-deployed Pips: backup pairing + optional escrow, no phrase needed. Recommended for individual subscribers: backup pairing + recovery phrase.

The user is informed of all options at onboarding and chooses what level of recovery infrastructure they want.

### 8.3 Authentication

Authentication to a specific Pip instance uses the standard pattern: user signs a challenge with their key, Pip verifies, session opens. No passwords. No accounts in the conventional SaaS sense. The USB key can hold an unlock credential that authenticates against the user's key pair, simplifying day-to-day use.

### 8.4 Multi-device access

In v1, a user's Pip is accessed from one device at a time — the device into which their USB key is plugged, plus any device authenticated with their key for hosted scenarios. Multi-device sync within a single Pip instance (laptop and phone both showing the same active conversation in real-time) is desirable but deferred to v2.

## 9. The relationship between Pip, Deek, and Brian — and the tenant model

These are three products on one platform. The platform's tenant model is the most architecturally consequential decision in the v1 build, and v0.3 commits to it explicitly.

**Deek** is the organisational/business AI. One Deek per organisation. Contains institutional memory — clients, suppliers, projects, decisions, policies. Owned by the organisation. Accessible to authorised staff. Not portable in the same sense as Pip.

**Pip** is the personal AI. One Pip per user. Owned by the user. Portable. May be hosted by an organisation as an employment benefit, but data ownership is the user's.

**Brian** is the deployment/packaging product. A Brian appliance hosts one Deek (the customer organisation's) plus N Pips (one per staff member). Brian is sold to small professional services firms; it bundles hardware, platform, onboarding, and ongoing services into a single offering.

### 9.1 Tenant isolation model — committed

**Decision:** per-Pip Postgres schema in a shared cluster, with code-level enforcement.

The four candidate models considered:

| Model | Isolation | Ops cost | Migration cost from current state | Verdict |
|---|---|---|---|---|
| Per-Pip database | Strongest | Backup × N, monitoring × N, migration × N | High | Operationally infeasible at 50+ users |
| **Per-Pip schema in shared cluster** | **Strong (schema = real boundary)** | **Single Postgres to manage; schema iteration for migrations** | **Medium** | **Chosen** |
| Row-level security in shared tables | Policy-only | Lowest | Lowest | One missing WHERE clause = breach; rejected |
| Per-Pip container | Compute isolation only — DB still shared | Highest | High | Doesn't address the data-layer boundary |

**Implementation details for the chosen model:**

- Each Pip instance gets a Postgres schema named `pip_<user_uuid>` containing the user's memory tables (`chunks`, `schemas`, `entities`, `edges`, `conversations`, `inferences`, etc.)
- Deek lives in a `deek_<org_uuid>` schema in the same cluster
- The platform's connection pool is **schema-pinning**: every connection acquired by a Pip's request handler has its `search_path` set to that Pip's schema and the `public` (shared catalogue) schema only. Cross-Pip queries are physically impossible from the application layer.
- Cross-tenant reads (Pip-reading-Deek per §9.2) go through a dedicated `cross_tenant_reader` role with explicit SECURITY DEFINER functions that whitelist specific allowed reads. No raw SQL access across the boundary.
- Migrations use a per-schema iteration pattern: `for schema in list_pip_schemas(): apply_migration(schema, sql)`. Migration tooling is built in v1, not retrofitted.

**Migration from current shared-schema state:**

Today's Deek runs as one schema `public` containing everything — `claw_code_chunks`, `cairn_intel.*`, etc. The migration is:

1. Rename current `public` schema content to `deek_nbne` (NBNE's organisational schema)
2. Build the schema-pinning connection pool layer
3. Create `pip_<toby_uuid>` for the first Pip; populate from selectively-tagged content in `deek_nbne`
4. Roll out per-Pip schemas as each new staff Pip is provisioned

This is the work that step 3 of §15 covers. Realistic estimate: **8-12 weeks of focused work**, not "one item among 17."

**Defense in depth.** Even with the connection pool enforcing schema search paths, application code adds explicit assertions (`assert query.schema == ctx.tenant_schema`) at the data-access layer. A code-level mistake doesn't breach because the DB-level pin would refuse. A DB-level mis-config doesn't breach because the code-level assertion would refuse. Both layers must fail simultaneously for a cross-tenant read to occur.

### 9.2 Information flow

**Deek → Pip (allowed by default, configurable):** A user's Pip can read from the organisation's Deek for context. When the user asks Pip about a client, Pip pulls relevant business context from Deek to give a richer answer. This is the staff-member-using-business-context use case and the primary value of Pip-hosted-by-employer.

Implementation: Pip's request handler can call SECURITY DEFINER functions on `deek_<org_uuid>` schema (e.g. `deek_search(query)`, `deek_get_project(id)`) but cannot SELECT directly. The function results are read-only in Pip's session.

**Pip → Deek (always requires explicit user consent):** A user's Pip never writes to Deek without the user's explicit per-action consent. Pip may suggest sharing — *"This observation about workload seems like something Jo would want to know. Share with Deek?"* — but the user accepts, declines, or modifies before anything propagates.

The asymmetry is enforced at infrastructure level: Pip's database role has no write privileges on Deek's schema, period. A Pip-side bug cannot breach the boundary because the boundary is below the application.

### 9.3 Cross-Pip isolation

A user's Pip cannot read from another user's Pip. Full stop. Schema search-path pinning at the connection pool level makes this physically impossible; the application layer never sees other users' schemas in its search path.

Aggregated cross-Pip insights (e.g. "team morale this week") are achievable only via voluntary contribution: each user's Pip asks them, they answer, they choose what to share, and the aggregation happens at Deek level on the contributed shared content. Never via cross-reading.

## 10. Onboarding and ongoing experience

The user experience design matters as much as the architecture. A technically excellent Pip that staff don't use is a failure.

### 10.1 First conversation + structured baseline-memory interview

When a user first accesses their Pip, the experience is a guided onboarding *conversation* — not a form, not a settings page — because Pip is a conversational product and onboarding should demonstrate that.

The conversation covers, in order:

1. **Introduction in plain language:** what Pip is, what it isn't, what data it stores and where
2. **Naming:** the user names their AI (or accepts the default)
3. **Mode selection:** strict or adaptive, with the honest framing from §5.4 + the capability table from §5.3
4. **Key generation:** explained, then performed, with the recovery options explained (§8.2)
5. **Structured baseline-memory interview (NEW v0.3):** ~30 minutes of scripted questions designed to produce 50-100 explicit memory entries before mode selection's effects matter

The baseline interview is the v0.3 fix for strict-mode cold-start. Without it, a user who chooses strict mode has an empty Pip and abandons. With it, even a strict-mode Pip on day one has substantive context.

The interview script covers, at minimum:
- **Identity context:** name, role, location, working pattern, who they work with
- **Project context:** what they're currently working on, in their own words
- **Communication preferences:** length, tone, formality, what irritates them, what works
- **Recurring decisions:** the 5 things they find themselves deciding repeatedly that they wish they could just say once
- **Things that should NOT be remembered:** topics, contacts, content the user wants Pip to actively forget if mentioned

Each answer becomes one or more explicit memory entries, tagged `source=onboarding_interview`. The user reviews the captured entries at the end of the interview and confirms / edits each.

The first session ends with Pip suggesting concrete things the user could try next time, calibrated to their stated role. *"You mentioned you draft a lot of customer emails — try giving me one to redraft and we'll see how I do."* Not "ask me anything!"

### 10.2 Daily use

Pip is designed for habitual use. The interface supports quick interactions: voice notes from the field, photo attachments, short text messages, full conversations on quiet evenings. A staff member should reach for their Pip the way they reach for Notes or WhatsApp — low friction, no ceremony.

The home interface (PWA in v1, possibly native apps later) shows:

- A simple chat affordance — *"ask Pip something"* (or whatever the user named their AI)
- Recent conversations
- Suggested actions, calibrated to the user's pattern of use (adaptive mode only; strict mode shows a generic prompt)
- Mode indicator (strict or adaptive) so the user always knows which mode is active
- Optional: morning briefing if the user has opted in

Pip never sends notifications without explicit per-category opt-in. The default state is silent and reactive. The user comes to Pip; Pip does not interrupt the user.

### 10.3 The share-with-Deek affordance

Every Pip response includes an optional small affordance: *"Share this with Deek?"* The user taps to share, edits if they want, sees a confirmation of what's being shared, and approves or cancels. The shared content appears in Deek with attribution and timestamp.

The affordance is unobtrusive. It's not the primary action. The primary action is helping the user; sharing is incidental. If users feel the affordance is pushing them to share, the design has failed.

### 10.4 Memory inspection and audit

Every Pip user has access to a memory inspection view. They can browse what their Pip remembers, organised by topic, with the source of each memory visible. Strict-mode entries are tagged with how the entry was created (user-stated, user-confirmed, user-saved). Adaptive-mode inferred entries are tagged separately, with the conversation snippets that triggered the inference visible on demand.

The user can:

- View any entry
- Delete any entry (real deletion: chunks removed, embeddings removed, attached files removed)
- Bulk delete by category, date range, or topic
- Challenge an inference (Pip explains the evidence, user confirms or rejects)
- Export everything (PMF format)
- Switch modes (with offer to delete prior inferred memories on switch from adaptive to strict)

The audit trail is the trust mechanism. A user who can verify that Pip is behaving as advertised has reason to use it; a user who can't, doesn't.

## 11. Routing, cost, and sovereignty

Pip uses tiered routing — local model first, escalate to cheaper API tiers, then mid-tier, then frontier, with fallback. The user can configure the routing.

### 11.1 Per-user budget

Each Pip user has a monthly API budget. NBNE staff have budgets set by the employer (default £15/month, adjustable). External Pip customers have budgets tied to their subscription tier.

When the budget is approached (80%), Pip warns the user. When exhausted, Pip falls back to local-only inference for the rest of the month. The user is told what's happening and offered the option to top up if their tier supports it.

### 11.2 Category-aware halts

The user can mark conversation categories as local-only. Default suggested categories at onboarding:

- Personal health and medical
- Family and relationships
- Financial concerns
- HR-related matters
- Legal matters

Conversations in these categories never escalate to cloud APIs regardless of complexity. The user gets a less sophisticated answer but no data leaves their hardware. They can override per-conversation if they choose.

### 11.3 Economics target

The unit economics target: **monthly per-user cloud-API cost under £2 for typical usage**. This is achievable today with a tiered routing strategy that uses local models for ~70% of queries, a low-cost API tier for ~25%, and frontier escalation for ~5%. Specific model choices are an operational decision and may shift; the budget commitment doesn't.

A six-staff NBNE deployment runs at approximately £20-50/month in API fees plus the fixed cost of the Brian appliance. The marginal cost of adding a Pip user is small compared to operational + storage cost, which is itself small.

## 12. Branding and product line

**Brand structure:**

- **Deek** — business AI. NBNE's; future customers'.
- **Pip** — personal AI product. Each user's instance is named by the user.
- **Brian** — sovereign appliance bundling Deek + Pips for a customer organisation.
- **PMF** — Personal Memory Format, the open standard for memory portability.

**Positioning:**

- Deek: serious, capable, integrated with business systems
- Pip: warm, helpful, distinctively yours, travels with you
- Brian: the box that arrives, gets installed, runs everything
- PMF: the standard your AI memory is built on

**Pip's marketing line, when commercialised:** *"The personal AI you can take with you. Sovereign by default. Open standard."*

The portability and ownership properties are the differentiators. Every other personal AI product on the market today rents memory. Pip sells it.

## 13. Scope of v1

V1 is deliberately narrow but **broader than v0.2** — the impressions layer (Brief 2) is promoted into v1 in shadow mode because without it, a new Pip feels measurably worse than today's Deek and adoption suffers.

**In scope:**

- Tenant isolation per §9.1 (per-Pip Postgres schema, schema-pinning connection pool, migration tooling) — the foundation
- Per-Pip identity, key pair, isolation
- PMF export and import per §6
- **PMF reference implementation in a second language** (Rust or TypeScript) — proves the standard is real
- USB key as data carrier (Option A from §7.1) with backup pairing (§7.3)
- Departure flow per §7.4
- Strict and adaptive memory modes (§5.2) with explicit capability gating (§5.3) and audit trail (§10.4)
- **Voice ambient-discard architecture per §5.7** — wake-word gated, on-device, 10s rolling buffer
- Read access from Pip to Deek (with per-tenant authorisation via SECURITY DEFINER functions)
- Voluntary share-to-Deek affordance
- Per-Pip API budget and category-aware halts
- PWA interface (extending existing work)
- Voice access (extending existing voice path)
- **Onboarding conversation including structured baseline-memory interview (§10.1)**
- Privacy controls: delete, export, bulk operations, mode switch
- PMF specification published as a documented open standard
- **Per-Pip impressions layer in shadow mode** — accumulates data so the v2 cutover is a flag flip

**Out of scope, deferred to later versions:**

- Brief 3 (graph) per-Pip — V2
- Brief 4 (dream state) per-Pip — V2; needs per-user calibration data first
- USB key with full Pip stack (Option B) — separate spec when needed
- Hosted Pip service for individuals not associated with a Brian deployment — V3
- Native mobile apps — PWA only for v1
- Multi-device sync within a single Pip — V2

**Explicitly never in scope:**

- Cross-Pip reading
- Pip-to-Deek writing without user consent
- Inferred memory in strict mode
- Selling user data, training on user data without consent, any use of memory beyond serving the user
- Forced staff use (Pip is opt-in for staff; opt-out must be respected without consequence)
- Multi-Pip federation (deliberate omission — see §14)

## 14. Open design questions

Decisions deferred to implementation briefs.

- **Recovery phrase ergonomics.** Backup pairing solves most of the loss case. Whether to recommend the recovery phrase as backup of last resort, employer escrow, both, or neither for non-technical users.
- **Voice biometrics.** Currently authenticate by device, not voice. Whether voice biometrics add real security or just friction.
- **Inference category granularity in adaptive mode.** Whether the six categories from §5.5 are right, or whether more granular control is needed.
- **Pip-to-third-party integrations.** Should Pip be able to read from the user's Google Calendar, email, banking? With explicit consent and category-aware halts, this is genuinely useful. But it's a substantial scope expansion. V2 territory.

**Removed from v0.2's open questions list:** *Multi-Pip federation* — committed to **out-of-scope and unbiased-by**, meaning v1 makes no architectural compromises to enable a future federation feature. If federation is built later, it will be on top of, not at the cost of, v1's isolation guarantees.

## 15. Implementation programme

This spec implies a sequence of implementation briefs. In rough order:

1. **Bug-fix completion (in flight).** Contamination + silent termination. Closes out before Pip work begins.
2. **Voice path completion.** Brief 1a.2 finished properly across all paths.
3. **Tenant-awareness refactor (8-12 weeks).** Per §9.1: schema-pinning connection pool, per-tenant schemas, migration tooling, defense-in-depth assertions. The big foundation piece. Unblocks everything below.
4. **Identity and key management.** User key pairs, signing, verification, recovery, key rotation in PMF.
5. **PMF v1 specification published** — open standard documented in a public repo with examples + validation tooling.
6. **PMF export and import.** Round-trip tested, embedding-model change scenarios covered, key rotation supported.
7. **PMF second-language reference implementation** — separate repository, separate language, round-trips with primary Pip.
8. **Memory write discipline.** Explicit/inferred separation, mode selection, audit trail, capability gating per §5.3, voice ambient-discard per §5.7.
9. **USB key as data carrier.** Encrypted storage, plug-in/unplug behaviour, backup pairing.
10. **Departure flow (§7.4).** End-to-end runbook implemented as code paths + UI + audit logging.
11. **Pip-to-Deek read access.** SECURITY DEFINER functions, per-tenant authorisation.
12. **Pip-to-Deek voluntary contribution.** Share-with-consent affordance, end-to-end.
13. **Per-Pip impressions layer (shadow mode).** Brief 2 plumbing per Pip; data accumulates; not surfaced to user yet.
14. **Onboarding flow + structured baseline-memory interview.** First-conversation experience, naming, mode selection, key generation, ~30 min interview producing 50-100 explicit memory entries.
15. **Privacy controls.** Delete, export, bulk operations, memory inspection view, mode switch.
16. **Routing per user.** API budgets, category-aware halts.
17. **PWA Pip interface.** Adapting existing PWA to Pip context, visual distinction from Deek.
18. **Jo's Pip deployment.** First non-business tenant, used in production, learnings inform staff rollout.
19. **First staff Pip.** Volunteer, ideally Ivan given technical comfort.
20. **Full staff rollout.**
21. **Impressions cutover** (flag flip from shadow → live per Pip).

Briefs 3-4 from the original Deek programme (graph, dream) re-enter as per-Pip features after step 21, applied across all tenants.

**Realistic timeline:**

- Bug-fix + voice completion: in flight, weeks not months
- Tenant-awareness refactor: 8-12 weeks
- Identity + PMF (spec + primary impl + ref impl): 8-10 weeks (parallelisable with later parts of tenant work)
- USB carrier + backup pairing + departure flow: 4 weeks
- Memory discipline + voice ambient-discard: 4 weeks
- Pip↔Deek wiring: 3 weeks
- Onboarding + interview: 4 weeks (parallelisable with PWA work)
- PWA + privacy controls + routing: 4 weeks
- Jo's Pip: month 7
- First staff Pip: month 8
- Full rollout: month 10-11
- Brief 3 onwards re-applies from month 12

Slower than v0.2's estimate because (a) tenant-awareness is sized honestly, (b) PMF reference implementation is added, (c) departure flow + capability table + voice architecture + structured onboarding are scoped properly. The trade is a stronger product with fewer hidden landmines.

### 15.5 Tier-2 role-specific briefs migrate to Pip (NEW)

Today's Deek has role-specific briefs for tier-2 staff (Jo's HR/finance/D2C, Ivan's production/equipment/tech, shipped 2026-04-26). Those briefs currently write to NBNE's organisational memory by default.

**Decision:** when each staff member gets a Pip, their role-specific brief migrates to be Pip-side. The daily question programme becomes their Pip's, not Deek's. The same questions still get asked, but the answers persist to that staff member's personal memory.

Items the staff member wants surfaced organisationally use the share-to-Deek affordance from §10.3 — explicit per-item, not blanket.

**Migration strategy.** The role-specific brief continues to run Deek-side until the staff member's Pip is provisioned. On Pip provisioning:
- The staff member's accumulated brief replies (from before they had a Pip) are exported to PMF and imported into their new Pip
- The Deek-side brief cron is disabled for that user
- The Pip-side equivalent fires daily
- The staff member is informed of the change at handover

This is a substantive change in default behaviour and warrants explicit communication at the time of handover, not a silent migration.

## 16. Success criteria

This spec has succeeded if:

- The first NBNE staff member with a Pip uses it daily without prompting after the first month
- That staff member can articulate, unprompted, why their Pip is different from ChatGPT
- Information flows from staff Pips to Deek occasionally and voluntarily, providing organisational signal that wouldn't otherwise be available
- A staff member who leaves NBNE successfully exports their Pip (PMF format) and imports into a different host, and confirms it works as before — **AND the departure flow §7.4 was followed without ad-hoc intervention**
- The same architecture, with no significant code changes, supports a Brian deployment to a small external customer (target: end of year 2)
- A user in strict mode can audit their entire memory and confirm every entry came from their explicit action
- A user who switches from adaptive to strict can review prior inferences and accept, edit, or delete each
- **No corporate access to user Pip data has occurred outside what users explicitly authorised — verified by audit logs that the user can read directly**
- PMF is documented openly enough that **a second implementation has been built from the spec alone and round-trips successfully** (the v1 reference implementation per §6.7 is the proof)
- **The schema-pinning isolation has been load-tested with deliberate cross-tenant query attempts and rejected at the connection-pool layer** — not just policy-based enforcement

This spec has failed if:

- Staff feel surveilled rather than supported
- The portability claim turns out to be marketing rather than technical reality
- The Pip-to-Deek boundary is breached, accidentally or deliberately
- Memory inferred in adaptive mode cannot be distinguished from explicit memory in the audit view
- The product ends up being indistinguishable from a corporate-monitoring tool with friendly framing
- **A departing staff member's data ownership becomes a per-case negotiation rather than a runbook**
- **A strict-mode Pip with the structured onboarding interview still feels empty after one month — indicating the interview design is wrong**

## 17. The principle

The user owns the AI. The memory is the product. The memory belongs to its owner.

Inference is opt-in. Trust is earned. Sovereignty starts at the individual.

Tenant boundaries are enforced by infrastructure, not policy.

The code stays in Northumberland. The standard is open.
