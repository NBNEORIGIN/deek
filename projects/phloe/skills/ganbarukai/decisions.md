# Ganbarukai — Decision Log
# Client: Ganbaru Kai Karate (Chrissie Howard)
# Slug: northumberland-karate
# Domain: ganbarukai.co.uk
# Ports: backend 8005, frontend 3005
# Stripe: live (acct_1S8fu42as3jCW0s5)

---

## 2026-04-06 — Ledger Lite Module Deployed

**Context**: Ganbarukai wanted expense tracking and receipt scanning integrated into
the admin panel. The `ledger_lite` module was already built in `nbne_platform` but
not yet deployed to any live client.

**Decision**: Enable `ledger_lite` for Ganbarukai as the first live client for
this module.

**Changes made**:
- Added `ledger_lite` to `enabled_modules` in tenant config
- Added Finances nav entry to `AdminLayoutClient.tsx` in `nbne_platform`:
  `{ href: '/admin/finances', label: 'Finances', icon: '💷', module: 'ledger_lite' }`
- Rebuilt frontend container

**Current state**: `/admin/finances` loads correctly. Scans: 0/20 (OCR quota).
P&L Dashboard available at `/admin/finances/pnl`.

---

## 2026-04-06 — Babel Workaround Applied to Frontend Build

**Context**: Forcing a `--no-cache` rebuild (required after source changes) exposed
a pre-existing SWC JSX parser regression in Next.js 14.2.x. The old Docker cache
had been masking it.

**Decision**: Added `frontend/.babelrc` to `nbne_platform` to force Babel compilation
for all client builds. Two underlying JSX bugs were also fixed:
- `book/page.tsx`: missing fragment wrapper in ternary sibling JSX
- `GymBookingFlow.tsx`: IIFE computing `gcalUrl` moved before the JSX `return`

**Status**: All client sites that rebuild from `nbne_platform` will use Babel going
forward. This is a platform-wide change, not Ganbarukai-specific.

**See also**: `phloe-deployment/decisions.md` for full Babel configuration rationale.

---

## 2026-03-30 — Events Module + Podcast/Blog Self-Service Deployed

**Context**: Ganbarukai needed event listings (grading days, competitions, seminars)
and wanted to manage blog/podcast content themselves.

**Decision**: Events module shipped as first live client. Blog and CMS pages
enabled for self-service content management.

**Status**: Live on ganbarukai.co.uk.

---

## Pending

- **Stripe**: Live keys being confirmed with Chrissie Howard (acct_1S8fu42as3jCW0s5
  confirmed, webhook and products pending)
- **Club Merch**: Shop module live but no products yet added by client
- **Podcast**: Blog/CMS enabled, client to add content
