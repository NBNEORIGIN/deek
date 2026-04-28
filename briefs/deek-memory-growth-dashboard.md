# Deek memory-growth dashboard

**For:** fresh Claude Code session against the Deek repo (`D:\claw\`)
**Estimate:** half a day to a day
**Triggered by:** Toby asked for visible "is the business context growing as it should?" telemetry on 2026-04-28. The numbers exist (`claw_code_chunks.indexed_at`, `schemas.derived_at`, `memory_brief_responses.received_at`); what's missing is a queryable surface to see them rather than ad-hoc psql.

---

## Read first (in order)

1. `CLAUDE.md` — Deek agent scope (additive endpoint + new admin route, both confirmed in-scope; no spanning brief)
2. `api/main.py` — see how `/api/deek/...` routes are mounted (look for `app.include_router(...)` block ~line 3395 onwards). Pattern is `APIRouter(prefix='/...')` then `include_router(router, prefix='/api/deek')`
3. `api/middleware/auth.py` — `verify_api_key` Depends; reuse it
4. `api/routes/quotes.py` — short example of the router-with-prefix pattern, mirror this shape
5. `api/routes/admin.py` — existing admin routes; new endpoint can land alongside
6. `web/src/app/admin/` — existing admin UI surfaces; new page goes here
7. `web/src/lib/auth.ts` — JWT session shape with `role: ADMIN | PM | STAFF | …`; the new admin page must gate on `session.role === 'ADMIN'`

Then look at the actual schemas:
- `migrations/postgres/0001_impressions_layer.sql` — `claw_code_chunks` columns + `schemas` table
- `migrations/postgres/0005_memory_brief.sql` — `memory_brief_runs` + `memory_brief_responses`

---

## What you're building

Two artefacts:

### Backend — `GET /api/deek/admin/memory-growth`

Query string:
- `days` (int, default 30, max 365)
- `project_id` (optional, repeatable; defaults to all)

Response shape:
```json
{
  "as_of": "2026-04-28T16:30:00Z",
  "window_days": 30,
  "totals": {
    "chunks": 43871,
    "schemas": 142,
    "brief_replies": 18,
    "projects": 13
  },
  "projects": [
    {
      "project_id": "deek",
      "current_chunks": 6334,
      "daily_series": [
        {"date": "2026-03-30", "chunks_added": 42, "cumulative": 5876},
        ...
        {"date": "2026-04-28", "chunks_added": 47, "cumulative": 6334}
      ]
    },
    ...
  ],
  "schemas": {
    "current_count": 142,
    "daily_series": [
      {"date": "2026-03-30", "added": 1, "cumulative": 117},
      ...
    ]
  },
  "brief_replies": {
    "current_count": 18,
    "daily_series": [...]
  }
}
```

Implementation notes:
- Use `generate_series(CURRENT_DATE - (days || ' days')::interval, CURRENT_DATE, '1 day')` for the date axis so days with zero activity still show.
- LEFT JOIN against the chunks/schemas/responses tables grouped by `(indexed_at::date, project_id)`.
- Cumulative is computed in SQL with a window function: `SUM(daily_count) OVER (PARTITION BY project_id ORDER BY date)`.
- All three queries should be parallelisable but sequential is fine for now — total runtime should be <1s on Hetzner.
- **Index needed:** `claw_code_chunks` currently has no index on `indexed_at` (verified on Hetzner 2026-04-28: only `pkey`, `idx_chunks_project`, `idx_claw_chunks_project`, `idx_claw_chunks_embedding`, `ix_chunks_salience`, `ix_chunks_last_accessed`, and the two `salience_signals` partial indexes exist). Add a new migration `migrations/postgres/00NN_chunks_indexed_at_index.sql` with `CREATE INDEX IF NOT EXISTS ix_chunks_indexed_at ON claw_code_chunks (indexed_at DESC);` — without it, the date-bucketed scan will be a sequential scan over 43k+ rows on Hetzner today and worse later.
- Auth: `verify_api_key` Depends, mirroring every other `/api/deek/admin/*` endpoint.
- Land the route in `api/routes/admin.py` (existing file) under a new `@router.get('/memory-growth')` handler, OR a new `api/routes/memory_growth.py` mounted under `/api/deek/admin`. Prefer the new file for testability.

### Frontend — `/admin/memory-growth` page

Layout (single scrollable page):

1. **Header strip** — total chunks, total schemas, total brief replies, last 30 days new memory count. Big numbers, like `BriefingView`'s emerald cards.

2. **Filter row** — time-window selector (7 / 30 / 90 / 365), project multiselect, refresh button.

3. **Stacked area chart** — chunks added per day, stacked by project. Width 100%, height ~280px. Use [recharts](https://recharts.org/) — add to `web/package.json` and regenerate the lock file (don't make the same lock-file mistake we made on 2026-04-28).

4. **Line chart** — schemas cumulative + per-day added on the same axes (dual y-axis). 280px.

5. **Bar chart** — brief replies per day for the window. 200px.

6. **Project breakdown table** — sortable: project_id, current chunks, last 7 days added, last 30 days added, last 90 days added, oldest chunk date, newest chunk date.

UX detail: empty days should still render as zero (gaps mislead). Hover tooltips on every chart. Use the existing slate-950/emerald-700 palette to match `/voice/brief`.

### Auth wrapper

The page route at `web/src/app/admin/memory-growth/page.tsx` must redirect to `/voice/login?callbackUrl=/admin/memory-growth` if `session.role !== 'ADMIN'`. Check how `web/src/app/admin/staff/page.tsx` (existing) gates — mirror it.

The Next.js proxy at `web/src/app/api/admin/memory-growth/route.ts` must also gate on session role; do not trust client filters.

---

## Critical constraints

- **Don't query `claw_code_chunks` without an `indexed_at` filter.** That table has 43k+ rows; an unfiltered scan + group is fine for Hetzner today but will get slower. Bound every query by the requested window.
- **Don't add real-time subscriptions / SSE.** This is a "open and look" surface, not a live dashboard. Refresh button is enough.
- **Don't include cost_log data** in this endpoint — there's a separate cost surface elsewhere; folding cost in muddles the "is memory growing" question.
- **`/admin/memory-growth` is not for Jo.** Toby-only (ADMIN role). On Rex (jo-pip) the page exists but Jo's session has `ADMIN` too — fine, she can see her own memory growth on her own instance. But on the shared Toby instance, anyone signed in shouldn't see this without ADMIN.

---

## Tests (backend only)

Land in `tests/test_memory_growth_endpoint.py`. Use the existing FastAPI `TestClient` + fake-DB pattern (see `tests/test_brief_pwa.py` for the `_FakeConn` helper). Cases:

1. **Empty DB returns zeros.** Mock cursor returns `[]`; assert all daily_series are zero-filled.
2. **`days` parameter is honoured** — request `?days=7`, assert response `window_days == 7`.
3. **`days` clamped at 365** — request `?days=10000`, assert 400 with detail `days_too_large` (or clamp to 365 with a note in metadata; pick one).
4. **Auth required** — request without `X-API-Key`, assert 401.
5. **Per-project filtering** — request `?project_id=deek`, assert response only has that project.
6. **Cumulative monotonic increase** — assert that for each project's daily_series, `cumulative[i+1] >= cumulative[i]`.

Frontend tests are out of scope for this brief — Toby will eyeball it.

---

## Out of scope for this session

- **Charts on the `/voice/brief` page** for Jo. The dashboard is admin-only; Jo's surface stays uncluttered.
- **A nightly snapshot table.** Toby could ask for this later (so the time series is cached rather than computed live), but for current data volumes the live query is fast enough.
- **Cost over time** (separate concern; covered by the existing cost log).
- **Schema graph visualisations** (the dream-state crosslink graph already has a separate UI).
- **Export to CSV / PNG.** Browser print works.

---

## Definition of done

1. `GET /api/deek/admin/memory-growth?days=30` returns the documented shape with real data, runs in <1s on Hetzner.
2. 6 backend tests pass; the existing brief test suites also still pass.
3. `/admin/memory-growth` renders the four sections (header, filters, three charts, table) with real data and looks at-home next to the existing admin UI.
4. ADMIN-role gating verified — non-ADMIN session is redirected to login.
5. Recharts added to `web/package.json` AND `web/package-lock.json` regenerated (verify with `npm ci` from a clean `node_modules`).
6. After deploy on Hetzner: load `https://deek.nbnesigns.co.uk/admin/memory-growth`, sanity-check that totals match `psql -c "SELECT count(*) FROM claw_code_chunks"`.

When done: update `briefs/deek-memory-growth-dashboard.md` with a completion note + commit hash.

---

## Confirm before starting

- Validator clean: `python scripts/validate_brief.py briefs/deek-memory-growth-dashboard.md`
- Deek API reachable: `GET http://localhost:8765/health`
- Pull memory: `retrieve_codebase_context(query="admin endpoint memory growth", project="deek", limit=5)`
- If anything in the data shape or auth wrapping is unclear, flag it before writing code — chart UIs and time-series math are easy to get subtly wrong.
