"""
Email triage runner — the main pipeline.

Reads unprocessed emails from cairn_email_raw, classifies each one
via Haiku, matches existing_project_replies to CRM projects, runs
analyze_enquiry on new_enquiries, and writes a row per email into
cairn_intel.email_triage. Does NOT send any emails — that's the
digest_sender's job, which runs separately.

Safety rails:
    - Kill switch: CAIRN_EMAIL_TRIAGE_ENABLED must be 'true'
    - Loop prevention: skips emails from cairn@nbnesigns.com
    - Mailbox whitelist: only processes {toby, sales}
    - Idempotent: email_triage UNIQUE constraint drops repeats
    - Max budget: --max-emails cap per run (default 20) so a burst
      of mail can't blow the Haiku budget in one go
    - 7-day window: only processes emails from the last 7 days
      (older ones are archive, not actionable)

Usage:
    python -m scripts.email_triage.triage_runner                   # dry run
    python -m scripts.email_triage.triage_runner --commit           # writes
    python -m scripts.email_triage.triage_runner --max-emails 50    # cap
    python -m scripts.email_triage.triage_runner --window-days 3    # shorter
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2
from dotenv import load_dotenv

load_dotenv(override=True)

from core.intel.db import (
    ensure_schema as intel_ensure_schema,
    upsert_email_triage,
    already_triaged_message_ids,
)


log = logging.getLogger('cairn.email_triage.runner')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')


ALLOWED_MAILBOXES = ['cairn']
# Note: cairn@ receives IONOS mail-forwarded copies of toby@ and
# sales@ traffic, so the production whitelist is just ['cairn'].
# Loop prevention still filters anything Cairn itself sends to
# itself — see LOOP_PREVENTION_SENDER_PATTERNS below.
LOOP_PREVENTION_SENDER_PATTERNS = [
    'cairn@nbnesigns.com',
    'cairn@nbnesigns.co.uk',
    # Toby's own outbound replies shouldn't normally hit cairn@
    # (IONOS forwards inbound only), but guard anyway in case a
    # client CCs him and he replies-all.
    'toby@nbnesigns.com',
    'toby@nbnesigns.co.uk',
    'sales@nbnesigns.com',
    'sales@nbnesigns.co.uk',
]


def is_triage_enabled() -> bool:
    return os.getenv('CAIRN_EMAIL_TRIAGE_ENABLED', 'false').strip().lower() in {
        'true', '1', 'yes', 'on',
    }


def fetch_candidate_emails(
    db_url: str,
    window_days: int,
    max_emails: int,
    mailboxes: list[str] | None = None,
) -> list[dict]:
    """Pull emails from cairn_email_raw inside the triage window.

    ``mailboxes`` defaults to ALLOWED_MAILBOXES when None. Passing an
    explicit list overrides the default — used by smoke tests to
    exercise the pipeline against whatever mailboxes are actually
    ingested to the current DB.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=window_days)
    whitelist = mailboxes if mailboxes else ALLOWED_MAILBOXES
    conn = psycopg2.connect(db_url, connect_timeout=8)
    try:
        with conn.cursor() as cur:
            cur.execute('SET statement_timeout = 60000')
            mailbox_filters = '(' + ' OR '.join(
                ['mailbox = %s'] * len(whitelist)
            ) + ')'
            # Note: loop prevention is done in Python below because
            # the sender field contains full addresses with mixed
            # case and display names, which is messy in SQL.
            sql = f"""
                SELECT id, message_id, mailbox, sender, subject,
                       body_text, received_at
                FROM cairn_email_raw
                WHERE body_text IS NOT NULL
                  AND {mailbox_filters}
                  AND received_at >= %s
                ORDER BY received_at DESC
                LIMIT %s
            """
            params: list[Any] = list(whitelist) + [cutoff, max_emails * 3]
            cur.execute(sql, params)
            rows = cur.fetchall()
            col_names = [d[0] for d in cur.description]
    finally:
        conn.close()

    out: list[dict] = []
    for row in rows:
        d = dict(zip(col_names, row))
        # Loop prevention — drop emails sent from cairn@ itself
        sender = (d.get('sender') or '').lower()
        if any(p in sender for p in LOOP_PREVENTION_SENDER_PATTERNS):
            continue
        out.append(d)
    return out[:max_emails]


def _daily_triage_count(db_url: str) -> int:
    """Count triage rows written today (UTC) to enforce daily budget."""
    conn = psycopg2.connect(db_url, connect_timeout=8)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM cairn_intel.email_triage "
                "WHERE triaged_at >= CURRENT_DATE"
            )
            return cur.fetchone()[0]
    except Exception:
        return 0
    finally:
        conn.close()


# Default daily budget — overridable via CAIRN_TRIAGE_DAILY_LIMIT env var.
DEFAULT_DAILY_LIMIT = 50


def run_triage(
    *,
    commit: bool,
    max_emails: int,
    window_days: int,
    mailboxes: list[str] | None = None,
) -> int:
    """Execute one pass of the triage pipeline.

    Returns the number of rows written to email_triage (0 in dry-run).
    ``mailboxes`` optionally overrides ALLOWED_MAILBOXES — used by smoke
    tests to process whatever mailbox is actually ingested in the DB.
    """
    if not is_triage_enabled():
        log.warning(
            'triage_runner: CAIRN_EMAIL_TRIAGE_ENABLED is not set — '
            'aborting. Set the env var to "true" to enable.'
        )
        return 0

    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        log.error('triage_runner: DATABASE_URL not set')
        return 0

    # Ensure schema — if this is the first run, the email_triage table
    # may not exist yet (cairn-api startup creates it, but for a direct
    # cron invocation we want the safety net).
    try:
        intel_ensure_schema(db_url=db_url)
    except Exception as exc:
        log.warning('triage_runner: ensure_schema raised: %s', exc)

    # ── Daily budget cap ──────────────────────────────────────────────
    daily_limit = int(os.getenv('CAIRN_TRIAGE_DAILY_LIMIT', str(DEFAULT_DAILY_LIMIT)))
    today_count = _daily_triage_count(db_url)
    if today_count >= daily_limit:
        log.info(
            'triage_runner: daily budget exhausted (%d/%d) — skipping run',
            today_count, daily_limit,
        )
        return 0
    # Reduce max_emails to stay within budget for the rest of the day
    remaining = daily_limit - today_count
    if max_emails > remaining:
        log.info(
            'triage_runner: capping max_emails from %d to %d (daily budget)',
            max_emails, remaining,
        )
        max_emails = remaining

    emails = fetch_candidate_emails(db_url, window_days, max_emails, mailboxes=mailboxes)
    log.info(
        'triage_runner: fetched %d candidate emails (window=%d days, cap=%d)',
        len(emails), window_days, max_emails,
    )
    if not emails:
        return 0

    # Dedupe against existing triage rows
    message_ids = [e['message_id'] for e in emails if e.get('message_id')]
    already_seen = already_triaged_message_ids(message_ids, db_url=db_url)
    emails = [e for e in emails if e.get('message_id') not in already_seen]
    log.info('triage_runner: %d emails after dedupe against email_triage', len(emails))
    if not emails:
        return 0

    from .classifier import classify_email
    from .project_matcher import match_project

    written = 0
    for email in emails:
        try:
            triage_row = _process_one(email, commit=commit)
        except Exception as exc:
            log.exception('triage_runner: error processing %s: %s',
                          email.get('message_id'), exc)
            triage_row = {
                'email_message_id': email.get('message_id'),
                'email_mailbox': email.get('mailbox') or '',
                'email_sender': email.get('sender'),
                'email_subject': email.get('subject'),
                'email_received_at': email.get('received_at'),
                'classification': 'error',
                'classification_confidence': 'low',
                'classification_reason': f'{type(exc).__name__}: {exc}',
                'client_name_guess': None,
                'project_id': None,
                'project_match_score': None,
                'analyzer_brief': None,
                'analyzer_job_size': None,
                'skip_reason': 'processing exception',
            }

        if commit:
            upsert_email_triage(triage_row, db_url=db_url)
            written += 1
        else:
            log.info(
                '[DRY-RUN] would upsert triage row: message=%s classification=%s',
                triage_row.get('email_message_id'),
                triage_row.get('classification'),
            )

    log.info('triage_runner: done — written=%d (commit=%s)', written, commit)
    return written


def _process_one(email: dict, commit: bool) -> dict:
    """Run classification → project match → analyzer for a single email.

    Returns the dict ready for upsert_email_triage.
    """
    from .classifier import classify_email
    from .project_matcher import match_project

    log.info(
        'processing message_id=%s mailbox=%s sender=%s subject=%r',
        email.get('message_id'),
        email.get('mailbox'),
        email.get('sender'),
        (email.get('subject') or '')[:80],
    )

    classification = classify_email(email)
    classification_name = classification['classification']

    # Start building the triage row
    row: dict = {
        'email_message_id': email.get('message_id'),
        'email_mailbox': email.get('mailbox') or '',
        'email_sender': email.get('sender'),
        'email_subject': email.get('subject'),
        'email_received_at': email.get('received_at'),
        'classification': classification_name,
        'classification_confidence': classification.get('confidence', 'medium'),
        'classification_reason': classification.get('reason'),
        'client_name_guess': classification.get('client_name_guess') or None,
        'project_id': None,
        'project_match_score': None,
        'analyzer_brief': None,
        'analyzer_job_size': None,
        'skip_reason': None,
    }

    if classification_name in {'automation', 'personal', 'unclassified'}:
        row['skip_reason'] = f'classified as {classification_name}'
        return row

    if classification_name == 'existing_project_reply':
        match = match_project(email, classification)
        row['project_id'] = match.get('project_id') or None
        row['project_match_score'] = match.get('match_score')
        # For existing-project replies we don't run analyze_enquiry —
        # a summary line in the digest is enough. The runner still
        # records a triage row so the digest sender has something
        # to deliver.
        return row

    if classification_name == 'new_enquiry':
        # Run analyze_enquiry on the email body. Best-effort — if
        # the analyzer fails we still record the classification and
        # the digest email just says "analyzer unavailable".
        try:
            from core.tools.enquiry_analyzer import _analyze_enquiry
            body_text = (email.get('body_text') or '').strip()
            subject = (email.get('subject') or '').strip()
            sender = (email.get('sender') or '').strip()
            enquiry_text = (
                f'From: {sender}\n'
                f'Subject: {subject}\n\n'
                f'{body_text}'
            )
            brief = _analyze_enquiry(project_root='', enquiry=enquiry_text)
            row['analyzer_brief'] = brief
            row['analyzer_job_size'] = _extract_job_size(brief)
        except Exception as exc:
            log.warning(
                'analyzer failed on %s: %s',
                email.get('message_id'), exc,
            )
            row['analyzer_brief'] = f'Analyzer failed: {type(exc).__name__}: {exc}'

        # Try to match to an existing project too (for reference, even
        # if it's a new enquiry — could help surface repeat clients)
        match = match_project(email, classification)
        if match.get('project_id'):
            row['project_id'] = match['project_id']
            row['project_match_score'] = match.get('match_score')

    return row


def _extract_job_size(brief: str | None) -> str | None:
    """Pull job_size from the analyzer's provenance footer."""
    if not brief:
        return None
    # The analyzer emits "job_size: small" (or mid/large) in the
    # provenance footer. Grep for it.
    import re
    match = re.search(r'job_size[:\s=]+(small|mid|large)', brief, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='python -m scripts.email_triage.triage_runner',
        description='Classify + analyze incoming emails into cairn_intel.email_triage',
    )
    parser.add_argument('--commit', action='store_true',
                        help='Write triage rows to DB (default: dry-run)')
    parser.add_argument('--max-emails', type=int, default=5,
                        help='Max emails to process per run (default 5)')
    parser.add_argument('--window-days', type=int, default=7,
                        help='Process emails received in the last N days (default 7)')
    parser.add_argument('--mailbox', action='append', default=None,
                        help='Override mailbox whitelist (repeatable). '
                             'Defaults to toby+sales. Used by smoke tests to '
                             'point the pipeline at whatever mailbox is '
                             'actually ingested (e.g. --mailbox cairn).')
    args = parser.parse_args(argv)

    written = run_triage(
        commit=args.commit,
        max_emails=args.max_emails,
        window_days=args.window_days,
        mailboxes=args.mailbox,
    )
    print(f'[triage_runner] done written={written} commit={args.commit}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
