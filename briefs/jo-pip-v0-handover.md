# Jo's Pip v0 — Handover

**Date:** 2026-04-27
**Status:** Staged, not running. ~10 minutes of Toby + Jo work to bring live.
**Companion docs:**
- `briefs/jo-pip-v0-spec.md` — deployment + boundaries
- `briefs/jo-pip-mobile-design.md` — UX + interaction design

---

## What's done

| | |
|---|---|
| Capacity check on nbne1 | ✅ green — 181GB disk free, 12GB RAM avail, Docker 28.2, Tailscale 1.96 already running |
| `/opt/nbne/jo-pip/` directory + permissions | ✅ |
| Deek repo cloned at `/opt/nbne/jo-pip-src/` | ✅ commit `dd7261eb` |
| `docker-compose.yml` (3 services: db, api, poller) | ✅ tailnet-bound only |
| `.env` with fresh `POSTGRES_PASSWORD` + `DEEK_API_KEY` | ✅ at `/opt/nbne/jo-pip/.env`, mode 600 |
| `.gitignore` for the deploy dir | ✅ |
| Image built (`jo-pip-deek:latest`) | ✅ sha `77cbc83e` |
| Nginx vhost on `100.125.120.1:80` for `jo.nbne.local` | ✅ live |
| Polling driver (no public webhook needed) | ✅ shipped to Deek codebase, PR #55 merged |
| Jo project profile (`projects/jo/config.json` + `identity.md`) | ✅ in main repo + cloned to nbne1 |

---

## What needs doing (in order)

### 1. Toby — add cloud API keys to `.env`

```bash
ssh toby@192.168.1.228
sudo -e /opt/nbne/jo-pip/.env
```

Fill these from the existing Hetzner `.env` (same accounts; they're cloud-provider-tied, not deployment-tied):
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `DEEPSEEK_API_KEY`

Leave `TELEGRAM_BOT_TOKEN` blank for now — fills in step 3.

### 2. Jo — create her Telegram bot via @BotFather

On her phone:

1. Open Telegram, message `@BotFather`
2. `/newbot`
3. Name: **Rex** (or whatever she likes — this is the displayed name in her chat)
4. Username: must end in `bot`. Suggest `JoNbneRexBot` or `RexNbneBot`
5. BotFather returns an HTTP API token — looks like `123456789:ABC...`
6. Send the token to Toby (over a secure channel — DM, not a shared chat)
7. Optional: in @BotFather, `/setdescription` and `/setuserpic` so Rex's avatar is distinct from any other bots she has

### 3. Toby — paste Telegram token + bring up the stack

```bash
ssh toby@192.168.1.228
sudo -e /opt/nbne/jo-pip/.env   # paste TELEGRAM_BOT_TOKEN value
cd /opt/nbne/jo-pip
docker compose up -d
docker compose ps                # all three containers should be Up
docker compose logs -f --tail 50 # watch for the API health check + poller startup
```

Expected log lines:
- `jo-pip-db`: `database system is ready to accept connections`
- `jo-pip-api`: `Uvicorn running on http://0.0.0.0:8765`
- `jo-pip-poller`: `[telegram-poll] entering poll loop (long_poll_timeout=25s)`

### 4. Toby — apply migrations to fresh DB

The API runs `apply_migrations()` automatically on startup, but verify:

```bash
docker exec -w /app -e PYTHONPATH=/app jo-pip-api python -c "
from core.memory.migrations import apply_migrations
import json
print(json.dumps(apply_migrations(), indent=2, default=str))
"
```

Expected: `applied: [0001..0016]`, `total: 16`, `errors: []`.

### 5. Toby + Jo — pair her chat_id with Rex

```bash
docker exec -w /app -e PYTHONPATH=/app jo-pip-api \
  python scripts/telegram_join_code.py jo@nbnesigns.com
```

Returns an 8-character code. Jo opens her chat with Rex on Telegram, sends just the code. Rex replies "✅ Registered."

### 6. Tailscale ACL — Toby

In the Tailscale admin console (`login.tailscale.com`):
- Confirm Jo's phone is on the tailnet (her device should already be paired if she's used Tailscale before; otherwise she installs the app + signs in with her NBNE account)
- Add an ACL rule allowing Jo's device → `nbne1` on TCP 80 (the vhost port)
- All other tailnet members are denied access to nbne1 by default — Toby has SSH for ops, no one else needs nbne1 access

Per the v0 spec: ACL is **Jo only** + Toby's SSH for ops. Ivan added later when his own Pip ships.

### 7. MagicDNS / hostname resolution — Jo

For Jo's phone to resolve `jo.nbne.local`:

**Option A (preferred): Tailscale MagicDNS.** In the Tailscale admin, enable MagicDNS for the tailnet. nbne1 then resolves automatically as `nbne1.<tailnet-name>.ts.net`. Jo bookmarks `http://nbne1.<tailnet>.ts.net/` instead of `jo.nbne.local`.

**Option B (fallback): /etc/hosts on her phone.** Doesn't work on iOS without jailbreak. Android needs root. Not viable for non-technical users.

Recommend Option A. The vhost's `server_name` includes both `jo.nbne.local` and `jo`, plus the IP-only fallback works regardless.

### 8. First brief — Toby

Force-send a brief to verify end-to-end:

```bash
docker exec -w /app -e PYTHONPATH=/app jo-pip-api \
  python scripts/send_memory_brief.py --user jo@nbnesigns.com --force --verbose
```

Expected: brief sent via Telegram (her channel is `telegram` per `user_profiles.yaml`). Jo gets it on her phone. She replies in plain prose. Polling driver picks up the reply ~5s later, runs through the conversational normaliser, persists answers, sends a confirmation back to Telegram.

### 9. Schedule the daily cron

```bash
crontab -e   # as toby
```

Add:
```
# Rex — Jo's Pip morning brief, 07:32 UTC (1 min after Toby's)
32 7 * * * docker exec -w /app -e PYTHONPATH=/app jo-pip-api python scripts/send_memory_brief.py --user jo@nbnesigns.com >> /var/log/rex-morning-brief.log 2>&1
```

**Disable Jo's NBNE-Deek-side cron simultaneously** (Toby's call from earlier — hard cutover, no parallel run):

```bash
ssh root@178.104.1.152 'crontab -l | grep -v "send_memory_brief.py --user jo" | crontab -'
```

### 10. Migrate Jo's existing brief replies

Per Toby's decision, copy her existing replies from NBNE-Deek to Rex with provenance tag.

```bash
# On Hetzner: dump Jo's existing brief responses + her past memory writes
ssh root@178.104.1.152 \
  'docker exec deploy-deek-db-1 pg_dump --data-only \
     -t memory_brief_runs -t memory_brief_responses \
     "postgresql://cairn:cairn_nbne_2026@localhost:5432/cairn" \
     | grep -E "jo@nbnesigns" \
     > /tmp/jo-brief-history.sql'

# Transfer to nbne1
scp -i ~/.ssh/id_ed25519 root@178.104.1.152:/tmp/jo-brief-history.sql /tmp/

# Apply to Rex's DB with a provenance tag preserved on each row
# (NOTE: this needs care — a few SQL fixups before apply. Don't
#  run blindly. I'll write the migration script as a follow-up
#  task once we see what's in the dump.)
```

This is the trickiest step + warrants a careful script rather than a one-off SQL apply. **Defer to a follow-up turn after Rex is otherwise live.** Jo can start using Rex with a clean memory in the meantime; the historical brief replies are a nice-to-have, not a blocker.

### 11. Onboarding conversation

When Jo's chat with Rex is paired and she's received her first brief, sit with her for ~20 min:
- Walk through what Rex is (read her the relevant bits of `identity.md`)
- Ask the first-conversation questions from `jo-pip-mobile-design.md` §6.1
- Capture her substantive replies as explicit memories
- Show her `/help`, `/projects`, `/audit` (placeholder), `/export` (placeholder)
- Open `http://jo.nbne.local/` (or the MagicDNS hostname) in her phone browser, install as PWA
- Confirm she can see today's brief in the PWA

---

## Architectural notes worth keeping handy

### Sovereignty by topology

- **API container binds to 127.0.0.1:8770** — not 0.0.0.0. The host doesn't expose the API on its LAN interface.
- **Postgres binds to 127.0.0.1:5436** — localhost-only. Only the API container reaches it (via Docker bridge net, not the host port).
- **Nginx vhost binds to 100.125.120.1:80** — Tailscale interface only. nbne1's LAN-side and public-side interfaces don't see the vhost.
- **Telegram polling, not webhook** — no public ingress required. nbne1 only makes outbound HTTPS calls.

There is no public IP, port, or service exposed by Rex. Everything happens inside the Tailscale boundary.

### Boundary with NBNE-Deek

- Different DB, different cluster, different credentials.
- Rex CAN call NBNE-Deek's `/api/cairn/search` for context (via the existing `search_crm` tool).
- Rex CANNOT write to NBNE-Deek without Jo's explicit per-item consent (via `write_crm_memory` tool, which she has to approve in chat first).
- NBNE-Deek cannot read from Rex's DB. No connection string, no shared credentials, no shared network namespace.

### Migration to v1 Pip

When v1 lands per `pip-product-spec-v0.3.md`, Rex's content migrates:

1. `pg_dump jo_deek` → restore into the per-Pip schema in v1's shared cluster
2. Jo generates a key pair (v1 §8.1) — public key associated with her existing data
3. Mode selection: she chooses strict / adaptive at v1 onboarding
4. Existing memories tagged `source=v0_pre_mode_split` so they don't masquerade
5. PMF baseline export runs once

The v0 → v1 commitment in the spec (§5) is non-negotiable: her data is preserved.

---

## Known gaps / follow-ups

These don't block v0 launch but should be done after Jo's been using Rex for a few days:

1. **Role-specific question builders.** `user_profiles.yaml` declares `question_categories: [hr_pulse, finance_check, d2c_observation, open_ended]` for Jo and `[production_quality, equipment_health, technical_solve, open_ended]` for Ivan. The corresponding question-builder code in `core/brief/questions.py` doesn't exist yet — those categories currently fall back to the open_ended template. Brief work needed.

2. **Existing brief replies migration** (step 10 above). Defer until Rex is otherwise live + we can write a careful SQL fixup script.

3. **PWA theming for Rex.** The PWA at `jo.nbne.local` currently shows the standard Deek/voice interface. Re-theming to the sage/warm-white scheme + Rex avatar + persistent confidentiality banner per `jo-pip-mobile-design.md` §4.5. ~1 day.

4. **Brief in PWA surface.** Today the brief lands in Telegram only. Surfacing the same brief inside the PWA (with reply boxes inline) is the §10 addition from the v0 spec — needs PWA work first.

5. **Jo's Telegram bot avatar + description.** When she creates Rex, she can `/setuserpic` and `/setdescription` in BotFather. Worth doing — distinct visual identity in her Telegram chat list.

---

## What success looks like at end of week 1

- Jo's received 5 morning briefs + replied to most of them
- She's sent at least one ad-hoc memory or question per day on average
- The polling driver hasn't required a restart
- Tailscale on her phone has held up (no manual reconnect needed)
- She's used the chat with Rex via Telegram + opened the PWA at least once
- Her conversation with Rex hasn't accidentally crossed into NBNE-Deek territory (audit log clean)

If those hold, v0 is working. We retro at end of week 2 + write v1 spec v0.4 against what we've learned.
