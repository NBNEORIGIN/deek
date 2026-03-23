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
