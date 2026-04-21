# CRM Brief — Project folder column + PATCH endpoint

**Target repo:** CRM (`D:\crm` / `NBNEORIGIN/crm`)
**Module:** CRM
**Consumer:** Claude Code (CRM session — NOT Deek)
**Protocol:** Follow `NBNE_PROTOCOL.md`.
**Originating work:** Deek Triage Phase C (branch `feat/triage-phase-c-crm-folder-endpoint`)

---

## Why this brief exists

Deek's email triage Phase B is sending `project_folder_path` values
(local disk paths where NBNE signage projects live on Toby's
workstation) as free-text inside `/api/cairn/memory` note bodies.
That works, but the path should be a first-class field on the
`Project` model so:

- The folder path is visible in the CRM admin UI alongside the
  project record
- Future CRM queries can filter / sort by whether a project has a
  folder mapped
- Deek's next phase (similarity surfacing) can use folder presence
  as a "project is tracked" signal

Focused spanning brief: schema + API + minimal admin-UI surface.

---

## Pre-flight self-check

Before writing any code:

1. Read `CLAUDE.md` at the repo root to confirm scope + existing
   patterns for Prisma + API routes.
2. Confirm the `Project` Prisma model's current field set and any
   existing migrations under `prisma/migrations/`.
3. Inventory `/api/cairn/*` existing endpoints. `/api/cairn/memory`
   already exists (accepts POST for notes) — confirm it's the
   canonical surface for Deek-to-CRM writes.
4. Confirm the admin UI list + detail views for `Project` so you
   know where the new field should render.
5. Report findings before Task 1. Do not proceed until Toby
   acknowledges.

---

## Tasks

### Task 1 — Schema

Add to the `Project` Prisma model:

```prisma
localFolderPath  String?   // absolute path on Toby's workstation,
                           // e.g. "D:\\NBNE\\Projects\\M1234-flowers-by-julie"
```

Nullable. No index (low cardinality query pattern). Generate a
migration named `2026_04_21_project_local_folder_path`.

### Task 2 — API endpoint

Decide between:

**(a)** Extend `/api/cairn/memory` to recognise a new `type` value
(`"project_folder_update"`) that sets `Project.localFolderPath`
rather than creating a `Note` row.

**(b)** Add a new endpoint `PATCH /api/cairn/projects/{id}/folder`
taking `{ localFolderPath: string }` in the body.

**Recommend (b)** — clean separation, doesn't overload the memory
endpoint, gives Deek a cheap 404 probe to detect whether the new
surface is live.

Auth: same Bearer-token check as every other `/api/cairn/*` endpoint.

Validation:
- Path is stripped whitespace, max 500 chars
- Empty string clears the field
- No path format validation (Toby's paths are Windows-style; the
  CRM server runs Linux, don't assume POSIX)

Response shape:
```json
{ "id": "...", "localFolderPath": "...", "updatedAt": "..." }
```

On unknown project id: `404` with `{"error": "project_not_found"}`.

### Task 3 — Admin UI

In the `Project` detail view (admin panel, NOT the customer-facing
CRM UI), add a "Project folder" row showing `localFolderPath` when
set. Editable inline — same pattern as other project fields. Read at
minimum; editable if the existing project-edit form supports it with
minimal effort. If editability is non-trivial, ship read-only and
flag for a follow-up.

No UI in the customer-facing app. This is an internal field.

### Task 4 — Memory / write-back

Standard `update_memory(...)` call per `NBNE_PROTOCOL.md` Step 4.
The `decision` field should note which API shape (a) or (b) you
chose and why.

### Task 5 — Tests

- Unit: Prisma migration applies cleanly
- Integration: PATCH succeeds on valid project, 404s on unknown
  project id, empty string clears the field
- Regression: the existing `/api/cairn/memory` POST for notes
  continues to work unchanged

### Deliverable

A single PR on the CRM repo with:
- migration
- route handler
- admin UI change (or flagged follow-up)
- tests green

---

## Deek-side readiness (already shipped)

The Deek repo already has the companion change in
`feat/triage-phase-c-crm-folder-endpoint`. When Toby replies to a
triage digest with a project folder path, Deek calls:

```
PATCH https://crm.nbnesigns.co.uk/api/cairn/projects/{id}/folder
Authorization: Bearer <DEEK_API_KEY>
Content-Type: application/json

{"localFolderPath": "D:\\NBNE\\Projects\\..."}
```

Fallback today: on 404/405 the folder path folds into the existing
`/api/cairn/memory` note body, same as Phase B. Zero Deek-side
deploy needed once this CRM PR merges — the next triage reply
auto-detects the new endpoint.

---

## Out of scope

- Deek-side code. Do not edit anything in the Deek repo.
- Cross-module data migrations. No bulk backfill from existing Deek
  notes — Toby has ~15 projects with folder paths today at most;
  he'll confirm them case-by-case.
- Similarity / graph surfacing. That's Deek Phase D.

---

## Constraints

- No breaking changes to existing `/api/cairn/*` endpoints
- No new cloud dependencies
- `localFolderPath` is plain text — no validation that the path
  exists (it's on Toby's workstation, not the CRM server)
- Admin UI only — never expose this field to customer-facing pages

---

## Rules of engagement

You stay in the CRM repo. You do not touch Deek, the Phloe booking
app, or any other module. If something requires coordinated changes
across repos, stop and write a second spanning brief for that — do
not silently expand scope.
