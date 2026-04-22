#!/usr/bin/env python3
"""arXiv Stage 3 — auto-draft briefs for YES-verdict candidates.

Scans cairn_intel.arxiv_candidates for rows where toby_verdict='yes'
AND brief_drafted_at IS NULL, fetches each PDF, runs the local-Qwen
brief drafter, and writes briefs/research-<id>-<slug>.md plus marks
the row as drafted.

Runs hourly. At current volume (maybe 0-2 YES verdicts per day), a
single pass finishes in a minute or two.

Usage:
    python scripts/draft_pending_research_briefs.py
    python scripts/draft_pending_research_briefs.py --dry-run
    python scripts/draft_pending_research_briefs.py --limit 3
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _connect():
    import psycopg2
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        raise RuntimeError('DATABASE_URL not set')
    return psycopg2.connect(db_url, connect_timeout=5)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true',
                    help='Draft but do not write file or update DB')
    ap.add_argument('--limit', type=int, default=5,
                    help='Max candidates per run (default 5)')
    ap.add_argument('--verbose', '-v', action='store_true')
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )
    log = logging.getLogger('stage3')

    from core.research.autodrafter import (
        list_pending, draft_one, fetch_pdf_bytes,
        extract_pdf_text, draft_brief,
    )

    try:
        conn = _connect()
    except Exception as exc:
        log.error('db connect failed: %s', exc)
        return 1

    try:
        pending = list_pending(conn, limit=args.limit)
        log.info('pending YES drafts: %d', len(pending))
        if not pending:
            return 0

        for cand in pending:
            log.info('drafting %s  "%s"',
                     cand['arxiv_id'], cand['title'][:70])
            if args.dry_run:
                pdf_bytes = fetch_pdf_bytes(cand['pdf_url'])
                pdf_text = extract_pdf_text(pdf_bytes or b'')
                log.info('  extracted %d chars', len(pdf_text))
                brief = draft_brief(
                    arxiv_id=cand['arxiv_id'],
                    title=cand['title'],
                    abstract=cand['abstract'],
                    pdf_text=pdf_text,
                    applicability_score=cand.get('applicability_score'),
                )
                log.info(
                    '  [dry-run] brief_chars=%d',
                    len(brief or ''),
                )
                if brief:
                    log.info('  first 200 chars: %s', brief[:200])
                continue

            result = draft_one(conn, cand)
            if result.success:
                log.info(
                    '  -> %s (extracted %d chars)',
                    result.brief_path, result.chars_extracted,
                )
            else:
                log.warning('  -> FAILED: %s', result.error)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
