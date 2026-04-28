# Telegram brief replies — async webhook ack

**For:** fresh Claude Code session against the Deek repo (`D:\claw\`)
**Estimate:** half a day
**Triggered by:** 2026-04-28 incident — Toby's Telegram replies have been silently dropped because the webhook handler runs the conversational normaliser inline (~22s on a 7B model) and Telegram's webhook timeout fires before we can return 200. Telegram retries, the next call also hangs, eventually the queue fills. Net effect: zero replies persisted, zero acks delivered.

---

## Read first (in order)

1. `CLAUDE.md` — Deek agent scope (additive route changes are in-scope, but this is **API contract** to Telegram so be careful)
2. `api/routes/telegram.py` — current webhook handler (`_handle_message` + `_route_as_brief_reply` at line 184)
3. `core/brief/replies.py` — `parse_reply_body` + `apply_reply` + `store_response` (the apply path the webhook calls)
4. `core/brief/conversational.py` — `normalise_conversational_reply` (the slow LLM call inside `parse_reply_body`)
5. `core/brief/telegram_delivery.py` — `find_pending_telegram_brief` + `_send_telegram` (outbound)

Then look at:
- `api/main.py` for FastAPI app setup — confirm `BackgroundTasks` is available (FastAPI 0.x ships it)
- `tests/test_brief_pwa.py` for the existing test pattern (TestClient + monkeypatched `_connect`)

---

## What you're building

A two-phase webhook handler that returns 200 to Telegram immediately, then processes the reply in a background task. The user-visible flow becomes:

1. **t=0s**: Toby replies "TRUE — supplier still good"
2. **t≈0.3s**: Webhook returns 200 to Telegram. Handler sends `"⏳ Got it — processing…"` via the outbound Telegram API (independent of the webhook response).
3. **t≈25s**: Background task finishes parsing + applying. Sends the existing summary block (`✅ Brief reply logged (run abc12345…) • belief_audit: reinforced +0.5 • …`).

If parsing fails, the second message becomes the existing error path (`I couldn't parse answers to your brief out of that message…`). If `apply_reply` raises, send `"⚠️ Couldn't apply your reply — Toby will see it in the audit log."` and log loudly.

---

## Critical constraints

- **The webhook must return 200 in well under 60s.** Telegram closes the connection at ~60s and retries; that's how we got the 18-hour-old DB lock pile-up on 2026-04-28. Target <2s for the synchronous portion.

- **Background tasks must commit their own DB transactions** — the request-scoped connection is gone by the time the background task runs. Use a fresh connect inside the task.

- **Telegram out-of-order delivery is real.** If a user sends two replies in quick succession, the second webhook may complete before the first. The acks must include enough context (the run_id prefix is already in the existing summary) for Toby to disambiguate. Don't add new ordering guarantees.

- **Idempotency is via `already_applied(conn, run_id, raw_body)`.** That check was broken until 2026-04-28 (hash mismatch — see commit fixing `_body_hash`). Do **not** introduce a parallel idempotency path. Trust the existing one.

- **Don't change `_send_telegram`'s signature.** It's used by other code paths (nudges, error notifications). If you need richer formatting (reply-to-message-id, parse_mode), pass via kwargs.

---

## Implementation shape

In `api/routes/telegram.py`:

```python
from fastapi import BackgroundTasks

@router.post('/telegram/webhook')
async def telegram_webhook(
    update: dict,
    background_tasks: BackgroundTasks,
    ...
):
    # Existing fast-path checks (signature/secret, message extraction,
    # /commands, pairing) stay synchronous.
    ...

    # The two slow paths — brief reply + chat routing — move to bg tasks
    if has_pending_brief(user_email):
        # Synchronous: send "got it, processing" so the user sees something
        _send_telegram(chat_id, "⏳ Got it — processing your reply…")
        background_tasks.add_task(
            _apply_brief_reply_async,
            chat_id=chat_id, user_email=user_email, text=text,
        )
        return {"ok": True}

    # Same pattern for chat routing
    ...
```

Move `_route_as_brief_reply` body into `_apply_brief_reply_async` with these adjustments:
- Open a fresh DB connection at the top of the task (don't reuse caller's).
- All exception paths must call `_send_telegram` with a user-readable error AND `log.exception(...)` so failures surface in `docker logs` AND in Telegram.
- Wrap the whole thing in a `try / except / finally close-conn`. The current code already has this shape — just make it function-local instead of request-local.

Add a small helper `has_pending_brief(user_email)` that does only the cheap lookup (`find_pending_telegram_brief` is already an O(1) DB query) so the synchronous path stays fast. If you see this lookup taking >100ms in practice, add an index on `memory_brief_runs(user_email, generated_at desc)` — but that's likely already present.

---

## Tests (`tests/test_telegram_webhook_async.py`)

Use the existing FastAPI TestClient + fake-DB pattern in `tests/test_brief_pwa.py`. Key cases:

1. **Webhook returns 200 within 1s when a brief is pending.** Patch `_apply_brief_reply_async` to `time.sleep(5)`; assert the response time is <1s.
2. **Background task runs after the response returns.** Use a global counter in the patched task to confirm it executed.
3. **`_send_telegram` is called twice** — once for the "processing" ack, once for the summary.
4. **Failure path** — patch `apply_reply` to raise; assert `_send_telegram` is called with the error string and `log.exception` is called.
5. **No pending brief** — handler routes to chat as before, no new behaviour.

Run the existing brief-reply suites (`tests/memory/test_brief_replies.py`, `tests/test_brief_pwa.py`) to confirm no regression — neither touches `api/routes/telegram.py`, but the helpers move around.

---

## Out of scope for this session

- **Switching to long-polling instead of webhook** (`scripts/run_telegram_poller.py` exists from PR #55 but isn't deployed on Hetzner). Long-polling makes the timeout question moot but is a deployment change, separate brief.
- **Smart skip for structured replies** ("Fix 2" — only call the LLM normaliser when the reply isn't already YES/NO/TRUE/FALSE-shaped). Additive after this lands.
- **Idempotency for outbound acks.** If Telegram retries the webhook before the bg task finishes, we'd send "processing…" twice. Acceptable for v1; add a per-update_id dedup in v2.
- **Cleanup of duplicate `claw_code_chunks` rows** caused by the hash-mismatch bug pre-fix. Separate SQL.

---

## Definition of done

1. Webhook handler returns 200 in <2s for brief-reply messages, verified by test.
2. Background task runs `parse_reply_body` + `apply_reply` + `store_response` and sends the summary message via Telegram.
3. The existing "I couldn't parse answers to your brief…" error path still fires for genuinely unparseable replies, but now from inside the bg task.
4. `docker logs deploy-deek-api-1` shows a `[telegram] async-applied run=...` log line per successful apply (so we have a visible audit trail without round-tripping the DB).
5. Tests pass: 5 new + 52 existing brief tests.
6. After deploy on Hetzner: send a test reply to today's brief from Toby's Telegram, confirm both messages arrive and `memory_brief_responses` has exactly one row.

When done: update this brief with a completion note + commit hash, mark `briefs/telegram-webhook-async-ack.md` as ✅ in any tracking doc.

---

## Confirm before starting

- Validator clean: `python scripts/validate_brief.py briefs/telegram-webhook-async-ack.md`
- Deek API reachable: `GET http://localhost:8765/health`
- Pull memory: `retrieve_codebase_context(query="telegram webhook brief reply", project="deek", limit=5)`
- If anything in the constraints section is unclear, raise it before writing code — async/background tasks are the kind of place where "I'll figure it out" produces subtly broken results.
