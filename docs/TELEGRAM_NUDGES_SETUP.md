# Deek Telegram Nudges — setup guide

One-off steps to wire the Telegram bot. Do these once; after that
the channel runs autonomously.

Phase A is shadow-mode by default (`DEEK_NUDGES_SHADOW=true`) —
triggers queue nudges but the sender does NOT actually hit the
Telegram API until cutover. Scheduled cutover: **2026-05-20**.

---

## Step 1 — Create the bot

On your phone or the desktop Telegram app:

1. Open a chat with **@BotFather**
2. Send `/newbot`
3. Name: `Deek Nudges` (or similar — shown in your chat list)
4. Username: `DeekNbneBot` (must end in `bot`; pick anything
   available)
5. BotFather returns an HTTP API token — looks like
   `123456789:ABCdefGhIjKlMnOpQrStUvWxYz-0123456789`

**Keep the token private** — anyone with it can send messages as
the bot.

## Step 2 — Set env vars on Hetzner

```bash
ssh root@178.104.1.152
sudo -e /opt/nbne/deek/deploy/.env
```

Add:

```
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIjKlMnOpQrStUvWxYz-0123456789
TELEGRAM_WEBHOOK_SECRET=<random 32-char string; pick your own>
```

Then rebuild: `cd /opt/nbne/deek/deploy && ./build-deek-api.sh full`

## Step 3 — Register the webhook with Telegram

One-off curl. Run on Hetzner:

```bash
source /opt/nbne/deek/deploy/.env
curl -sS -X POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -H 'Content-Type: application/json' \
  -d "{
    \"url\": \"https://deek.nbnesigns.co.uk/api/deek/telegram/webhook\",
    \"secret_token\": \"${TELEGRAM_WEBHOOK_SECRET}\",
    \"allowed_updates\": [\"message\"]
  }"
```

Should return `{"ok":true,...}`. Verify:

```bash
curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```

Expect `"url": "https://deek.nbnesigns.co.uk/api/deek/telegram/webhook"`
with `"pending_update_count": 0`.

**Nginx routing**: the webhook path `/api/deek/*` already proxies
to FastAPI per the existing location block in
`/etc/nginx/sites-enabled/deek.conf`. No nginx change needed.

## Step 4 — Register your Telegram account

```bash
ssh root@178.104.1.152
docker exec -w /app -e PYTHONPATH=/app deploy-deek-api-1 \
  python scripts/telegram_join_code.py toby@nbnesigns.com
```

This prints an 8-character code valid for 30 minutes. Open
Telegram, find your newly-created bot, send the code as a
message. The bot replies with a confirmation — your chat_id is
now paired to `toby@nbnesigns.com`.

Repeat for Jo + Ivan when ready:

```bash
python scripts/telegram_join_code.py jo@nbnesigns.com
python scripts/telegram_join_code.py ivan@nbnesigns.com
```

## Step 5 — Schedule the crons

Already scripted in the deploy (see below), but verify:

```bash
crontab -l | grep nudge
```

Expected:

```
# Deek nudges — stalled-project trigger, daily
30 7 * * * docker exec -w /app -e PYTHONPATH=/app deploy-deek-api-1 \
    python scripts/nudge_stalled_projects.py >> /var/log/deek-nudge-stalled.log 2>&1

# Deek nudges — drain pending queue every 5 min
*/5 * * * * docker exec -w /app -e PYTHONPATH=/app deploy-deek-api-1 \
    python scripts/send_pending_nudges.py >> /var/log/deek-nudge-sender.log 2>&1

# Deek nudges — ONE-SHOT cutover scheduled for 2026-05-20
0 9 20 5 * cd /opt/nbne/deek && python3 scripts/nudges_cutover.py \
    >> /var/log/deek-nudges-cutover.log 2>&1
```

## Step 6 — Shadow mode review

During shadow period (now → 2026-05-20), nudges queue but don't
send. Review what WOULD have been sent via:

```sql
SELECT id, trigger_kind, user_email, LEFT(message_text, 120),
       state, created_at, sent_at
  FROM cairn_intel.deek_nudges
 WHERE state IN ('shadow', 'pending')
 ORDER BY created_at DESC
 LIMIT 20;
```

Or via the shadow-review dashboard at
`https://deek.nbnesigns.co.uk/admin/shadow/review-ui` (Phase B
will add a Nudges tab there; for Phase A, SQL is fine).

If the stalled-project signals look too noisy or miss obvious
cases, tune `--stale-days` in the cron line or adjust the
`STALE_STAGES` list in `scripts/nudge_stalled_projects.py`.

## Step 7 — Cutover (2026-05-20, automatic)

`scripts/nudges_cutover.py` runs at 09:00 UTC on 2026-05-20:

1. Reads shadow stats (≥20 rows logged, ≥72h span)
2. Flips `DEEK_NUDGES_SHADOW=false` in `.env`
3. Restarts deek-api
4. Logs the cutover to `data/nudges_cutover.jsonl`

If gates fail, writes a reason to the log and waits for manual
action. To force early: `python scripts/nudges_cutover.py --force`.

---

## Troubleshooting

**Webhook returns 401** — `TELEGRAM_WEBHOOK_SECRET` doesn't match
the one set at `/setWebhook`. Re-run Step 3 with the current env.

**Bot doesn't respond to codes** — webhook isn't receiving
updates. Check `getWebhookInfo` → `pending_update_count`. If high,
webhook URL is unreachable (nginx / cert / DNS).

**Code expired** — generate a fresh one via Step 4.

**Nudges queue but never send** — still in shadow mode. Check
`SELECT * FROM cairn_intel.deek_nudges WHERE state='pending' LIMIT 5`
— state should be 'shadow' not 'pending'. If the sender cron isn't
firing, check `/var/log/deek-nudge-sender.log`.

**Want to opt out entirely** — set `revoked_at = NOW()` on your
row in `cairn_intel.registered_telegram_chats`. Sender will skip
future sends.
