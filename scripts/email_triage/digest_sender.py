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
    - Kill switch: DEEK_EMAIL_TRIAGE_ENABLED must be 'true'
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
    return (os.getenv('DEEK_EMAIL_TRIAGE_ENABLED') or os.getenv('CAIRN_EMAIL_TRIAGE_ENABLED', 'false')).strip().lower() in {
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


def _build_candidates_block(row: dict) -> list[str]:
    """Render the top-N candidate projects block for the digest.

    Accepts the JSONB match_candidates field (already parsed to a
    Python list/dict by psycopg2) or falls back to the legacy single
    project_id when the field is absent.
    """
    import json as _json
    cands = row.get('match_candidates')
    if isinstance(cands, str):
        try:
            cands = _json.loads(cands)
        except Exception:
            cands = None
    if not cands:
        pid = row.get('project_id') or '(no match found)'
        return [
            '=' * 60,
            'PROJECT MATCH',
            '=' * 60,
            '',
            f'  {pid}',
            '',
        ]

    lines = [
        '=' * 60,
        'CANDIDATE PROJECTS (top guess + alternatives)',
        '=' * 60,
        '',
    ]
    for i, c in enumerate(cands, 1):
        marker = '->' if i == 1 else '  '
        name = c.get('project_name') or '(unnamed)'
        pid = c.get('project_id') or '(no id)'
        score = float(c.get('match_score') or 0.0)
        last = c.get('last_activity_at') or ''
        status = c.get('status') or ''
        lines.append(f'  {marker} {i}. {name}')
        lines.append(f'       id:       {pid}')
        lines.append(f'       score:    {score:.3f}')
        if last:
            lines.append(f'       last:     {last[:19]}')
        if status:
            lines.append(f'       status:   {status}')
        excerpt = (c.get('excerpt') or '').strip()
        if excerpt:
            for line in excerpt.splitlines()[:3]:
                lines.append(f'       | {line}')
        lines.append('')
    return lines


def _build_draft_block(row: dict) -> list[str]:
    """Render the drafted reply block, or a reason for its absence."""
    draft = (row.get('draft_reply') or '').strip()
    lines = [
        '=' * 60,
        'PROPOSED REPLY (draft — edit before sending)',
        '=' * 60,
        '',
    ]
    if not draft:
        lines.append('  (no draft — insufficient context or drafter error)')
        lines.append('')
        return lines
    for line in draft.splitlines():
        lines.append(f'  {line}')
    lines.append('')
    model = row.get('draft_model') or 'local'
    lines.append(f'  (drafted by: {model})')
    lines.append('')
    return lines


def _build_similar_jobs_block(jobs: list) -> list[str]:
    """Phase D — render the top-N similar past jobs below candidates.

    If ``jobs`` is empty, returns an empty list (caller renders nothing,
    so the digest stays tight when there's no similarity signal).
    """
    if not jobs:
        return []
    lines = [
        '=' * 60,
        'SIMILAR PAST JOBS (for pricing / spec context)',
        '=' * 60,
        '',
    ]
    for i, j in enumerate(jobs, 1):
        name = (j.project_name or '(unnamed)')[:80]
        client = j.client_name or '—'
        lines.append(f'  {i}. [{j.project_id}] {name}')
        lines.append(f'       client:   {client}')
        bits: list[str] = []
        if j.quoted_amount is not None:
            bits.append(f'quoted £{j.quoted_amount:,.0f}')
        if j.status:
            bits.append(j.status)
        if bits:
            lines.append(f'       outcome:  ' + ' · '.join(bits))
        if j.summary:
            lines.append(f'       | {j.summary}')
        lines.append(f'       match:    {j.score:.3f}')
        lines.append('')
    return lines


def _build_reply_back_block(row: dict, include_q5: bool = False) -> list[str]:
    """The structured answer block Toby fills in. Phase B parses this
    same shape back into CRM updates + memory corrections.

    Q5 is added when a similar-jobs block was rendered (Phase D
    post-cutover). Shadow mode hides it so we don't ask about jobs
    Toby can't see.
    """
    lines = [
        '=' * 60,
        'YOUR ANSWER (reply to this email; keep the Q<n> headers intact)',
        '=' * 60,
        '',
        '--- Q1 (match_confirm) ---',
        '  Is the #1 candidate the correct project?',
        '  Reply: YES / NO / [candidate number 1-3]',
        '',
        '--- Q2 (reply_approval) ---',
        '  Use the drafted reply above?',
        '  Reply: USE / EDIT: <new text> / REJECT',
        '',
        '--- Q3 (project_folder) ---',
        '  Where does this project live on disk? (optional — skip to pass)',
        '  Reply: <path, e.g. D:\\NBNE\\Projects\\M1234-flowers-by-julie>',
        '',
        '--- Q4 (notes) ---',
        '  Anything else Deek should remember about this client / project?',
        '  Reply: (free text, optional)',
        '',
    ]
    if include_q5:
        lines.extend([
            '--- Q5 (similar_job_useful) ---',
            '  Which similar past job above helped you quote this one?',
            '  Reply: 1 / 2 / 3 / SKIP',
            '',
        ])
    return lines


def format_digest_body(row: dict) -> tuple[str, str]:
    """Return (subject, body) for a triage row.

    Subject pattern:
        [Deek] {classification} — {original_subject}
    Body is a structured summary followed by the original enquiry.
    """
    classification = row.get('classification', 'unclassified')
    original_subject = (row.get('email_subject') or '(no subject)').strip()
    sender = (row.get('email_sender') or '(unknown)').strip()
    received = row.get('email_received_at')
    received_iso = received.strftime('%Y-%m-%d %H:%M UTC') if received else '?'
    mailbox = row.get('email_mailbox', '?')

    subject = f'[Deek] {classification} — {original_subject}'[:200]

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
        # Phase A (2026-04-21): the previous "no match found — see
        # original message below" digest was identical every time
        # and trained Toby to ignore it. Now we surface top-N
        # candidates + drafted reply + structured reply-back block
        # so a single email closes the loop.
        body_parts.extend(_build_candidates_block(row))
        body_parts.extend(_build_draft_block(row))

        # Phase D: similar past jobs. Always run the query (logs to
        # triage_similarity_debug either way). The rendered block
        # + Q5 are gated by DEEK_SIMILARITY_SHADOW.
        similar_jobs, rendered = _similar_jobs_for_digest(row)
        if rendered and similar_jobs:
            body_parts.extend(_build_similar_jobs_block(similar_jobs))
        body_parts.extend(_build_reply_back_block(
            row, include_q5=(rendered and bool(similar_jobs)),
        ))

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
        'You are receiving this because Deek triaged a new incoming '
        'email. Reply to toby@nbnesigns.com as normal — this digest '
        'is one-way, your reply will not come back to Deek.'
    )

    return subject, '\n'.join(body_parts)


def _similar_jobs_for_digest(row: dict) -> tuple[list, bool]:
    """Phase D helper. Runs find_and_log() (always, for audit) and
    returns (jobs, should_render).

    ``should_render`` is True iff DEEK_SIMILARITY_SHADOW is off. In
    shadow mode the query still runs and the debug table still gets
    a row, but the digest block + Q5 are suppressed.

    Never raises. On any error returns ([], False).
    """
    try:
        from core.triage.similar_jobs import find_and_log, is_similarity_shadow
    except Exception as exc:
        log.warning('_similar_jobs_for_digest: import failed: %s', exc)
        return [], False

    enquiry = (
        row.get('analyzer_brief')
        or row.get('email_subject')
        or ''
    )
    enquiry = _strip_verbatim_wrapper(enquiry) if enquiry else ''
    if not enquiry:
        return [], False

    try:
        import psycopg2
        db_url = os.getenv('DATABASE_URL', '')
        if not db_url:
            return [], False
        with psycopg2.connect(db_url, connect_timeout=5) as conn:
            jobs = find_and_log(
                conn,
                triage_id=int(row.get('id') or 0),
                enquiry_summary=enquiry,
                client_name=row.get('client_name_guess'),
                exclude_project_id=row.get('project_id'),
            )
    except Exception as exc:
        log.warning('_similar_jobs_for_digest: query failed: %s', exc)
        return [], False

    should_render = not is_similarity_shadow()
    return jobs, should_render


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
    token = (os.getenv('DEEK_API_KEY') or os.getenv('CAIRN_API_KEY') or os.getenv('CLAW_API_KEY', '')).strip()
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
            f'Analyzer brief available in Deek email_triage row #{row.get("id")}.'
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
            'digest_sender: DEEK_EMAIL_TRIAGE_ENABLED is not set — '
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
    digest_to = os.getenv('DEEK_TRIAGE_DIGEST_TO') or os.getenv('CAIRN_TRIAGE_DIGEST_TO', DEFAULT_DIGEST_TO)

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
