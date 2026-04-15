# Beacon — Google Ads Attribution Module

Beacon is NBNE's Google Ads integration and attribution system. It connects Google Ads spend to orders via a tracking pipeline, enabling cost-per-acquisition measurement across signage product categories.

## Status

**Phase 1 complete as of 2026-04-15.** Scheduler cycle verified against a live Celery beat + solo worker + Redis broker for one full 15-minute interval; both `beacon_sync_tenants` and `beacon_upload_conversions` fired on cadence and wrote `beacon_job_run` rows with `outcome=success`. See `projects/beacon/core.md` D-008 for the full evidence record.

Deployed at D:\beacon, ports 8017/3017.
GitHub: NBNEORIGIN/beacon

### Status changelog

- 2026-04-15 — Phase 1 scheduler cycle verified end-to-end against local Redis; D-008 logged. Phase 1 now genuinely COMPLETE (prior 2026-04-07 claim was premature — Redis was never run, only eager mode).
- 2026-04-07 — Phase 1 code landed (commit b0f3791). Acceptance criterion deferred.

## Architecture

Django 5.2 + DRF backend + Next.js frontend. `google-ads` 30.0.0 pinned to Google Ads API v20. Celery 5.6 + Redis broker for the scheduler (declarative `CELERY_BEAT_SCHEDULE`, gated by `BEACON_SCHEDULER_ENABLED`). Per-row database encryption via Fernet (`core/crypto.py`, `BEACON_ENC_KEY` env var). PostgreSQL on port 5432.

## Scheduler

Three Celery beat entries gated on `BEACON_SCHEDULER_ENABLED=true`:

- `beacon_sync_tenants` — every 15 min (pulls tenant list from Phloe)
- `beacon_upload_conversions` — every 15 min (processes attribution queue)
- `beacon_smoke_campaigns` — every 60 min, pinned to sub-account `2028631064` (heartbeat + campaign cache refresh; MCC `2141262231` is never scheduled per D-005)

Each run writes a `beacon_job_run` row. `GET /api/cairn/context` exposes `last_success` / `last_failure` timestamps per job.

## Google Ads OAuth

OAuth credentials are stored in `.env` only — never in wiki or source files. The redirect URI for the OAuth callback is `/oauth/google-ads/callback/`. Credential rotation: contact the developer if Google OAuth access is lost.

## Open Phase 1 housekeeping

- ~~Run Celery beat + worker against live Redis for one 15-min cycle (scheduler acceptance criterion)~~ — **done 2026-04-15, D-008**
- Phloe tenant API endpoint contract (separate session in the Phloe module)
- CRM / Manufacture webhook secret configs (separate sessions in those modules)
- Admin UI auth hardening
- `cairn_app.views.context_endpoint` DRF auth bug — global `JWTAuthentication` shadows the custom Bearer check; fix with `authentication_classes = []` on the view (flagged in D-008, not blocking Phase 1)

## Links

- [[wiki/modules/cairn]] — Cairn manages Beacon as a registered module
