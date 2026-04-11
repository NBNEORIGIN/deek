"""
Email triage digest sender.

Reads unsent triage rows from cairn_intel.email_triage and delivers
a digest email to Toby for each one. Two modes:

    - SMTP mode: if SMTP_HOST / SMTP_USER / SMTP_PASS env vars are
      set, send real email via smtplib (IONOS by default).
    - Dry-run mode: if credentials are missing, log what WOULD be
      sent and mark the triage row with send_dry_run=True so the
      runner knows the brief was "delivered" (to the logs) and
      shouldn't be re-sent.

In Mode A the digest always goes TO toby@nbnesigns.com regardless
of who the original sender was — we never reply to the client
directly.

Safety rails:
    - Kill switch: CAIRN_EMAIL_TRIAGE_ENABLED must be 'true'
    - Hard cap per run (--max-per-run, default 20) so a backlog
      of triage rows can't blast the inbox in one go
    - send_error recorded on the triage row on SMTP failure
    - Each row marked sent_to_toby_at on success so the sender
      never re-delivers the same brief twice

Usage:
    python -m scripts.email_triage.digest_sender                # dry run
    python -m scripts.email_triage.digest_sender --commit        # sends
    python -m scripts.email_triage.digest_sender --max-per-run 5
"""
from __future__ import annotations

import argparse
import logging
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(override=True)

from core.intel.db import (
    load_unsent_triage_drafts,
    mark_triage_sent,
)


log = logging.getLogger('cairn.email_triage.sender')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')


DEFAULT_DIGEST_TO = 'toby@nbnesigns.com'
DEFAULT_DIGEST_FROM = 'cairn@nbnesigns.com'


def is_triage_enabled() -> bool:
    return os.getenv('CAIRN_EMAIL_TRIAGE_ENABLED', 'false').strip().lower() in {
        'true', '1', 'yes', 'on',
    }


def smtp_config() -> dict | None:
    """Return an SMTP config dict if all required env vars are set,
    otherwise None (which triggers dry-run mode)."""
    host = os.getenv('SMTP_HOST', '').strip()
    port_raw = os.getenv('SMTP_PORT', '587').strip()
    user = os.getenv('SMTP_USER', '').strip()
    password = os.getenv('SMTP_PASS', '').strip()
    if not (host and user and password):
        return None
    try:
        port = int(port_raw)
    except ValueError:
        port = 587
    return {
        'host': host,
        'port': port,
        'user': user,
        'password': password,
        'from_addr': os.getenv('SMTP_FROM', DEFAULT_DIGEST_FROM),
    }


def send_via_smtp(
    cfg: dict,
    to_addr: str,
    subject: str,
    body: str,
) -> None:
    """Send a plain-text email via SMTP. Raises on failure."""
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = cfg['from_addr']
    msg['To'] = to_addr
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP(cfg['host'], cfg['port'], timeout=30) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(cfg['user'], cfg['password'])
        server.send_message(msg)


def format_digest_body(row: dict) -> tuple[str, str]:
    """Return (subject, body) for a triage row.

    Subject pattern:
        [Cairn] {classification} — {original_subject}
    Body is a structured summary followed by the original enquiry.
    """
    classification = row.get('classification', 'unclassified')
    original_subject = (row.get('email_subject') or '(no subject)').strip()
    sender = (row.get('email_sender') or '(unknown)').strip()
    received = row.get('email_received_at')
    received_iso = received.strftime('%Y-%m-%d %H:%M UTC') if received else '?'
    mailbox = row.get('email_mailbox', '?')

    subject = f'[Cairn] {classification} — {original_subject}'[:200]

    header = [
        f'Classification: {classification}',
        f'Confidence:     {row.get("classification_confidence") or "?"}',
        f'Mailbox:        {mailbox}@',
        f'From:           {sender}',
        f'Received:       {received_iso}',
    ]
    client_guess = row.get('client_name_guess')
    if client_guess:
        header.append(f'Client:         {client_guess}')
    project_id = row.get('project_id')
    if project_id:
        header.append(f'Matched project: {project_id}')
    job_size = row.get('analyzer_job_size')
    if job_size:
        header.append(f'Job size:       {job_size}')

    body_parts: list[str] = []
    body_parts.append('\n'.join(header))
    body_parts.append('')

    analyzer_brief = row.get('analyzer_brief')
    if classification == 'new_enquiry':
        if analyzer_brief:
            # Strip the verbatim-wrapper sentinels if present so the
            # recipient sees the clean brief.
            cleaned = _strip_verbatim_wrapper(analyzer_brief)
            body_parts.append('=' * 60)
            body_parts.append('ANALYZER BRIEF')
            body_parts.append('=' * 60)
            body_parts.append('')
            body_parts.append(cleaned)
        else:
            body_parts.append('_(analyzer brief not available)_')
    elif classification == 'existing_project_reply':
        body_parts.append(
            f'This looks like a follow-up on an existing project '
            f'({project_id or "no match found"}). No new analyzer '
            f'brief generated — see the original message below.'
        )

    body_parts.append('')
    body_parts.append('=' * 60)
    body_parts.append('ORIGINAL MESSAGE')
    body_parts.append('=' * 60)
    body_parts.append(f'From: {sender}')
    body_parts.append(f'Subject: {original_subject}')
    body_parts.append(f'Received: {received_iso}')
    body_parts.append('')
    # The body is not in the triage row — we just reference the message_id
    body_parts.append(
        f'(See message_id {row.get("email_message_id")} in cairn_email_raw '
        f'for the full body. This digest is a summary + analyzer brief only.)'
    )

    body_parts.append('')
    body_parts.append('---')
    body_parts.append(
        'You are receiving this because Cairn triaged a new incoming '
        'email. Reply to toby@nbnesigns.com as normal — this digest '
        'is one-way, your reply will not come back to Cairn.'
    )

    return subject, '\n'.join(body_parts)


def _strip_verbatim_wrapper(brief: str) -> str:
    """Remove the STRICT VERBATIM sentinel markers from the analyzer
    output so the recipient sees a clean brief."""
    if not brief:
        return ''
    text = brief
    # Drop the instruction header
    if 'STRICT VERBATIM OUTPUT' in text:
        start = text.find('<<<ANALYZER_BRIEF_START>>>')
        if start >= 0:
            text = text[start + len('<<<ANALYZER_BRIEF_START>>>'):]
    end = text.rfind('<<<ANALYZER_BRIEF_END>>>')
    if end >= 0:
        text = text[:end]
    return text.strip()


def push_recommendation_to_crm(row: dict) -> str | None:
    """Push a Mode-A cairn_recommendations row to the CRM.

    Returns the recommendation ID from the CRM response, or None on
    failure (which is non-fatal — the digest email still goes out).
    """
    token = os.getenv('CAIRN_API_KEY', '').strip()
    if not token:
        return None
    base_url = os.getenv('CRM_BASE_URL', 'https://crm.nbnesigns.co.uk').rstrip('/')

    classification = row.get('classification', 'unclassified')
    sender = row.get('email_sender') or '?'
    subject = row.get('email_subject') or '(no subject)'
    client_guess = row.get('client_name_guess') or ''

    if classification == 'new_enquiry':
        message = (
            f'New enquiry from {client_guess or sender}: "{subject[:100]}". '
            f'Analyzer brief available in Cairn email_triage row #{row.get("id")}.'
        )
        priority = 'medium'
    elif classification == 'existing_project_reply':
        message = (
            f'Reply from {sender} on project {row.get("project_id") or "(unmatched)"}: '
            f'"{subject[:100]}".'
        )
        priority = 'low'
    else:
        return None

    try:
        import httpx
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                f'{base_url}/api/cairn/memory',
                json={
                    'type': 'recommendation',
                    'priority': priority,
                    'message': message,
                    'project_id': row.get('project_id') or None,
                    'source_modules': ['cairn', 'email_triage'],
                },
                headers={
                    'Authorization': f'Bearer {token}',
                },
            )
    except Exception as exc:
        log.warning('push_recommendation: CRM POST failed: %s', exc)
        return None

    if response.status_code not in (200, 201):
        log.warning(
            'push_recommendation: CRM returned HTTP %d — %s',
            response.status_code, response.text[:200],
        )
        return None

    try:
        data = response.json()
        return data.get('id')
    except Exception:
        return None


def run_digest(
    *,
    commit: bool,
    max_per_run: int,
) -> int:
    if not is_triage_enabled():
        log.warning(
            'digest_sender: CAIRN_EMAIL_TRIAGE_ENABLED is not set — '
            'aborting. Set the env var to "true" to enable.'
        )
        return 0

    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        log.error('digest_sender: DATABASE_URL not set')
        return 0

    drafts = load_unsent_triage_drafts(db_url=db_url, limit=max_per_run)
    if not drafts:
        log.info('digest_sender: no unsent drafts')
        return 0

    cfg = smtp_config()
    digest_to = os.getenv('CAIRN_TRIAGE_DIGEST_TO', DEFAULT_DIGEST_TO)

    log.info(
        'digest_sender: %d drafts to process, smtp=%s, to=%s, commit=%s',
        len(drafts),
        'configured' if cfg else 'missing (dry-run)',
        digest_to,
        commit,
    )

    sent = 0
    for row in drafts:
        subject, body = format_digest_body(row)
        dry_run = cfg is None
        send_error: str | None = None
        crm_rec_id: str | None = None

        if not commit:
            log.info('[DRY-RUN] would send subject=%r', subject)
            continue

        if dry_run:
            log.info(
                '[SMTP MISSING] dry-run log: to=%s subject=%r body_len=%d',
                digest_to, subject, len(body),
            )
        else:
            try:
                send_via_smtp(cfg, digest_to, subject, body)
                log.info('sent digest for triage_id=%s', row.get('id'))
            except Exception as exc:
                send_error = f'{type(exc).__name__}: {exc}'
                log.error(
                    'SMTP send failed for triage_id=%s: %s',
                    row.get('id'), send_error,
                )

        crm_rec_id = push_recommendation_to_crm(row)

        mark_triage_sent(
            triage_id=row['id'],
            dry_run=dry_run,
            send_error=send_error,
            crm_recommendation_id=crm_rec_id,
            db_url=db_url,
        )
        if send_error is None:
            sent += 1

    log.info('digest_sender: sent=%d / drafts=%d', sent, len(drafts))
    return sent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='python -m scripts.email_triage.digest_sender',
        description='Deliver triage digests to Toby via SMTP (or dry-run log)',
    )
    parser.add_argument('--commit', action='store_true')
    parser.add_argument('--max-per-run', type=int, default=20)
    args = parser.parse_args(argv)
    sent = run_digest(commit=args.commit, max_per_run=args.max_per_run)
    print(f'[digest_sender] done sent={sent} commit={args.commit}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
