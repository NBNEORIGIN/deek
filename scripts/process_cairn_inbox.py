"""
Cairn cairn@ inbox processor — run by Windows Scheduled Task every 15 minutes.

Checks cairn@nbnesigns.com for new messages, ingests and embeds them.
Handles both forwarded business email and direct notes to Cairn.

Usage:
    D:\claw\.venv\Scripts\python.exe D:\claw\scripts\process_cairn_inbox.py

Registered by: scripts\install_scheduled_tasks.ps1 (CairnEmailInbox task)
"""
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

CLAW_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLAW_ROOT))

from dotenv import load_dotenv
load_dotenv(CLAW_ROOT / '.env')

log_dir = CLAW_ROOT / 'logs' / 'email_ingest'
log_dir.mkdir(parents=True, exist_ok=True)
log_file = log_dir / 'cairn_inbox.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s — %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger('cairn_inbox_processor')


def main():
    logger.info('cairn@ inbox processor starting at %s', datetime.utcnow().isoformat())

    try:
        from core.email_ingest.db import ensure_schema
        ensure_schema()
    except Exception as exc:
        logger.error('Schema setup failed: %s', exc)
        sys.exit(1)

    try:
        from core.email_ingest.processor import process_cairn_inbox
        result = process_cairn_inbox(embed_immediately=True)
        logger.info('Processor result: %s', json.dumps(result, default=str))
    except Exception as exc:
        logger.exception('Inbox processor failed: %s', exc)
        sys.exit(1)

    logger.info('cairn@ inbox processor finished at %s', datetime.utcnow().isoformat())


if __name__ == '__main__':
    main()
