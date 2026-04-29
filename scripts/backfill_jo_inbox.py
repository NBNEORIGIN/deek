"""One-off backfill of Jo's entire mailbox into Rex.

Connects via IMAP, enumerates every folder, filters out the obvious
junk (Drafts, Spam, Trash, Junk), then runs the bulk ingester
folder-by-folder. After ingest, runs embed_email_batch in chunks
until everything has an embedding.

Resume-safe: bulk_ingest checks message_ids against the existing
cairn_email_raw rows before fetching, so re-running picks up where
it left off.

Run on jo-pip-api:
    docker exec -w /app -e PYTHONPATH=/app jo-pip-api \\
        python scripts/backfill_jo_inbox.py 2>&1 | tee /tmp/jo-backfill.log

Long-running — for Jo's ~540 folders this is hours, not minutes.
"""
from __future__ import annotations

import logging
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / '.env')
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s — %(message)s',
)
logger = logging.getLogger('jo_backfill')

# Folders we never want to ingest — drafts/spam/trash/junk + sync noise.
SKIP_FOLDER_RES = [
    re.compile(r'^drafts?$', re.IGNORECASE),
    re.compile(r'^junk$', re.IGNORECASE),
    re.compile(r'^junk e?-?mail$', re.IGNORECASE),
    re.compile(r'^spam$', re.IGNORECASE),
    re.compile(r'^trash$', re.IGNORECASE),
    re.compile(r'^deleted items?$', re.IGNORECASE),
    re.compile(r'^outbox$', re.IGNORECASE),
    re.compile(r'^sync(ed)? items?$', re.IGNORECASE),
    re.compile(r'^conversation history$', re.IGNORECASE),
    re.compile(r'^notes$', re.IGNORECASE),
    re.compile(r'^calendar$', re.IGNORECASE),
    re.compile(r'^contacts?$', re.IGNORECASE),
    re.compile(r'^tasks?$', re.IGNORECASE),
]


def _should_skip(folder_name: str) -> bool:
    bare = folder_name.split('/')[-1].strip()
    return any(rx.search(bare) for rx in SKIP_FOLDER_RES)


def _list_folders(mailbox: str) -> list[str]:
    """Return the IMAP folder names we should ingest, in priority order
    (INBOX first, Sent next, everything else after, alphabetised)."""
    from core.email_ingest.imap_client import connect_imap

    conn = connect_imap(mailbox)
    try:
        typ, raw = conn.list()
        if typ != 'OK':
            return ['INBOX']
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    names: list[str] = []
    # IMAP LIST returns lines like:
    #   (\HasNoChildren) "/" "Archive/2024"
    list_re = re.compile(r'\((?P<flags>[^)]*)\)\s+"(?P<sep>[^"]+)"\s+"?(?P<name>[^"]+)"?\s*$')
    for line in raw or []:
        s = line.decode('utf-8', errors='replace') if isinstance(line, (bytes, bytearray)) else str(line)
        m = list_re.search(s)
        if not m:
            continue
        name = m.group('name')
        flags = m.group('flags')
        # Skip non-selectable folders (containers only)
        if '\\Noselect' in flags:
            continue
        if _should_skip(name):
            continue
        names.append(name)

    # Order: INBOX, Sent variants, then alphabetical
    front: list[str] = []
    for prefer in ('INBOX', 'Sent', 'Sent Messages', 'Sent Items'):
        if prefer in names:
            front.append(prefer)
            names.remove(prefer)
    names.sort(key=str.lower)
    return front + names


def _embed_until_done(batch: int = 50, max_loops: int = 200) -> dict:
    """Loop embed_email_batch until no more pending messages or the
    safety cap trips."""
    from core.email_ingest.embedder import embed_email_batch
    from core.email_ingest.db import get_conn

    total_embedded = 0
    total_chunks = 0
    loop = 0
    while loop < max_loops:
        loop += 1
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM cairn_email_raw "
                    "WHERE mailbox=%s AND is_embedded=FALSE AND skip_reason IS NULL",
                    ('jo',),
                )
                pending = int(cur.fetchone()[0] or 0)
        if pending == 0:
            logger.info('[jo@] no more emails to embed (loop=%d)', loop)
            break
        logger.info('[jo@] embed loop %d — %d pending', loop, pending)
        result = embed_email_batch(batch_size=batch)
        total_embedded += int(result.get('embedded', 0) or 0)
        total_chunks += int(result.get('chunks_written', 0) or 0)
        if int(result.get('embedded', 0) or 0) == 0 and int(result.get('errors', 0) or 0) > 0:
            logger.error('[jo@] embedder making no progress; halting after %d loops', loop)
            break
    return {'loops': loop, 'embedded': total_embedded, 'chunks': total_chunks}


def main() -> int:
    logger.info('[jo@] backfill starting')

    try:
        from core.email_ingest.db import ensure_schema
        ensure_schema()
    except Exception as exc:
        logger.error('schema setup failed: %s', exc)
        return 1

    folders = _list_folders('jo')
    logger.info('[jo@] %d folders selected for backfill', len(folders))
    for f in folders[:10]:
        logger.info('  - %s', f)
    if len(folders) > 10:
        logger.info('  …and %d more', len(folders) - 10)

    from core.email_ingest.bulk_ingest import ingest_mailbox

    start = time.time()
    result = ingest_mailbox(
        mailbox_name='jo',
        sleep_between=0.2,  # tighten the per-fetch pause for backfill
        folders=folders,
    )
    elapsed = time.time() - start
    logger.info('[jo@] ingest result: %s (%.0fs wall)', result, elapsed)

    logger.info('[jo@] running embedder…')
    embed_result = _embed_until_done(batch=50)
    logger.info('[jo@] embed result: %s', embed_result)

    logger.info('[jo@] backfill complete')
    return 0


if __name__ == '__main__':
    sys.exit(main())
