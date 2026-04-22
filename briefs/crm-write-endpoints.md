# CRM Brief — Richer write endpoints for Deek

**Target repo:** CRM (`D:\crm` / `NBNEORIGIN/crm`)
**Module:** CRM
**Consumer:** Claude Code (CRM session — NOT Deek)
**Protocol:** Follow `NBNE_PROTOCOL.md`.
**Originating work:** Deek chat-write tools (this repo, PR #??)

---

## Why this brief exists

Deek already wraps the three write endpoints the CRM exposes today
(`POST /api/cairn/memory`, `PATCH /api/cairn/memory`,
`PATCH /api/cairn/projects/{id}/folder`). That covers "record an
observation" but nothing else. The common writes Deek would actually
want to do in chat — add a note to a project, move a project's
stage, update its quoted value, append a lesson learned — still
require Toby to open the CRM admin UI.

This brief adds the smallest set of endpoints that closes the 80 %
case.

---

## Pre-flight self-check

1. Read `CLAUDE.md` and `DEEK_MODULES.md` for the current API
   contract + auth pattern.
2. Confirm the Prisma models for `Project`, `ProjectNote` (or
   equivalent — a per-project note table), and `Client`.
3. Inventory what the existing `/api/cairn/*` endpoints do so the
   additions stay consistent in auth, response shape, and error
   conventions.
4. Report findings before Task 1.

---

## Tasks

### Task 1 — POST /api/cairn/projects/{id}/notes

Append a free-text note to a project.

Body:
```json
{ "text": "...", "source": "deek_chat" }
```

Response 201:
```json
{
  "id": "<note-id>",
  "project_id": "...",
  "text": "...",
  "source": "deek_chat",
  "created_at": "..."
}
```

- Max 5000 chars on `text`.
- `source` defaults to `"api"` — used so the CRM UI can render
  Deek-authored notes with a little 🤖 marker.
- 404 on unknown project id.

If there is no existing `ProjectNote`-style table, create one with
minimal columns (id, project_id, text, source, created_at) and the
usual indexes. A migration `2026_04_YY_project_notes` is fine.

### Task 2 — PATCH /api/cairn/projects/{id}

Accept a partial update. Body fields are all optional — missing
keys leave the column unchanged:

```json
{
  "stage": "NEGOTIATING",
  "value": 2850.0,
  "next_action": "Send formal quote by Fri",
  "waiting_on_party": "client",
  "close_probability": 0.6,
  "status_note": "..."
}
```

Any field the `Project` model has today that changes during the
pipeline is in scope here. Validation mirrors whatever the admin
form does — don't invent new constraints.

Returns the updated project row (minus heavy relations). 404 on
unknown id.

### Task 3 — POST /api/cairn/lessons

Create a LessonLearned entry.

Body:
```json
{
  "title": "...",
  "summary": "...",
  "tags": ["qa", "mitre"],
  "source_project_id": "...",
  "created_by": "deek"
}
```

Response 201 with the row.

If the existing LessonLearned model has richer fields (category,
confidence, etc.) set defaults server-side — Deek won't know those.

### Task 4 — Auth

Same Bearer-token check as every other `/api/cairn/*` endpoint.
Reject without it (401) — don't fall back to NextAuth session.

### Task 5 — Tests

- Unit: each endpoint handles missing/invalid fields cleanly
- Integration:
    - Full round-trip: POST note → GET note back via search
    - PATCH stage: verify the project row reflects it
    - POST lesson: verify search_crm / LessonLearned surface picks
      it up
- Regression: existing `/api/cairn/memory` + search + folder
  endpoints unchanged

### Deliverable

Single PR on the CRM repo with migration (if Task 1 needs it),
three route handlers, permission glue if any, tests green.

---

## Out of scope

- Deek-side code. Deek already has the tool wrappers ready; they
  currently call the new endpoints and return "not available yet"
  on 404. When your PR merges the next call succeeds — zero
  Deek-side deploy needed.
- UI surfaces for any of this — admin panel work is a separate
  brief if wanted.
- Cross-module writes (e.g. creating a Phloe booking from a CRM
  project). Separate spanning briefs.

---

## Constraints

- No breaking changes to existing endpoints
- Max 5000 chars on any free-text field
- Owner-level audit trail on every write — `created_by` /
  `updated_by` column populated from the Bearer-token identity or
  explicit payload field
- Bearer-auth only (no NextAuth fallback)

---

## Rules of engagement

Stay in the CRM repo. Do NOT touch Deek, Phloe, or any other
module. If anything here needs coordinated changes across repos,
stop and write a second spanning brief — do not silently expand
scope.
