#!/usr/bin/env python3
"""Sent-folder poll — outbound half of the thread-association loop.

Runs every 15 min alongside the inbound poll. Ingests new messages
from toby@'s Sent folder into cairn_email_raw (direction='outbound')
and bumps any existing thread→project associations when an
outbound message continues an associated conversation.

Usage:
    python scripts/process_sent_folder.py
    python scripts/process_sent_folder.py --mailbox toby --limit 50
    python scripts/process_sent_folder.py --verbose
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mailbox', default='toby',
                    help='Which mailbox to poll Sent for (default: toby)')
    ap.add_argument('--limit', type=int, default=50,
                    help='Max UIDs to process per run (default 50)')
    ap.add_argument('--verbose', '-v', action='store_true')
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )
    log = logging.getLogger('sent-folder')

    from core.email_ingest.sent_folder import poll_sent_folder

    summary = poll_sent_folder(
        mailbox_name=args.mailbox, max_messages=args.limit,
    )
    log.info('result: %s', summary)
    # Exit 0 regardless — missing creds shouldn't fail loud (noisy
    # cron alerts). Logs carry the detail.
    return 0


if __name__ == '__main__':
    sys.exit(main())
