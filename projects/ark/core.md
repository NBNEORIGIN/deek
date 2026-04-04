# Phloe Ark — core.md

## Purpose

Ensure that if the Hetzner production server (178.104.1.152) is completely destroyed,
every Phloe client site can be restored to a known recent state (< 24 hours data loss)
on a fresh server within hours, not days. Zero client data loss is the goal;
< 24 hours is the minimum acceptable standard.

## Infrastructure Context

**Critical:** Phloe runs in Docker. Every tenant is a separate Docker Compose stack.
This has direct implications for backup mechanics:

- Databases are NOT accessible as a system Postgres user. Each tenant's database runs
  inside its own container: `<slug>-db-1`. pg_dump must be run via `docker exec`:
  ```bash
  docker exec <slug>-db-1 pg_dump -U nbne -d <slug> | gzip > <slug>_YYYY-MM-DD.sql.gz
  ```
- Database names are the tenant slug directly — NOT prefixed with `phloe_`.
  e.g. `demnurse`, `northumberland-karate`, `amble-pincushion`
- Media files are in Docker named volumes (`<slug>_media`), not filesystem paths like
  `/var/www/*/media/`. Extract via:
  ```bash
  docker run --rm -v <slug>_media:/data -v /backups/ark:/out alpine \
    tar czf /out/<slug>_media_YYYY-MM-DD.tar.gz -C /data .
  ```
- Environment files: `/opt/nbne/instances/<slug>/.env` — back these up (encrypted).
  They contain Stripe keys, email credentials, DB passwords. Never push these to B2 unencrypted.
- Compose file: `/opt/nbne/shared/docker/docker-compose.client.yml` (shared)
  Override per tenant (where present): `/opt/nbne/instances/<slug>/docker-compose.production.yml`
- Tenant discovery: enumerate `/opt/nbne/instances/` — each subdirectory is a tenant slug.
  Do NOT hardcode the tenant list. Discover dynamically.

## Architecture Overview

### Layer 1: Automated Daily Backup

A cron job (systemd timer acceptable) on the Hetzner production server running daily
at 03:00 UTC:

#### Per tenant (iterate `/opt/nbne/instances/`):
1. `docker exec <slug>-db-1 pg_dump -U nbne -d <slug>` → compressed SQL
2. Docker volume dump of `<slug>_media` → compressed tar
3. Copy of `/opt/nbne/instances/<slug>/.env` → encrypted copy
4. Bundle into a single archive per tenant: `<slug>_YYYY-MM-DD.tar.gz`

#### Platform-level:
5. Backup of nginx configs (`/etc/nginx/sites-available/`)
6. Backup of SSL certificates — Let's Encrypt at `/etc/letsencrypt/`, Cloudflare origin
   certs at `/etc/ssl/cloudflare/`
7. Git commit hash of each deployed repo (production and shared):
   - `/opt/nbne/production` → `git rev-parse HEAD`
   - `/opt/nbne/shared` → `git rev-parse HEAD`
8. Manifest file: `manifest.json` with timestamp, tenant list, row counts, git hashes

#### Encryption:
- Use `age` (https://github.com/FiloSottile/age) — simpler than GPG
- Encrypt `.env` files before they leave the server
- Full archive encryption in Phase 5 — Phase 1 uses local-only unencrypted dumps
  (better than nothing; encryption added in Phase 5 before off-site push)

#### Off-site storage (Phase 2+):
- Primary: Backblaze B2 (provider isolation from Hetzner)
- Secondary (optional): Hetzner Storage Box (BX11 €3.81/month, fast restore)
- Upload via `rclone` (supports both natively)

#### Retention policy:
- 7 daily, 4 weekly (Sunday), 3 monthly (1st of month)
- Pruning via rclone or a simple date-comparison script

#### Monitoring:
- On success: append to `/var/log/ark-backup.log`
- On failure: email to `toby@nbnesigns.com` (use msmtp or mailutils — check availability)
- Health check cron: verify most recent backup exists and is < 26 hours old

---

### Layer 2: Human-Readable CSV Export

Daily CSV export per tenant (Phase 4):
- Bookings, Customers, Products/Services, Staff, Configuration summary
- One directory per tenant, one CSV per table, plus `manifest.json`
- Purpose: GDPR break-glass, manual reconstruction if SQL dumps fail

---

### Layer 3: Re-Provisioning Script (The Ark Script)

Single script (Phase 3):
```bash
./ark-restore.sh --backup /path/to/backup/2026-03-30/ --server <fresh-ip>
```

Sequence:
1. Install stack: Docker, nginx, certbot, rclone on fresh Ubuntu 24
2. Clone production repo at recorded git hash
3. Per tenant: recreate Docker stack, restore DB via `docker exec psql`, restore media volume
4. Restore nginx configs and SSL certificates
5. Restore `.env` files (decrypt with age key)
6. Cloudflare API: update A records for all tenant domains to new server IP
7. Verify: `curl -I https://<domain>` → expect 200
8. Install and verify first backup run on new server

---

### Layer 4: Recovery Drills

- Monthly: restore one random tenant to a test server — verify it works, log result
- Quarterly: full disaster simulation — all tenants, fresh server, target < 4 hours

---

## Current Tenant Inventory

Discovered dynamically from `/opt/nbne/instances/` — do NOT hardcode this list.
Reference only — update as tenants change.

| Slug | Domain | DB name | Paradigm | Status |
|------|--------|---------|----------|--------|
| demnurse | demnurse.nbne.uk | demnurse | appointment | live client |
| northumberland-karate | northumberland-karate.phloe.co.uk | northumberland-karate | class | live client |
| amble-pincushion | amble-pincushion.phloe.co.uk | amble-pincushion | class | demo/prospect |
| pizza-shack-x | pizza-shack-x.nbne.uk | pizza-shack-x | food | demo |
| salon-x | salon-x.nbne.uk | salon-x | appointment | demo |
| restaurant-x | restaurant-x.nbne.uk | restaurant-x | table | demo |
| health-club-x | health-club-x.nbne.uk | health-club-x | class | demo |
| mind-department | mind-department.nbne.uk | mind-department | appointment | demo/prospect |
| nbne | nbne.nbne.uk | nbne | — | internal |

---

## Implementation Priority

### Phase 1 — Basic Protection (this week) ✅ CURRENT
- [ ] Enumerate all tenant containers and DB names from live server
- [ ] Write `/opt/ark/backup.sh` — Docker-aware pg_dump + volume media dump
- [ ] Local backup destination: `/backups/ark/daily/<slug>/<date>/`
- [ ] Cron: `0 3 * * * /opt/ark/backup.sh`
- [ ] Email alert on failure
- [ ] Manual test run — verify all tenants backed up successfully

### Phase 2 — Off-Site Push (this week / next)
- [ ] Toby creates Backblaze B2 account and bucket
- [ ] Install and configure rclone on server
- [ ] Push daily backups to B2 after local dump
- [ ] Verify: manually download and restore one tenant from B2

### Phase 3 — Re-Provisioning Script (1–2 weeks)
- [ ] `ark-restore.sh` — full server rebuild from backup
- [ ] Cloudflare API integration for DNS cutover
- [ ] Test on a fresh Hetzner CX22 (€4/month test server)
- [ ] Recovery runbook documentation

### Phase 4 — CSV Export + Monitoring (2–3 weeks)
- [ ] Django management command: `export_tenant_csv`
- [ ] Manifest generation
- [ ] Backup health dashboard or status page

### Phase 5 — Encryption + Hardening (3–4 weeks)
- [ ] `age` encryption for all archives before off-site push
- [ ] Key management documentation (Toby holds key locally + sealed emergency copy)
- [ ] Retention policy automation
- [ ] Quarterly full-disaster drill schedule

---

## Off-Site Storage Costs

| Provider | Cost | Notes |
|----------|------|-------|
| Backblaze B2 | ~$0.006/GB/month | Primary — provider-isolated from Hetzner |
| Hetzner Storage Box BX11 | €3.81/month, 1TB | Fast restore, same-provider convenience |
| Estimated for current tenants | < $2/month | Scales with media volume |

---

## Decision Log

### 2026-03-31 — Project Registered
**Context**: Phloe has zero backup or disaster recovery infrastructure. If the Hetzner
server at 178.104.1.152 fails, all client data and all tenant sites are permanently
lost. DemNurse and Ganbarukai are live paying clients. This is an unacceptable
business risk.
**Decision**: Create "Ark" — phased backup, recovery, and re-provisioning system.
Phase 1 (pg_dump + cron, Docker-aware) implemented this week. Phase 2–5 follow.
**Rationale**: Disaster recovery is infrastructure, not a feature. The cost of
implementation (days) is trivial vs the cost of data loss (business-ending).
**Rejected**: Managed snapshots (insufficient granularity, no tenant-level restore),
manual backups (will be forgotten), deferral (every day unprotected is uninsured risk).
**Key architectural constraint identified at registration**: Phloe is fully Dockerised.
Backup script must use `docker exec <slug>-db-1 pg_dump` and Docker volume extraction,
not system-level postgres or filesystem media paths. DB names = slug, not phloe_<slug>.

### 2026-04-04 — Standalone Apps: Memorials Added

**Context**: Memorials (memorial SVG generator) runs on the same Hetzner server but is
NOT a Phloe tenant. It has its own docker-compose.yml, uses SQLite (not PostgreSQL),
and has fixed container names (memorials-backend-1, memorials-frontend-1).

**Decision**: Add standalone app backup/restore scripts alongside the tenant system.
- `backup-memorials.sh`: docker cp for SQLite DB, tar Docker volumes, copy .env
- `restore-memorials.sh`: clone repo at recorded git hash, restore DB + volumes
- Stored under `$BACKUP_ROOT/memorials/$TODAY/` (separate from tenant backups)

**Key differences from Phloe tenants**:
- SQLite backup via `docker cp` (not `docker exec pg_dump`)
- Three volumes: memorials-data, memorials-uploads, memorials-output
- Deployed at `/opt/nbne/memorials/` (not `/opt/nbne/instances/`)
- WAL files (memorials.db-wal, memorials.db-shm) also backed up if present
- Git commit hash recorded for exact repo state restoration
