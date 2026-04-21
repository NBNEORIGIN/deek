#!/usr/bin/env python3
"""Triage digest reply processor — Phase B.

Scans claw_code_chunks for recent email chunks whose subject matches
the triage digest reply pattern ('Re: [Deek] ...'), parses each,
and applies the 4-question reply-back actions back to:

  * cairn_intel.email_triage (confirmed project, approved reply,
    project_folder_path, review state)
  * CRM via POST /api/cairn/memory (note on the confirmed project)
  * claw_code_chunks (new memory with toby_flag=true for free-text
    notes)

Mirror of scripts/process_memory_brief_replies.py — same shape,
different table, different apply logic. Idempotent via sha256 of
(raw_body + triage_id) stored in cairn_intel.email_triage.review_notes.

Runs on Hetzner cron at :10 and :40 past the hour (5 min after the
Memory Brief parser's :05/:35 slot, 10 min after the inbox poll).

Usage:
    python scripts/process_triage_replies.py
    python scripts/process_triage_replies.py --since 24      # hours
    python scripts/process_triage_replies.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


log = logging.getLogger('triage-replies')


def _connect():
    import psycopg2
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        raise RuntimeError('DATABASE_URL not set')
    return psycopg2.connect(db_url, connect_timeout=5)


def _fetch_candidate_emails(conn, since_hours: int) -> list[tuple[int, str, str]]:
    """Return (chunk_id, chunk_name, chunk_content) tuples for email
    chunks indexed in the last N hours whose subject matches the
    triage reply pattern.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, chunk_name, chunk_content
                 FROM claw_code_chunks
                WHERE chunk_type = 'email'
                  AND indexed_at > NOW() - (INTERVAL '1 hour' * %s)
                  AND (chunk_name ILIKE '%%[Deek]%%'
                       OR chunk_content ILIKE '%%[Deek]%%')
                ORDER BY indexed_at DESC""",
            (since_hours,),
        )
        return [(int(r[0]), str(r[1] or ''), str(r[2] or '')) for r in cur.fetchall()]


_FROM_RE = re.compile(r'^\s*Email from\s+([^\s(]+)', re.IGNORECASE)
_SUBJECT_BODY_RE = re.compile(r'^\s*Subject:\s*(.+)$', re.IGNORECASE | re.MULTILINE)


def _extract_sender(content: str) -> str:
    m = _FROM_RE.search(content.splitlines()[0] if content else '')
    return m.group(1).strip().rstrip(',.;') if m else ''


def _extract_body(content: str) -> str:
    parts = content.split('\n\n', 1)
    return parts[1] if len(parts) == 2 else content


def _extract_subject(chunk_name: str, content: str) -> str:
    if chunk_name:
        return chunk_name
    m = _SUBJECT_BODY_RE.search(content)
    return m.group(1).strip() if m else ''


def process_one(
    conn, chunk_id: int, chunk_name: str, chunk_content: str,
    dry_run: bool,
) -> dict:
    from core.triage.replies import (
        is_triage_reply, match_triage_row_by_subject,
        already_applied, parse_reply_body, apply_reply,
    )

    subject = _extract_subject(chunk_name, chunk_content)
    sender = _extract_sender(chunk_content).lower()
    body = _extract_body(chunk_content)

    result = {
        'chunk_id': chunk_id,
        'subject': subject,
        'sender': sender,
    }

    if not is_triage_reply(subject):
        result['status'] = 'skipped (subject pattern mismatch)'
        return result

    triage_id = match_triage_row_by_subject(conn, subject)
    if triage_id is None:
        result['status'] = 'skipped (no matching triage row)'
        return result
    result['triage_id'] = triage_id

    if already_applied(conn, triage_id, body):
        result['status'] = 'already-applied (idempotent skip)'
        return result

    parsed = parse_reply_body(body, sender or 'unknown', triage_id)
    if not parsed.answers:
        result['status'] = 'parsed but no answers extracted'
        result['parse_notes'] = parsed.parse_notes
        return result

    if dry_run:
        result['status'] = 'dry-run (parsed, not applied)'
        result['answers'] = [
            {
                'q': a.q_number, 'cat': a.category, 'verdict': a.verdict,
                'candidate': a.selected_candidate_index,
                'edit_preview': (a.edited_text or a.free_text or '')[:120],
            }
            for a in parsed.answers
        ]
        return result

    applied = apply_reply(conn, parsed, body)
    result['status'] = 'applied'
    result.update(applied)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--since', type=int, default=48)
    ap.add_argument('--dry-run', action='store_true')
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

    applied_count = 0
    skipped_count = 0
    try:
        candidates = _fetch_candidate_emails(conn, args.since)
        log.info('candidate emails in last %dh: %d', args.since, len(candidates))

        for chunk_id, name, content in candidates:
            try:
                result = process_one(conn, chunk_id, name, content, args.dry_run)
                status = result.get('status', '?')
                log.info('  chunk %d: %s', chunk_id, status)
                if args.verbose and result.get('answers_processed'):
                    for a in result['answers_processed']:
                        log.info('    - %s', a)
                if status == 'applied':
                    applied_count += 1
                else:
                    skipped_count += 1
            except Exception as exc:
                log.warning('chunk %d failed: %s', chunk_id, exc)
                skipped_count += 1

        log.info('done: applied=%d skipped=%d dry_run=%s',
                 applied_count, skipped_count, args.dry_run)
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
