# CAIRN_COWORK.md
# Context file for Claude Cowork sessions
# North By North East Print & Sign Ltd, Alnwick, Northumberland
# Last updated: 29 March 2026

---

## Who You Are Working With

**Toby Fletcher** — Co-Director, NBNE. CEng MIMechE. Sets direction and priorities.
Not a professional developer. Communicates plainly and expects the same back.

**Jo Fletcher** — Co-Director, NBNE. Day-to-day operations and client relationships.

**The team**: Ivan, Gabby, Ben, Sanna.

---

## What NBNE Is

North By North East Print & Sign Ltd is a sign fabrication and print business based
in Alnwick, Northumberland. It operates across three areas:

1. **Commercial signage** — fabrication, installation, structural engineering sign-off.
   Toby holds CEng MIMechE credentials which gives NBNE a capability (structural
   calculations, wind loading, NDT inspection) that most sign companies cannot offer.

2. **E-commerce** — high-volume generic and personalised signage products on Amazon,
   Etsy, and eBay. Strategic direction is toward generic products for non-linear
   scaling efficiency.

3. **Software** — NBNE builds its own software. This is not a side project. It is
   a core part of the business strategy. See projects below.

---

## The Software Portfolio

### Cairn — Sovereign AI Development System
NBNE's in-house AI coding agent. Runs on NBNE hardware in Alnwick. Persistent memory
across all development sessions. Claude Code is the principal developer. Qwen and
DeepSeek are junior developers. No code leaves the building except deliberate API calls.
The memory is the product. The code stays in Northumberland.

### Phloe — WaaS Booking Platform
A multi-tenant white-label booking platform hosted on Hetzner. Four paradigms:
appointment (DemNurse), class/timetable (Ganbaru Kai), table reservation (Tavola),
food ordering (Pizza Shack). Built in Django / Next.js. Active development.
Next priority: locale-awareness and international expansion readiness.

Strategic insight: all booking paradigms are the same state machine with different
configuration surfaces. The competitive position is the workflow attachment and
configuration layer, not the booking engine itself.

### CRM
Fully built and functional. Could do with improvement. Path TBC — confirm with Toby.

### Signmaker (confirmed name: Render)
The most important piece of software NBNE has developed. AI-driven, semi-automated
small-format signage product design and publishing system. Takes a product concept
through to live listings on Amazon, Etsy, eBay, and the NBNE website.
Path and GitHub repo TBC — confirm with Toby.

### Studio (concept stage)
Lifestyle product image and video generation using ComfyUI and FLUX. Feeds into
Signmaker. Requires dedicated GPU. Downstream of Signmaker — not current priority.

### Bookkeeping App
Greenfield. Stack not yet decided. Not started.

### Client Static Sites
Three sites under maintenance:
- houseofhairalnwick.co.uk
- clayportjewellers.co.uk
- a1g.co.uk

Each has its own GitHub repo and Google Drive folder for assets.

---

## The Business Brain

Cairn has two goals:

**1. Business memory** — institutional knowledge that survives any session, tool,
or model. Every development decision, solved problem, and rejected approach indexed
and retrievable. This is running now and improves every week.

**2. Business brain** — cross-domain reasoning over the entire business. Not just
code. The brain answers questions like: what should we make today, are we making
money doing it, and are we reaching the right people to sell it?

**The value chain**: Make → Measure → Sell

- **Manufacture** — make list, stock levels, machine availability
- **Ledger** — cash position, margins, revenue by channel
- **Marketing** — ad spend, ROAS, CRM pipeline, Phloe bookings

When all three module context endpoints are live, Cairn assembles them into a
daily business state snapshot and produces prioritised recommendations.

Hardware dependency: the brain requires dual RTX 3090 (48GB VRAM). The context
endpoints are being built now. The brain runs when the hardware is ready.

---

## Current Priorities (as of 29 March 2026)

1. RTX 3090 arriving imminently — pull Qwen 32b and DeepSeek 16b models on arrival
2. Fix git_commit tool mapping in Cairn (resolves to git_add — see CAIRN_PROTOCOL.md)
3. Confirm and register missing project paths (CRM, Signmaker)
4. Phloe locale-awareness — compliance packs, tenant locale config, Django i18n

---

## How Toby Works

- Pragmatic and tool-oriented. Wants things that work, not things that are clever.
- Communicates in plain English. Expects the same back — no unnecessary jargon.
- Structured workflow: tasks broken into sections, committed atomically.
- Values sovereignty — data and code staying on NBNE hardware is a principle, not
  a preference.
- Background in pipeline integrity engineering (BS 7910, wind loading). Applies
  engineering rigour to software decisions.

---

## Key Files to Read for Deeper Context

- `CAIRN_PROTOCOL.md` — full developer protocol, project registry, memory system
- `projects/phloe/core.md` — Phloe domain context and decision log
- `projects/claw/core.md` — Cairn's own decision log

---

## The Principle

Cairn gets more useful the more it is used. Every session that writes back makes
the next session faster. Every decision captured is a mistake not repeated.
The memory is the product. The code stays in Northumberland.
