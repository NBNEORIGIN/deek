"""Memory Brief — Telegram delivery path.

Companion to core/brief/composer.py's email path. Renders the brief
compactly for a Telegram chat thread (4096-char limit, markdown,
no quoted-message noise) and sends via the existing bot wiring.

The inbound side (Toby's prose reply) is handled by
api/routes/telegram.py detecting a pending brief for the user +
routing the message through the same conversational normaliser
already used for email prose replies. No new parser needed.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx


logger = logging.getLogger(__name__)


TELEGRAM_API_BASE = 'https://api.telegram.org'
TELEGRAM_TIMEOUT = 15.0
TELEGRAM_MSG_LIMIT = 4000   # below Telegram's 4096 hard cap


@dataclass
class TelegramDeliveryResult:
    ok: bool
    chat_id: int | None = None
    message_ids: list[int] | None = None
    error: str | None = None


# ── Compact brief renderer ──────────────────────────────────────────

_EMOJI_BY_INDEX = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣']
_CATEGORY_LABEL = {
    'belief_audit': 'Belief audit',
    'gist_validation': 'Gist check',
    'salience_calibration': 'Salience check',
    'open_ended': 'Open',
    'research_prompt': 'Research prompt',
}


def render_brief_for_telegram(
    *, display_name: str, generated_at, questions: list,
    drafted_briefs: list[dict] | None = None,
) -> str:
    """Render a Telegram-friendly compact version of the morning brief.

    ``questions`` is a list of core.brief.questions.Question objects
    (duck-typed: needs .category + .prompt).
    Returns a single string — caller chunks if needed.
    """
    date_str = generated_at.strftime('%Y-%m-%d') if generated_at else ''
    lines: list[str] = []
    name = (display_name or '').strip()
    greeting = f'Hi {name},' if name else 'Morning —'

    lines.append(f'🌅 Deek morning brief — {date_str}')
    lines.append('')
    lines.append(greeting)
    lines.append('')
    q_count = len(questions)
    if q_count:
        lines.append(
            f'{q_count} question{"s" if q_count != 1 else ""} today. '
            'Reply in plain English — I\'ll parse the rest.'
        )
        lines.append('')

    for i, q in enumerate(questions):
        emoji = _EMOJI_BY_INDEX[i] if i < len(_EMOJI_BY_INDEX) else f'{i+1}.'
        label = _CATEGORY_LABEL.get(q.category, q.category)
        lines.append(f'{emoji} {label}')
        lines.append(_compact_prompt(q.prompt))
        lines.append('')

    if drafted_briefs:
        lines.append('📄 Research briefs ready to review')
        for d in drafted_briefs[:3]:
            path = d.get('brief_path') or ''
            title = (d.get('title') or '')[:70]
            lines.append(f'  • {path} — {title}')
        lines.append('')

    lines.append('Reply whenever. No format needed.')
    return '\n'.join(lines)


def _compact_prompt(prompt: str) -> str:
    """Strip the heavy email-formatting conventions from the
    original question prompt so it reads naturally in Telegram.

    Prompts like:
      "BELIEF AUDIT — 2 days old, used 0 times\\n\\nI currently believe:\\n  <text>\\n\\nIs this still true?\\nReply: TRUE / FALSE / [correction]"
    become:
      "I believe: <text>\\nStill true?"
    """
    if not prompt:
        return ''
    text = prompt.strip()
    # Drop any trailing "Reply: <format hint>" line — the Telegram
    # preamble already tells the user how to reply
    import re as _re
    # Email templates open with headers like "BELIEF AUDIT — 2 days
    # old" or "OPEN —" — recognise the "CAPS CAPS — rest" shape.
    _HEADER_RE = _re.compile(r'^[A-Z][A-Z\s]+[—-]')

    clean_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            clean_lines.append('')
            continue
        # Drop the category header line (SHOUTY caps followed by dash)
        if _HEADER_RE.match(stripped):
            continue
        # Drop the Reply: format hint line
        if stripped.lower().startswith('reply:'):
            continue
        clean_lines.append(line)
    # Collapse multi blank lines
    out: list[str] = []
    blank = False
    for line in clean_lines:
        if not line.strip():
            if blank:
                continue
            blank = True
        else:
            blank = False
        out.append(line)
    return '\n'.join(out).strip()


# ── Send ────────────────────────────────────────────────────────────

def send_brief_via_telegram(
    conn, *, user_email: str, text: str,
) -> TelegramDeliveryResult:
    """Look up the user's registered chat_id and send the brief.
    Chunks if the text exceeds Telegram's limit."""
    token = (os.getenv('TELEGRAM_BOT_TOKEN') or '').strip()
    if not token:
        return TelegramDeliveryResult(
            ok=False, error='TELEGRAM_BOT_TOKEN not set',
        )

    chat_id = _lookup_chat_id(conn, user_email)
    if chat_id is None:
        return TelegramDeliveryResult(
            ok=False, error=f'no registered chat_id for {user_email}',
        )

    chunks = _chunk_for_telegram(text)
    if not chunks:
        return TelegramDeliveryResult(
            ok=False, chat_id=chat_id, error='empty body',
        )

    message_ids: list[int] = []
    try:
        with httpx.Client(timeout=TELEGRAM_TIMEOUT) as client:
            for chunk in chunks:
                # No parse_mode — brief content contains user + DB
                # text that can't safely be escaped for Telegram's
                # legacy Markdown (underscores in emails, asterisks
                # in quotes, unmatched brackets). Plain text keeps
                # the emoji + layout without parsing risk.
                r = client.post(
                    f'{TELEGRAM_API_BASE}/bot{token}/sendMessage',
                    json={
                        'chat_id': int(chat_id),
                        'text': chunk,
                        'disable_web_page_preview': True,
                    },
                )
                if r.status_code != 200:
                    return TelegramDeliveryResult(
                        ok=False, chat_id=chat_id,
                        message_ids=message_ids,
                        error=f'HTTP {r.status_code}: {r.text[:200]}',
                    )
                data = r.json() or {}
                if not data.get('ok'):
                    return TelegramDeliveryResult(
                        ok=False, chat_id=chat_id,
                        message_ids=message_ids,
                        error=f"telegram error: {data.get('description', '?')}",
                    )
                msg_id = ((data.get('result') or {}).get('message_id'))
                if msg_id:
                    message_ids.append(int(msg_id))
    except Exception as exc:
        return TelegramDeliveryResult(
            ok=False, chat_id=chat_id, message_ids=message_ids,
            error=f'{type(exc).__name__}: {exc}',
        )

    return TelegramDeliveryResult(
        ok=True, chat_id=chat_id, message_ids=message_ids,
    )


def _lookup_chat_id(conn, user_email: str) -> int | None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT chat_id
                     FROM cairn_intel.registered_telegram_chats
                    WHERE user_email = %s
                      AND revoked_at IS NULL
                    ORDER BY registered_at DESC
                    LIMIT 1""",
                (user_email.lower(),),
            )
            row = cur.fetchone()
        return int(row[0]) if row else None
    except Exception as exc:
        logger.warning('[brief/telegram] chat_id lookup failed: %s', exc)
        return None


def _chunk_for_telegram(text: str) -> list[str]:
    """Splits a message at paragraph boundaries where possible, or
    hard-slices at the char limit if no boundary is close."""
    text = (text or '').strip()
    if len(text) <= TELEGRAM_MSG_LIMIT:
        return [text] if text else []
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= TELEGRAM_MSG_LIMIT:
            chunks.append(remaining)
            break
        window = remaining[:TELEGRAM_MSG_LIMIT]
        split_at = window.rfind('\n\n')
        if split_at == -1 or split_at < 500:
            split_at = window.rfind('\n')
        if split_at == -1 or split_at < 500:
            split_at = TELEGRAM_MSG_LIMIT
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    return chunks


# ── Pending-brief lookup (webhook uses this) ───────────────────────

def find_pending_telegram_brief(
    conn, user_email: str, within_hours: int = 48,
) -> dict | None:
    """Look for a memory_brief_run delivered via Telegram to this
    user in the last N hours that has NO response row yet. Used by
    the inbound webhook to decide if a Telegram message should be
    treated as a brief reply (vs general chat)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id::text, r.questions
                     FROM memory_brief_runs r
                     LEFT JOIN memory_brief_responses rsp
                       ON rsp.run_id = r.id
                    WHERE r.user_email = %s
                      AND r.delivery_status = 'sent'
                      AND r.delivered_via = 'telegram'
                      AND r.delivered_at > NOW() - (INTERVAL '1 hour' * %s)
                      AND rsp.id IS NULL
                    ORDER BY r.delivered_at DESC
                    LIMIT 1""",
                (user_email.lower(), int(within_hours)),
            )
            row = cur.fetchone()
    except Exception as exc:
        logger.warning('[brief/telegram] pending lookup failed: %s', exc)
        return None
    if not row:
        return None
    questions_raw = row[1]
    if isinstance(questions_raw, str):
        import json as _json
        try:
            questions_raw = _json.loads(questions_raw)
        except Exception:
            questions_raw = []
    return {
        'run_id': row[0],
        'questions': questions_raw or [],
    }


__all__ = [
    'TelegramDeliveryResult',
    'render_brief_for_telegram',
    'send_brief_via_telegram',
    'find_pending_telegram_brief',
    '_chunk_for_telegram',
]
