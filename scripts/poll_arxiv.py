#!/usr/bin/env python3
"""arXiv research-loop — Stage 1 daily poll.

Rotates through DEFAULT_QUERIES, fetches recent arxiv papers for
each, scores applicability to Deek's architecture via local Qwen,
and persists new candidates to ``cairn_intel.arxiv_candidates``.

Invoked by cron once per day. At 6 queries × 10 results = 60
arxiv calls + 60 Qwen calls per day. arxiv rate limits tolerate
this comfortably; Qwen runs locally so cost is zero.

Usage:
    python scripts/poll_arxiv.py
    python scripts/poll_arxiv.py --dry-run
    python scripts/poll_arxiv.py --queries "mechanistic interpretability"
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
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
                    help='Fetch + score but do not persist')
    ap.add_argument('--queries', type=str,
                    help='Comma-separated list; defaults to the rotation')
    ap.add_argument('--max-per-query', type=int, default=10)
    ap.add_argument('--verbose', '-v', action='store_true')
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )
    log = logging.getLogger('poll_arxiv')

    from core.research.arxiv_loop import (
        DEFAULT_QUERIES,
        fetch_recent,
        insert_candidate,
        score_applicability,
    )

    queries = (
        [q.strip() for q in args.queries.split(',') if q.strip()]
        if args.queries
        else list(DEFAULT_QUERIES)
    )

    conn = None
    if not args.dry_run:
        try:
            conn = _connect()
        except Exception as exc:
            log.error('db connect failed: %s', exc)
            return 1

    total_fetched = 0
    total_new = 0
    total_scored = 0
    try:
        for query in queries:
            log.info('query: %r', query)
            papers = fetch_recent(query, max_results=args.max_per_query)
            log.info('  %d papers fetched', len(papers))
            total_fetched += len(papers)

            for paper in papers:
                # Score applicability
                score, reason = score_applicability(paper)
                if score is None:
                    log.debug('  skip (score failed): %s', paper.arxiv_id)
                    continue
                total_scored += 1
                log.info(
                    '  %s  score=%.1f  %s',
                    paper.arxiv_id, score, paper.title[:70],
                )
                if args.verbose and reason:
                    log.info('    reason: %s', reason)

                if args.dry_run:
                    continue

                new_id = insert_candidate(
                    conn, paper, query=query,
                    score=score, reason=reason,
                )
                if new_id is not None:
                    total_new += 1

            # arxiv terms ask for a ~3s gap between requests
            time.sleep(3.0)

        log.info(
            'done: fetched=%d scored=%d new=%d (dry_run=%s)',
            total_fetched, total_scored, total_new, args.dry_run,
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
