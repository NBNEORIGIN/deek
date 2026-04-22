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

    # Personalise the welcome line when we have a profile for this
    # recipient (Memory Brief Tier 2). Falls back to unpersonalised
    # for director-tier / unknown users.
    display_name = ''
    try:
        from .user_profile import get_profile
        display_name = (get_profile(user_email).display_name or '').strip()
    except Exception:
        display_name = ''
    greeting = (
        f'Hi {display_name},' if display_name else 'Deek morning brief —'
    )

    lines: list[str] = [
        f'Deek morning brief — {date_str}',
        '=' * 60,
        '',
        greeting,
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

    # arXiv Stage 3 — append any auto-drafted research briefs the
    # user should know about. Fires opportunistically; silent if
    # nothing's been drafted recently.
    try:
        drafted = _list_recent_research_drafts()
    except Exception:
        drafted = []
    if drafted:
        lines.append('=' * 60)
        lines.append('DRAFTED RESEARCH BRIEFS — ready for review')
        lines.append('=' * 60)
        lines.append('')
        for d in drafted:
            lines.append(f"  • {d['brief_path']}")
            lines.append(f"    arxiv {d['arxiv_id']} — {d['title'][:80]}")
            if d.get('applicability_score') is not None:
                lines.append(
                    f"    applicability: {d['applicability_score']:.1f}/10"
                )
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


def _list_recent_research_drafts(hours: int = 24) -> list[dict]:
    """Briefs drafted by the arXiv Stage 3 autodrafter in the last
    N hours. Returns at most 5 to keep the email compact. Never
    raises — missing DB or table gives []."""
    try:
        import psycopg2
        db_url = os.getenv('DATABASE_URL', '')
        if not db_url:
            return []
        with psycopg2.connect(db_url, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT arxiv_id, title, brief_path,
                              applicability_score
                         FROM cairn_intel.arxiv_candidates
                        WHERE brief_drafted_at IS NOT NULL
                          AND brief_drafted_at > NOW() - (INTERVAL '1 hour' * %s)
                          AND brief_path IS NOT NULL
                        ORDER BY brief_drafted_at DESC
                        LIMIT 5""",
                    (hours,),
                )
                rows = cur.fetchall()
    except Exception:
        return []
    return [
        {
            'arxiv_id': r[0],
            'title': r[1] or '',
            'brief_path': r[2] or '',
            'applicability_score': float(r[3]) if r[3] is not None else None,
        }
        for r in rows
    ]


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


def send_via_smtp(email: ComposedEmail, to_addr: str) -> str:
    """Raises on any failure. Caller records delivery_status='failed'
    on catch.

    Returns the outgoing ``Message-ID`` header we injected so the
    caller can persist it against the brief run — replies then
    correlate via ``In-Reply-To`` instead of by date, which is
    what eliminated the cross-day misattribution bug on 2026-04-22.
    """
    from email.utils import make_msgid
    cfg = _smtp_cfg()
    msg = EmailMessage()
    msg['Subject'] = email.subject
    msg['From'] = email.from_addr
    msg['To'] = to_addr
    msg['Reply-To'] = email.reply_to
    # make_msgid() generates an RFC-5322-compliant id of the form
    # <timestamp.random@domain>. We pick a stable domain so the
    # reply processor can filter by suffix if needed.
    message_id = make_msgid(domain='deek.nbnesigns.co.uk')
    msg['Message-ID'] = message_id
    msg.set_content(email.body)
    context = ssl.create_default_context()
    with smtplib.SMTP(cfg['host'], cfg['port'], timeout=30) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(cfg['user'], cfg['password'])
        server.send_message(msg)
    return message_id


__all__ = ['ComposedEmail', 'compose_email', 'send_via_smtp', 'SMTPNotConfigured']
