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

    Always returns 200 fast (Telegram retries otherwise) and
    dispatches real work on a background task. Invalid secrets
    are logged + dropped silently.
    """
    expected_secret = (os.getenv('TELEGRAM_WEBHOOK_SECRET') or '').strip()
    if expected_secret and x_telegram_bot_api_secret_token != expected_secret:
        log.warning('[telegram] rejected webhook: bad/missing secret')
        return JSONResponse({'ok': True}, status_code=200)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({'ok': True}, status_code=200)

    # Kick the dispatch onto a background task so the webhook
    # responds 200 immediately. Agent.process() can take 10-30s
    # for a chat call; Telegram will retry aggressively if we
    # block.
    import asyncio
    try:
        asyncio.create_task(_dispatch_update_async(payload))
    except Exception as exc:
        log.exception('[telegram] task spawn error: %s', exc)

    return JSONResponse({'ok': True})


async def _dispatch_update_async(payload: dict) -> None:
    """Async wrapper around the router — catches everything so the
    background task never raises into the event loop."""
    try:
        await _route_update(payload)
    except Exception as exc:
        log.exception('[telegram] dispatch error: %s', exc)


async def _route_update(payload: dict) -> None:
    message = payload.get('message') or payload.get('edited_message') or {}
    if not message:
        return
    chat = message.get('chat') or {}
    chat_id = chat.get('id')
    if chat_id is None:
        return
    from_user = message.get('from') or {}
    text = (message.get('text') or '').strip()
    if not text:
        return

    # 1. Join-code registration (sync, cheap)
    if _looks_like_join_code(text):
        _handle_join_code(
            chat_id=int(chat_id),
            code=text.upper(),
            telegram_username=from_user.get('username'),
            first_name=from_user.get('first_name'),
        )
        return

    # 2. Registered user chatting with Deek
    user_email = _lookup_user_email(int(chat_id))
    if user_email is None:
        _send_telegram(
            int(chat_id),
            (
                '👋 You\'re not registered yet. Ask Toby for a join '
                'code (via `scripts/telegram_join_code.py <your-email>`) '
                'and send it here to pair.'
            ),
        )
        return

    await _route_chat_message(
        chat_id=int(chat_id),
        user_email=user_email,
        text=text,
    )


def _lookup_user_email(chat_id: int) -> str | None:
    """Look up the registered user_email for a given chat_id.
    None if the chat isn't paired (or was revoked)."""
    try:
        conn = _connect()
    except Exception:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT user_email
                     FROM cairn_intel.registered_telegram_chats
                    WHERE chat_id = %s
                      AND revoked_at IS NULL
                    ORDER BY registered_at DESC
                    LIMIT 1""",
                (int(chat_id),),
            )
            row = cur.fetchone()
        return row[0] if row else None
    except Exception as exc:
        log.warning('[telegram] lookup_user_email failed: %s', exc)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


async def _route_chat_message(
    *, chat_id: int, user_email: str, text: str,
) -> None:
    """Feed a registered user's Telegram message into the chat
    agent, stream/collect the response, send back to the chat."""
    # Quick "thinking" ack so the user sees immediate feedback
    _send_telegram(chat_id, '🤔 _thinking..._')

    try:
        from api.main import get_agent
        from core.channels.envelope import Channel, MessageEnvelope

        agent = get_agent('deek')
        envelope = MessageEnvelope(
            content=text,
            channel=Channel.TELEGRAM,
            project_id='deek',
            session_id=f'tg_{chat_id}',
            # Keep responses tool-light — Telegram isn't a place
            # for multi-tool exploration loops; a couple of rounds
            # covers search + answer patterns.
            max_tool_rounds=4,
            # False so writes are available (write_crm_memory etc.)
            read_only=False,
        )
        response = await agent.process(envelope)
        out = (response.content or '').strip()
        if not out:
            out = '_(empty response)_'
    except Exception as exc:
        log.exception('[telegram] chat route failed: %s', exc)
        _send_telegram(
            chat_id,
            '❌ Something went wrong while processing that. '
            'Try again, or check `/var/log/` on the server.',
        )
        return

    for chunk in _chunk_for_telegram(out):
        _send_telegram(chat_id, chunk)


_TELEGRAM_MSG_LIMIT = 4000   # slight margin under the 4096 hard limit


def _chunk_for_telegram(text: str) -> list[str]:
    """Split a long response into Telegram-friendly chunks. Prefer
    paragraph boundaries; fall back to hard slice at 4000 chars."""
    text = (text or '').strip()
    if len(text) <= _TELEGRAM_MSG_LIMIT:
        return [text] if text else []
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= _TELEGRAM_MSG_LIMIT:
            chunks.append(remaining)
            break
        # Look for the last double newline inside the window
        window = remaining[:_TELEGRAM_MSG_LIMIT]
        split_at = window.rfind('\n\n')
        if split_at == -1 or split_at < 500:
            # fall back to single newline
            split_at = window.rfind('\n')
        if split_at == -1 or split_at < 500:
            split_at = _TELEGRAM_MSG_LIMIT
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    return chunks


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
