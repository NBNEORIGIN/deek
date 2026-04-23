"""Deek nudge subsystem — proactive outbound messages to staff.

Channel abstraction so the trigger logic doesn't know or care
whether the nudge lands in Telegram, WhatsApp, email, or PWA push.
Phase A ships Telegram only; the ``NudgeChannel`` enum + send
dispatch mean additional channels drop in without touching the
triggers.

Key primitives:

    queue_nudge(kind, user_email, message, related_ref?, cooldown_hours?)
        Inserts a pending row. Respects the cooldown: if the same
        related_ref was nudged in the last N hours, skip (returns
        the existing row id and state='skipped').

    send_pending(limit=20)
        Cron driver. Pops pending rows, calls the channel send
        path, updates state. Shadow-mode-gated — under shadow
        rows go to state='shadow' instead of firing the channel.

    record_join_code(user_email) -> code
        One-shot: generates a join code Toby sends to the bot.

    consume_join_code(code, chat_id, username?, first_name?)
        Called by the webhook: ties chat_id to user_email.

Shadow gate: ``DEEK_NUDGES_SHADOW=true`` (default) queues + logs
everything but never fires the Telegram API. Cutover cron
scheduled 2026-05-20.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


logger = logging.getLogger(__name__)


TELEGRAM_API_BASE = 'https://api.telegram.org'
TELEGRAM_TIMEOUT = 15.0


@dataclass
class NudgeResult:
    """What queue_nudge and send_pending return."""
    nudge_id: int | None
    state: str
    detail: str = ''


# ── Shadow gate ─────────────────────────────────────────────────────

def is_nudges_shadow() -> bool:
    """Default on. DEEK_NUDGES_SHADOW=false after the 2026-05-20
    cutover cron."""
    raw = (os.getenv('DEEK_NUDGES_SHADOW') or 'true').strip().lower()
    return raw in {'true', '1', 'yes', 'on'}


def telegram_bot_token() -> str:
    return (os.getenv('TELEGRAM_BOT_TOKEN') or '').strip()


# ── Queue side ──────────────────────────────────────────────────────

def queue_nudge(
    conn,
    *,
    kind: str,
    user_email: str,
    message: str,
    related_ref: str | None = None,
    cooldown_hours: int = 24,
    context: dict | None = None,
) -> NudgeResult:
    """Queue a pending nudge. Returns a NudgeResult with state one of:
      - 'pending'  — queued for send
      - 'skipped'  — a nudge with the same related_ref fired within
                     the cooldown window; nothing queued
      - 'error'    — DB write failed
    """
    kind = (kind or '').strip()
    user_email = (user_email or '').strip().lower()
    message = (message or '').strip()
    if not (kind and user_email and message):
        return NudgeResult(None, 'error', 'missing kind/user/message')

    try:
        # Cooldown check
        if related_ref:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id FROM cairn_intel.deek_nudges
                        WHERE related_ref = %s
                          AND user_email  = %s
                          AND created_at > NOW() - (INTERVAL '1 hour' * %s)
                          AND state IN ('pending', 'sent', 'shadow', 'acknowledged')
                        ORDER BY created_at DESC
                        LIMIT 1""",
                    (related_ref, user_email, cooldown_hours),
                )
                existing = cur.fetchone()
            if existing:
                return NudgeResult(int(existing[0]), 'skipped',
                                   'cooldown-hit')

        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO cairn_intel.deek_nudges
                    (trigger_kind, user_email, message_text,
                     related_ref, context_json, cooldown_hours)
                   VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                   RETURNING id""",
                (kind, user_email, message[:4000],
                 related_ref, json.dumps(context or {}),
                 int(cooldown_hours)),
            )
            (new_id,) = cur.fetchone()
            conn.commit()
        return NudgeResult(int(new_id), 'pending')
    except Exception as exc:
        logger.warning('[nudge] queue failed: %s', exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return NudgeResult(None, 'error',
                           f'{type(exc).__name__}: {exc}')


# ── Send side ───────────────────────────────────────────────────────

def _lookup_chat_id(conn, user_email: str) -> int | None:
    """Active chat id for this user, or None if unregistered."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT chat_id
                     FROM cairn_intel.registered_telegram_chats
                    WHERE user_email = %s
                      AND revoked_at IS NULL
                    ORDER BY registered_at DESC
                    LIMIT 1""",
                (user_email,),
            )
            row = cur.fetchone()
        return int(row[0]) if row else None
    except Exception as exc:
        logger.warning('[nudge] chat lookup failed: %s', exc)
        return None


def _send_telegram(chat_id: int, text: str) -> tuple[bool, int | None, str]:
    """Returns (ok, telegram_message_id, error_detail)."""
    token = telegram_bot_token()
    if not token:
        return False, None, 'TELEGRAM_BOT_TOKEN not set'
    try:
        with httpx.Client(timeout=TELEGRAM_TIMEOUT) as client:
            r = client.post(
                f'{TELEGRAM_API_BASE}/bot{token}/sendMessage',
                json={
                    'chat_id': int(chat_id),
                    'text': text[:4096],   # Telegram limit
                    'parse_mode': 'Markdown',
                    'disable_web_page_preview': True,
                },
            )
    except Exception as exc:
        return False, None, f'{type(exc).__name__}: {exc}'
    if r.status_code != 200:
        return False, None, f'HTTP {r.status_code}: {r.text[:300]}'
    try:
        data = r.json() or {}
    except Exception:
        return False, None, 'non-json response'
    if not data.get('ok'):
        return False, None, f"telegram error: {data.get('description', '?')}"
    msg_id = ((data.get('result') or {}).get('message_id'))
    return True, int(msg_id) if msg_id else None, ''


def send_pending(conn, limit: int = 20) -> dict:
    """Drain up to `limit` pending nudges. Called by the minute-
    cadence cron. Returns a summary dict for logging."""
    summary = {
        'processed': 0, 'sent': 0, 'shadow': 0,
        'skipped': 0, 'failed': 0,
    }
    shadow = is_nudges_shadow()

    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, user_email, message_text
                 FROM cairn_intel.deek_nudges
                WHERE state = 'pending'
                ORDER BY created_at ASC
                LIMIT %s""",
            (int(limit),),
        )
        rows = cur.fetchall()

    for nudge_id, user_email, message in rows:
        summary['processed'] += 1
        nudge_id = int(nudge_id)

        if shadow:
            _set_state(conn, nudge_id, 'shadow',
                       error=None, telegram_message_id=None)
            summary['shadow'] += 1
            continue

        chat_id = _lookup_chat_id(conn, user_email)
        if chat_id is None:
            _set_state(conn, nudge_id, 'skipped',
                       error='no registered chat_id')
            summary['skipped'] += 1
            continue

        ok, tg_msg_id, detail = _send_telegram(chat_id, message)
        if ok:
            _set_state(conn, nudge_id, 'sent',
                       error=None, telegram_message_id=tg_msg_id)
            summary['sent'] += 1
        else:
            _set_state(conn, nudge_id, 'failed', error=detail)
            summary['failed'] += 1

    return summary


def _set_state(
    conn, nudge_id: int, state: str,
    error: str | None, telegram_message_id: int | None = None,
) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE cairn_intel.deek_nudges
                      SET state = %s,
                          error_detail = %s,
                          telegram_message_id = COALESCE(%s, telegram_message_id),
                          sent_at = CASE
                              WHEN %s IN ('sent', 'shadow') THEN NOW()
                              ELSE sent_at
                          END
                    WHERE id = %s""",
                (state, error, telegram_message_id, state, nudge_id),
            )
            conn.commit()
    except Exception as exc:
        logger.warning('[nudge] state update failed: %s', exc)


# ── Join codes ──────────────────────────────────────────────────────

_JOIN_CODE_ALPHA = string.ascii_uppercase + string.digits


def _make_code(length: int = 8) -> str:
    return ''.join(secrets.choice(_JOIN_CODE_ALPHA) for _ in range(length))


def record_join_code(
    conn, user_email: str, ttl_minutes: int = 30,
) -> str:
    """Generate + persist a one-shot join code. Toby runs a CLI
    that prints the code, then sends it to the bot — the webhook
    calls consume_join_code() with the chat_id."""
    code = _make_code()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO cairn_intel.telegram_join_codes
                (code, user_email, expires_at)
               VALUES (%s, %s, %s)""",
            (code, user_email.lower(), expires_at),
        )
        conn.commit()
    return code


def consume_join_code(
    conn, code: str, chat_id: int,
    telegram_username: str | None = None,
    first_name: str | None = None,
) -> tuple[bool, str]:
    """Tie chat_id to user_email via the code. Returns
    (ok, user_email_or_reason)."""
    code = (code or '').strip().upper()
    if not code:
        return False, 'empty code'
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT user_email, expires_at, consumed_at
                     FROM cairn_intel.telegram_join_codes
                    WHERE code = %s
                   FOR UPDATE""",
                (code,),
            )
            row = cur.fetchone()
            if row is None:
                return False, 'unknown code'
            user_email, expires_at, consumed_at = row
            if consumed_at is not None:
                return False, 'code already consumed'
            if expires_at < datetime.now(timezone.utc):
                return False, 'code expired'
            # Mark consumed
            cur.execute(
                """UPDATE cairn_intel.telegram_join_codes
                      SET consumed_at = NOW(),
                          consumed_by_chat_id = %s
                    WHERE code = %s""",
                (int(chat_id), code),
            )
            # Upsert the registration
            cur.execute(
                """INSERT INTO cairn_intel.registered_telegram_chats
                    (user_email, chat_id, telegram_username, first_name)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (user_email, chat_id) DO UPDATE
                      SET telegram_username = EXCLUDED.telegram_username,
                          first_name        = EXCLUDED.first_name,
                          revoked_at        = NULL""",
                (user_email, int(chat_id), telegram_username, first_name),
            )
            conn.commit()
        return True, user_email
    except Exception as exc:
        logger.warning('[nudge] join-code consume failed: %s', exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return False, f'{type(exc).__name__}'


__all__ = [
    'NudgeResult',
    'is_nudges_shadow',
    'queue_nudge',
    'send_pending',
    'record_join_code',
    'consume_join_code',
]
