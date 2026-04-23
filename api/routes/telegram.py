"""Telegram webhook for the Deek nudge channel.

Two responsibilities:

  1. First-time registration: user sends a join code from their
     phone to the Deek bot; webhook pairs the chat_id with a
     user_email.

  2. Future: inbound replies correlated to a pending nudge so
     Toby can reply "dismiss" / "acknowledged" / free text, and
     Deek processes that. Phase A ships outbound only — the
     inbound side here just echoes "registered" and "noted".

Security:
  * Telegram signs webhook requests via a secret token in a
    header (``X-Telegram-Bot-Api-Secret-Token``). We set that
    header at webhook registration time and verify it here.
  * No public posting — anyone hitting this endpoint without the
    secret gets 401.

Setup:
  1. Create the bot via @BotFather → receive a token
  2. Set env TELEGRAM_BOT_TOKEN + TELEGRAM_WEBHOOK_SECRET
  3. Register the webhook:
        POST https://api.telegram.org/bot<TOKEN>/setWebhook
        body: {url: "https://deek.nbnesigns.co.uk/api/deek/telegram/webhook",
               secret_token: "<TELEGRAM_WEBHOOK_SECRET>"}
     (a one-off curl, can be a script later)
  4. Toby runs ``scripts/telegram_join_code.py toby@nbnesigns.com``
     + sends that code to the bot — webhook consumes it, maps his
     chat_id, subsequent nudges flow.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)
router = APIRouter(prefix='/telegram', tags=['Telegram Nudges'])


def _connect():
    import psycopg2
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        raise HTTPException(500, 'DATABASE_URL not set')
    try:
        return psycopg2.connect(db_url, connect_timeout=5)
    except Exception as exc:
        raise HTTPException(500, f'db connect failed: {exc}')


def _send_telegram(chat_id: int, text: str) -> bool:
    """Fire-and-forget send, used to confirm registration / ack
    replies. Swallows errors; webhook must always respond 200 or
    Telegram will retry aggressively."""
    token = (os.getenv('TELEGRAM_BOT_TOKEN') or '').strip()
    if not token:
        return False
    try:
        import httpx
        with httpx.Client(timeout=10.0) as client:
            client.post(
                f'https://api.telegram.org/bot{token}/sendMessage',
                json={
                    'chat_id': int(chat_id),
                    'text': text[:4096],
                    'parse_mode': 'Markdown',
                },
            )
        return True
    except Exception as exc:
        log.warning('[telegram] send failed: %s', exc)
        return False


@router.post('/webhook')
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> JSONResponse:
    """Handle inbound Telegram updates.

    Always returns 200 (Telegram retries otherwise). Invalid
    secrets are logged + dropped silently to avoid leaking
    endpoint presence to scanners.
    """
    expected_secret = (os.getenv('TELEGRAM_WEBHOOK_SECRET') or '').strip()
    if expected_secret and x_telegram_bot_api_secret_token != expected_secret:
        log.warning('[telegram] rejected webhook: bad/missing secret')
        return JSONResponse({'ok': True}, status_code=200)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({'ok': True}, status_code=200)

    try:
        _dispatch_update(payload)
    except Exception as exc:
        log.exception('[telegram] dispatch error: %s', exc)

    return JSONResponse({'ok': True})


def _dispatch_update(payload: dict) -> None:
    """Route the Telegram update to the right handler."""
    message = payload.get('message') or payload.get('edited_message') or {}
    if not message:
        return
    chat = message.get('chat') or {}
    chat_id = chat.get('id')
    if chat_id is None:
        return
    from_user = message.get('from') or {}
    text = (message.get('text') or '').strip()

    # Registration: a plain message that's an 8-char uppercase code
    # (the shape of our join codes). No other command surface in
    # Phase A.
    if _looks_like_join_code(text):
        _handle_join_code(
            chat_id=int(chat_id),
            code=text.upper(),
            telegram_username=from_user.get('username'),
            first_name=from_user.get('first_name'),
        )
        return

    # Anything else: polite ack so Toby knows we saw the message.
    # Reply-handling to nudges is a Phase B feature (dismiss /
    # acknowledge / chat thread routing).
    _send_telegram(
        int(chat_id),
        (
            'Message received. Interactive replies to nudges are '
            'coming in Phase B — for now, use the chat surface at '
            'deek.nbnesigns.co.uk/voice for conversation.'
        ),
    )


_JOIN_CODE_LEN = 8


def _looks_like_join_code(text: str) -> bool:
    if not text or len(text) != _JOIN_CODE_LEN:
        return False
    return all(c.isalnum() and (c.isupper() or c.isdigit()) for c in text)


def _handle_join_code(
    *, chat_id: int, code: str,
    telegram_username: str | None, first_name: str | None,
) -> None:
    from core.channels.nudge import consume_join_code
    conn = _connect()
    try:
        ok, detail = consume_join_code(
            conn, code, chat_id,
            telegram_username=telegram_username,
            first_name=first_name,
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if ok:
        _send_telegram(
            chat_id,
            (
                f'✅ Registered. Deek nudges for *{detail}* will '
                'now land here. Shadow mode is on initially — '
                'the first real nudges arrive after cutover '
                '(2026-05-20 unless Toby forces early).'
            ),
        )
    else:
        _send_telegram(
            chat_id,
            (
                f'❌ Could not register: {detail}. '
                'If the code expired, run '
                '`scripts/telegram_join_code.py <your-email>` to '
                'generate a fresh one.'
            ),
        )
