"""Email composer for the Memory Brief.

Plain text only. One question per block. Reply-To set to cairn@
so replies land in the existing inbox-poll path (Phase B parses
them from there).

Strategic preamble discipline: fail loud. If SMTP is misconfigured
this module raises; the caller decides whether to record the run
as failed. Never silently no-op.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage

logger = logging.getLogger(__name__)

DEFAULT_FROM = 'cairn@nbnesigns.com'
DEFAULT_REPLY_TO = 'cairn@nbnesigns.com'


@dataclass
class ComposedEmail:
    subject: str
    body: str
    from_addr: str
    reply_to: str


def compose_email(
    user_email: str,
    generated_at: datetime,
    questions: list,         # list[Question] — avoid circular import
    notes: list[str] | None = None,
) -> ComposedEmail:
    """Build the email body. Shape matches what the Phase B parser
    will expect: each question is wrapped with `--- Q<n> (<category>) ---`
    delimiters so block-level parsing is trivial.
    """
    date_str = generated_at.strftime('%Y-%m-%d')
    subject = f'Deek morning brief — {date_str}'

    lines: list[str] = [
        f'Deek morning brief — {date_str}',
        '=' * 60,
        '',
        f'{len(questions)} question{"s" if len(questions) != 1 else ""} for you today.',
        'Reply to this email to answer. One block per question — keep the',
        'Q<n> headers in place so I can parse your replies correctly.',
        '',
    ]
    for i, q in enumerate(questions, 1):
        lines.append(f'--- Q{i} ({q.category}) ---')
        lines.append(q.prompt)
        lines.append('')
        lines.append(f'(Expected reply format: {q.reply_format})')
        lines.append('')

    if notes:
        lines.append('--- Generator notes (for debugging, you can ignore) ---')
        for n in notes:
            lines.append(f'- {n}')
        lines.append('')

    lines.append('— Deek')

    return ComposedEmail(
        subject=subject,
        body='\n'.join(lines),
        from_addr=os.getenv('SMTP_FROM', DEFAULT_FROM),
        reply_to=os.getenv('DEEK_BRIEF_REPLY_TO', DEFAULT_REPLY_TO),
    )


# ── SMTP send ────────────────────────────────────────────────────────

class SMTPNotConfigured(RuntimeError):
    """Raised when SMTP creds are absent. Caller chooses to fail or
    fall back to dry-run."""


def _smtp_cfg() -> dict:
    host = (os.getenv('SMTP_HOST') or '').strip()
    user = (os.getenv('SMTP_USER') or '').strip()
    password = (os.getenv('SMTP_PASS') or '').strip()
    if not (host and user and password):
        raise SMTPNotConfigured('SMTP_HOST / SMTP_USER / SMTP_PASS must all be set')
    try:
        port = int((os.getenv('SMTP_PORT') or '587').strip())
    except ValueError:
        port = 587
    return {
        'host': host, 'port': port, 'user': user, 'password': password,
    }


def send_via_smtp(email: ComposedEmail, to_addr: str) -> None:
    """Raises on any failure. Caller records delivery_status='failed'
    on catch.
    """
    cfg = _smtp_cfg()
    msg = EmailMessage()
    msg['Subject'] = email.subject
    msg['From'] = email.from_addr
    msg['To'] = to_addr
    msg['Reply-To'] = email.reply_to
    msg.set_content(email.body)
    context = ssl.create_default_context()
    with smtplib.SMTP(cfg['host'], cfg['port'], timeout=30) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(cfg['user'], cfg['password'])
        server.send_message(msg)


__all__ = ['ComposedEmail', 'compose_email', 'send_via_smtp', 'SMTPNotConfigured']
