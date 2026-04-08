"""
Cairn Email Bulk Ingest — one-off runner for sales@ and toby@ mailboxes.

Fetches all messages from INBOX and Sent, applies PII/relevance filters,
and stores sanitised content in cairn_email_raw.

Does NOT embed during ingest — run /email/embed (API) or the embedder
directly after inspecting the raw store.

Usage:
    D:\claw\.venv\Scripts\python.exe D:\claw\scripts\email_bulk_ingest.py
    D:\claw\.venv\Scripts\python.exe D:\claw\scripts\email_bulk_ingest.py --mailbox sales
    D:\claw\.venv\Scripts\python.exe D:\claw\scripts\email_bulk_ingest.py --mailbox toby --sleep 1.0

Options:
    --mailbox   Comma-separated list: sales,toby,cairn (default: sales,toby,cairn)
    --sleep     Seconds to sleep between IMAP message fetches (default: 0.5)

Prerequisites:
    Set in .env:
        IMAP_PASSWORD_SALES=...
        IMAP_PASSWORD_TOBY=...
        IMAP_PASSWORD_CAIRN=...
"""
import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime

CLAW_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLAW_ROOT))

from dotenv import load_dotenv
load_dotenv(CLAW_ROOT / '.env')

log_dir = CLAW_ROOT / 'logs' / 'email_ingest'
log_dir.mkdir(parents=True, exist_ok=True)
log_file = log_dir / 'email_bulk_ingest.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s — %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger('email_bulk_ingest')


def main():
    parser = argparse.ArgumentParser(description='Cairn email bulk ingest')
    parser.add_argument(
        '--mailbox',
        default='sales,toby,cairn',
        help='Comma-separated mailbox names (default: sales,toby,cairn)',
    )
    parser.add_argument(
        '--sleep',
        type=float,
        default=0.5,
        help='Seconds to sleep between IMAP fetches (default: 0.5)',
    )
    args = parser.parse_args()

    mailboxes = [m.strip() for m in args.mailbox.split(',') if m.strip()]
    logger.info('Bulk ingest starting — mailboxes=%s sleep=%.1fs', mailboxes, args.sleep)

    try:
        from core.email_ingest.db import ensure_schema
        ensure_schema()
        logger.info('Schema verified')
    except Exception as exc:
        logger.error('Schema setup failed: %s', exc)
        sys.exit(1)

    try:
        from core.email_ingest.bulk_ingest import run_bulk_ingest
        results = run_bulk_ingest(mailboxes=mailboxes, sleep_between=args.sleep)
    except Exception as exc:
        logger.exception('Bulk ingest failed: %s', exc)
        sys.exit(1)

    logger.info('Bulk ingest complete:')
    for result in results:
        logger.info('  %s', json.dumps(result, default=str))

    total_stored = sum(r.get('total_stored', 0) for r in results)
    total_skipped = sum(r.get('total_skipped', 0) for r in results)
    total_errors = sum(r.get('total_errors', 0) for r in results)

    logger.info(
        'Summary: stored=%d skipped=%d errors=%d',
        total_stored, total_skipped, total_errors,
    )
    logger.info('')
    logger.info('Next step: start embedding with:')
    logger.info('  curl -X POST http://localhost:8765/email/embed')
    logger.info('  curl http://localhost:8765/email/embed/status')


if __name__ == '__main__':
    main()
