# Phloe Platform — CLAW Agent Core Context
# Version: 1.0
# Update this file when significant architectural decisions are made.

## What this is
Phloe (formerly Flowan) is a multi-tenant Workflow as a Service (WaaS)
platform for UK micro and small businesses. Django backend + Next.js
frontend, hosted on Hetzner Nuremberg. Each tenant gets an isolated
PostgreSQL database. Domain: phloe.co.uk.

## Non-negotiable rules — never violate these

1. Every queryset MUST filter by request.tenant — no exceptions.
   WRONG: `Booking.objects.all()`
   RIGHT:  `Booking.objects.filter(tenant=request.tenant)`

2. No tenant identifier as a literal string in code.
   No hardcoded slugs, UUIDs, or tenant names anywhere.

3. Every cache key must be prefixed with tenant_id.
   `f'{request.tenant.id}:{cache_key}'` — always.

4. Module feature flag checked at every entry point.
   `if not module_enabled(request.tenant, 'module_key'): return 404`

5. No module touches another module's models directly.
   All cross-module access via module APIs only.

6. Migrations always run as a pair: makemigrations then migrate.
   Never edit migration files manually.

## Domain vocabulary
- Tenant: one client business (e.g. DemNurse, Ian Woods Fire Safety)
- Module: a feature set (bookings, shop, compliance, digest, etc.)
- M-number: NOT used in Phloe (that's the manufacturing app)
- WIGGUM: the micro-loop build methodology for new features
- Ask Floe: on-demand AI opinion feature in the digest module
- Brain: sovereign RAG knowledge engine (planned)

## Architecture
```
backend/          Django — per module in backend/[module_key]/
frontend/src/     Next.js — components in src/components/[module]/
core/             Shared middleware, tenant resolution, utils
```

## Current module registry
customer_facing: website, bookings, shop, crm, disclaimer, cms, community
finance: payments, orders
operations: staff, comms, documents, digest
compliance: compliance

## File structure
```
backend/[module]/models.py        Django models (always has tenant FK)
backend/[module]/views.py         DRF API views
backend/[module]/serializers.py   DRF serializers
backend/[module]/urls.py          URL patterns
backend/[module]/tests/           pytest tests
backend/[module]/manifest.json    Module contract
```

## Common patterns
- New module: follow WIGGUM micro-loop in windsurf_[module].md
- Bug fix: write failing test first, then fix, then verify test passes
- Migration: always makemigrations then migrate, never edit migrations
- Serializer: never trust tenant from request body, always from context

## Current state
- Master build prompt (digest + demo + conversational booking) running
- Shop module upgrade (variants, SEO fields) in progress
- Shopify migration (603 products) pending shop upgrade completion
- Community module designed, not yet built
- Brain module designed, not yet built
- Events module shipped to production (2026-03-30) — Ganbarukai first client
- Ganbarukai Stripe integration pending (Chrissie Howard's account details needed)

## Strategic Decisions

### 2026-03-29 — The Booking Paradigm Insight

**Context**: Client feedback on Phloe across multiple tenants revealed a pattern.
Requests such as "can I attach a PDF download to a class booking" and "can I add a
disclaimer form during checkout" kept arriving from different tenant types. On the
surface these look like bespoke feature requests. They are not.

**Decision**: Phloe is one booking paradigm, not four. Appointment, class, table,
and food ordering are the same state machine with different configuration surfaces.
The pizza QR code and the yoga disclaimer are both workflow attachments that trigger
at defined points in the same underlying booking flow. All future tenant requirements
should be evaluated through this lens before any new code is written.

**Rationale**: A hotel room, a dog grooming slot, a tennis court, a restaurant table —
all resolve to the same crude machinery: availability, selection, customer details,
confirmation, notification. What differs is the attachment type, the trigger point,
and the presentation layer. Building these as configuration rather than code is the
correct long-term architecture.

**Implication for development**: The priority architectural investment is the
*workflow attachment and configuration layer* — a system that lets a tenant describe
their specific requirements (attach this PDF at confirmation, show this disclaimer
before payment, generate this QR code post-booking) without requiring a developer.
This is Phloe's long-term competitive position. The booking engine itself is
commoditised. The configuration layer is not.

**Rejected**: Treating each tenant feature request as a bespoke development task.
This does not scale and creates an unmaintainable codebase of one-off conditionals.

### 2026-03-29 — Conversational Booking AI and Model Training

**Context**: Phloe currently uses pgvector + BM25 + cosine similarity as
deterministic retrieval and classification methods. Real anonymised booking data
is accumulating across tenants. The question was raised: could we train our own
model on this data to handle conversational booking flow?

**Decision**: Yes — this is a viable and strategically important direction.
Target: a fine-tuned small model (Qwen 2.5 3B or similar) trained on anonymised
Phloe booking interactions to handle conversational booking flow natively.

**Architecture**:
- pgvector / BM25 / cosine similarity stack handles *retrieval* — finding relevant
  tenant config, availability, pricing. This stays and is not replaced.
- The fine-tuned model handles *generation* — understanding booking intent
  conversationally, guiding users through the flow in natural language.
- The two layers are complementary. Retrieval feeds context to the generative model.

**Data requirements**: Fine-tuning a 3B model requires approximately 2,000–5,000
high-quality labelled examples to produce meaningful results. Sources:
  1. Anonymised real booking flows from Phloe tenants (primary)
  2. Feedback widget submissions — intent + resolution pairs
  3. Synthetic augmentation generated from real patterns to fill sparse categories

All training data must be anonymised before use. No PII, no tenant-identifiable
information. This is a condition, not a preference.

**Hardware**: Fine-tuning a 3B model locally is viable on a single RTX 3090 24GB.
Estimated compute: hours, not days. This is not a data centre job.

**Rationale**: A Phloe-native conversational model understands booking context that
a generic LLM does not — tenant-specific terminology, workflow attachment triggers,
paradigm-specific edge cases. A generic model can approximate this with prompting.
A fine-tuned model internalises it. The data to do this exists and is growing.

**Rejected**: Relying solely on prompt engineering with a generic model for
conversational booking. This works at small scale but does not compound over time
the way a fine-tuned model on real data does.

**Next steps** (when ready — not current priority):
- [ ] Define anonymisation pipeline for Phloe booking data
- [ ] Audit feedback widget submissions for training signal quality
- [ ] Evaluate Qwen 2.5 3B vs Phi-3 mini as base model for fine-tuning
- [ ] Design labelling schema for booking intent classification
- [ ] Scope synthetic data augmentation approach
- [ ] RTX 3090 required before starting — confirm hardware before scheduling

### 2026-03-30 — Events Module (One Booking Paradigm in Action)

**Context**: Chrissie Howard (Ganbarukai martial arts) requested an events calendar
for gradings, workshops, competitions, and open days. This is the first concrete
application of the "one booking paradigm" insight from 2026-03-29.

**Decision**: Events are implemented as a new model (Event) with a nullable FK on
Booking, sharing the existing booking + Stripe payment flow. Not a separate system.
Event bookings go through the same BookingViewSet.create() with an `event_id` branch
that auto-resolves service, staff, and pricing from the event record.

**Architecture**:
- `bookings/models_events.py` — Event model (tenant-scoped, capacity, pricing, dates)
- `bookings/views_events.py` — EventViewSet (CRUD) + public_events (listing)
- `bookings/serializers_events.py` — annotates booked_count, spots_remaining
- Frontend: admin CRUD at /admin/events/, public "Events" tab on /booking page
- URL prefix: `tenant-events` (avoids collision with `api/events/log/` audit log)

**Deployment lessons learned**:
1. **Two repos**: `nbne_platform` is development, `nbne_production` is live.
   Ganbarukai runs on production. Never push to platform expecting live deployment.
2. **Codebase divergence**: Production has GYM_BRANDS, AdminLayoutClient, disclaimer
   flow, orders module. Variable names differ (`accent` vs `_accent`). Always read
   the production file before modifying — never assume it matches platform.
3. **Migration safety**: Generating migrations without a live DB picks up ALL pending
   model diffs. Migration 0026 initially included RemoveField, AddField, AlterField
   on unrelated models, crashing Django on startup and taking down the entire backend.
   Fix: hand-edit the migration to contain only the intended operations.
4. **Docker cache**: Failed frontend builds prevent container replacement. The backend
   keeps running old code until a successful build. Multiple failed deploys compound.
5. **GitHub Actions guard**: The `if: github.repository == 'NBNEORIGIN/nbne_production'`
   condition was silently skipping all deploys. Removed.

**Implication**: The one booking paradigm works. Event bookings share the payment flow,
capacity checking, and client creation logic. The next booking type (e.g. room hire,
course enrolment) should follow this same pattern: new model, FK on Booking, branch
in BookingViewSet.create().
