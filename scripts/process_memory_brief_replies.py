#!/usr/bin/env python3
"""Memory Brief reply processor — runs after the IMAP inbox poll.

Scans claw_code_chunks for recent email chunks whose subject matches
the Memory Brief reply pattern, parses each, applies corrections to
memory, and records the response in memory_brief_responses.

Idempotent — a reply already present in memory_brief_responses (keyed
by run_id + raw body hash) is skipped.

Runs on Hetzner cron at :05 and :35 — 5 minutes after the inbox poll
has had time to index new mail.

Usage:
    python scripts/process_memory_brief_replies.py
    python scripts/process_memory_brief_replies.py --since 24      # hours
    python scripts/process_memory_brief_replies.py --dry-run       # parse only
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


log = logging.getLogger('memory-brief-replies')


def _connect():
    import psycopg2
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        raise RuntimeError('DATABASE_URL not set')
    return psycopg2.connect(db_url, connect_timeout=5)


def _fetch_candidate_emails(conn, since_hours: int) -> list[tuple[int, str, str]]:
    """Return (chunk_id, chunk_name, chunk_content) for email chunks
    indexed in the last N hours whose subject looks like a Memory Brief
    reply (contains 'deek morning brief' case-insensitive).
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, chunk_name, chunk_content
                 FROM claw_code_chunks
                WHERE chunk_type = 'email'
                  AND indexed_at > NOW() - (INTERVAL '1 hour' * %s)
                  AND (chunk_name ILIKE '%%deek morning brief%%'
                       OR chunk_content ILIKE '%%deek morning brief%%')
                ORDER BY indexed_at DESC""",
            (since_hours,),
        )
        return [(int(r[0]), str(r[1] or ''), str(r[2] or '')) for r in cur.fetchall()]


# Email content chunks are stored by the inbox processor in a
# predictable shape:
#   "Email from <addr> (<date>)\nSubject: <subject>\n\n<body>"
# Parse accordingly.
_FROM_RE = re.compile(r'^\s*Email from\s+([^\s(]+)', re.IGNORECASE)
_SUBJECT_FROM_BODY_RE = re.compile(r'^\s*Subject:\s*(.+)$', re.IGNORECASE | re.MULTILINE)


def _extract_sender(content: str) -> str:
    """Pull the sender email from the chunk header line. Empty
    string on any failure."""
    m = _FROM_RE.search(content.splitlines()[0] if content else '')
    return m.group(1).strip().rstrip(',.;') if m else ''


def _extract_body(content: str) -> str:
    """Everything after the first blank line is treated as body."""
    parts = content.split('\n\n', 1)
    return parts[1] if len(parts) == 2 else content


def _extract_subject(chunk_name: str, content: str) -> str:
    """chunk_name is usually the subject already, but for some
    providers it's truncated — fall back to scanning the body."""
    if chunk_name:
        return chunk_name
    m = _SUBJECT_FROM_BODY_RE.search(content)
    return m.group(1).strip() if m else ''


def process_one(
    conn, chunk_id: int, chunk_name: str, chunk_content: str,
    dry_run: bool,
) -> dict:
    """Parse + apply one email chunk. Returns a result dict."""
    from core.brief.replies import (
        extract_date_from_subject, parse_reply_body,
        find_run_for_reply, already_applied, apply_reply, store_response,
    )

    subject = _extract_subject(chunk_name, chunk_content)
    sender = _extract_sender(chunk_content).lower()
    body = _extract_body(chunk_content)

    result = {
        'chunk_id': chunk_id,
        'subject': subject,
        'sender': sender,
    }

    reply_date = extract_date_from_subject(subject)
    if reply_date is None:
        result['status'] = 'skipped (subject doesn\'t match pattern)'
        return result

    # Find the run. Try the sender first; fall back to any recent run
    # on that date if the sender doesn't quite match (some clients
    # alias From).
    run = find_run_for_reply(conn, sender, reply_date)
    if run is None:
        # Try without sender constraint — look for any user who had a
        # run sent that day. At current volume, always just Toby.
        with conn.cursor() as cur:
            cur.execute(
                """SELECT user_email FROM memory_brief_runs
                    WHERE (generated_at AT TIME ZONE 'UTC')::date = %s
                      AND delivery_status = 'sent'
                    ORDER BY generated_at DESC LIMIT 1""",
                (reply_date,),
            )
            row = cur.fetchone()
        if row:
            run = find_run_for_reply(conn, row[0], reply_date)

    if run is None:
        result['status'] = f'skipped (no run found for {reply_date.isoformat()})'
        return result

    run_id, _qmap = run
    result['run_id'] = run_id

    if already_applied(conn, run_id, body):
        result['status'] = 'already-applied (idempotent skip)'
        return result

    parsed = parse_reply_body(body, sender or 'unknown', reply_date)
    if not parsed.answers:
        result['status'] = 'parsed but no answers extracted'
        result['parse_notes'] = parsed.parse_notes
        return result

    if dry_run:
        result['status'] = 'dry-run (parsed, not applied)'
        result['answers'] = [
            {'q': a.q_number, 'cat': a.category, 'verdict': a.verdict,
             'correction': a.correction_text[:120]}
            for a in parsed.answers
        ]
        return result

    applied = apply_reply(conn, parsed)
    response_id = store_response(conn, run_id, body, parsed, applied)
    conn.commit()
    result['status'] = 'applied'
    result['response_id'] = response_id
    result['answers_processed'] = applied.get('answers_processed', [])
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--since', type=int, default=48,
                    help='Look at emails indexed in the last N hours (default: 48)')
    ap.add_argument('--dry-run', action='store_true',
                    help='Parse but do not apply')
    ap.add_argument('--verbose', '-v', action='store_true')
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )

    try:
        conn = _connect()
    except Exception as exc:
        log.error('db connect failed: %s', exc)
        return 1

    try:
        candidates = _fetch_candidate_emails(conn, args.since)
        log.info('candidate emails in last %dh: %d', args.since, len(candidates))

        applied = 0
        skipped = 0
        for chunk_id, name, content in candidates:
            try:
                result = process_one(
                    conn, chunk_id, name, content, args.dry_run,
                )
                log.info('  chunk %d: %s', chunk_id, result.get('status', '?'))
                if args.verbose and result.get('answers_processed'):
                    for a in result['answers_processed']:
                        log.info('    - %s', a)
                if result.get('status') == 'applied':
                    applied += 1
                else:
                    skipped += 1
            except Exception as exc:
                log.warning('chunk %d failed: %s', chunk_id, exc)
                skipped += 1

        log.info('done: applied=%d skipped=%d dry_run=%s',
                 applied, skipped, args.dry_run)
    finally:
        conn.close()

    return 0


if __name__ == '__main__':
    sys.exit(main())
