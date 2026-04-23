#!/usr/bin/env python3
"""Drain the deek_nudges pending queue through the Telegram sender.

Runs every 5 min. Shadow mode (default) records state='shadow'
on each row instead of actually sending. Cutover cron flips
DEEK_NUDGES_SHADOW=false on 2026-05-20.
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
    ap.add_argument('--limit', type=int, default=20)
    ap.add_argument('--verbose', '-v', action='store_true')
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )
    log = logging.getLogger('nudge-sender')

    from core.channels.nudge import send_pending, is_nudges_shadow

    conn = _connect()
    try:
        log.info('shadow=%s', is_nudges_shadow())
        summary = send_pending(conn, limit=args.limit)
        log.info('result: %s', summary)
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
