"""
One-off bulk ingest from sales@ and toby@ mailboxes.

Design decisions:
    - Fetch all UIDs first, then process sequentially (avoids IMAP timeout)
    - Resume-safe: existing message_ids are loaded at start; already-stored
      messages are skipped without re-fetching the full RFC822 body
    - Rate-limited: configurable sleep between fetches (default 0.5s) to
      avoid IONOS connection drops on rapid sequential access
    - Embedding is NOT performed here — run embed_email_batch() separately
      after inspecting the raw store
    - Checkpoint written to cairn_email_ingest_log every CHECKPOINT_EVERY messages
"""
import logging
import time
from datetime import datetime, timezone

from core.email_ingest.db import get_conn
from core.email_ingest.filters import (
    is_business_relevant,
    should_skip_email,
    sanitise_email_content,
)
from core.email_ingest.imap_client import (
    MAILBOX_CONFIG,
    connect_imap,
    fetch_all_uids,
    fetch_message,
    fetch_body_preview,
    parse_message,
)

logger = logging.getLogger(__name__)

CHECKPOINT_EVERY = 500
DEFAULT_SLEEP_BETWEEN_FETCHES = 0.5  # seconds


def _load_existing_message_ids(mailbox_name: str) -> set[str]:
    """Load message_ids already stored for this mailbox (for resume)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT message_id FROM cairn_email_raw WHERE mailbox = %s',
                (mailbox_name,),
            )
            return {row[0] for row in cur.fetchall()}


def _upsert_email(conn, parsed: dict, labels: list[str]) -> bool:
    """
    Insert email into cairn_email_raw. Skips if message_id already exists.
    Returns True if inserted, False if skipped (duplicate).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cairn_email_raw
                (message_id, mailbox, sender, recipients, subject,
                 body_text, body_html, received_at, thread_id, labels,
                 is_embedded, skip_reason, word_count)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, FALSE, NULL, %s)
            ON CONFLICT (message_id) DO NOTHING
            """,
            (
                parsed['message_id'],
                parsed['mailbox'],
                parsed['sender'],
                parsed['recipients'],
                parsed['subject'],
                parsed['body_text'],
                parsed['body_html'],
                parsed['received_at'],
                parsed['thread_id'],
                labels,
                parsed['word_count'],
            ),
        )
        return cur.rowcount == 1


def _store_skipped(conn, parsed: dict, skip_reason: str) -> None:
    """Record a skipped email (PII/relevance filtered) with reason."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cairn_email_raw
                (message_id, mailbox, sender, recipients, subject,
                 body_text, received_at, thread_id, labels,
                 is_embedded, skip_reason, word_count)
            VALUES (%s,%s,%s,%s,%s, NULL,%s,%s, '{}', FALSE, %s, 0)
            ON CONFLICT (message_id) DO NOTHING
            """,
            (
                parsed['message_id'],
                parsed['mailbox'],
                parsed['sender'],
                parsed['recipients'],
                parsed['subject'],
                parsed['received_at'],
                parsed['thread_id'],
                skip_reason,
            ),
        )


def _write_checkpoint(
    conn,
    log_id: int,
    last_message_id: str,
    total_fetched: int,
    total_stored: int,
    total_skipped: int,
    total_errors: int,
    status: str = 'running',
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE cairn_email_ingest_log
            SET last_message_id=%s, total_fetched=%s, total_stored=%s,
                total_skipped=%s, total_errors=%s, status=%s,
                run_ended = CASE WHEN %s != 'running' THEN NOW() ELSE NULL END
            WHERE id=%s
            """,
            (
                last_message_id,
                total_fetched,
                total_stored,
                total_skipped,
                total_errors,
                status,
                status,
                log_id,
            ),
        )
        conn.commit()


def _create_log_entry(conn, mailbox_name: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cairn_email_ingest_log
                (mailbox, run_started, status,
                 total_fetched, total_stored, total_skipped, total_errors)
            VALUES (%s, NOW(), 'running', 0, 0, 0, 0)
            RETURNING id
            """,
            (mailbox_name,),
        )
        log_id = cur.fetchone()[0]
        conn.commit()
        return log_id


def ingest_mailbox(
    mailbox_name: str,
    sleep_between: float = DEFAULT_SLEEP_BETWEEN_FETCHES,
    folders: list[str] | None = None,
) -> dict:
    """
    Bulk-ingest a single mailbox (INBOX + Sent by default).
    Returns a summary dict.
    """
    if folders is None:
        folders = ['INBOX', 'Sent']

    logger.info('[%s] Starting bulk ingest', mailbox_name)

    existing_ids = _load_existing_message_ids(mailbox_name)
    logger.info('[%s] %d messages already stored (resume set)', mailbox_name, len(existing_ids))

    total_fetched = 0
    total_stored = 0
    total_skipped = 0
    total_errors = 0
    last_message_id = ''

    with get_conn() as conn:
        log_id = _create_log_entry(conn, mailbox_name)

        try:
            imap = connect_imap(mailbox_name)
        except EnvironmentError as exc:
            logger.error('[%s] Cannot connect: %s', mailbox_name, exc)
            _write_checkpoint(conn, log_id, '', 0, 0, 0, 1, 'error')
            return {'mailbox': mailbox_name, 'status': 'error', 'reason': str(exc)}

        try:
            all_uids: list[bytes] = []
            for folder in folders:
                try:
                    uids = fetch_all_uids(imap, folder)
                    all_uids.extend(uids)
                    logger.info('[%s] %s: %d UIDs', mailbox_name, folder, len(uids))
                except Exception as exc:
                    logger.warning('[%s] Could not select folder %s: %s', mailbox_name, folder, exc)

            logger.info('[%s] Total UIDs to process: %d', mailbox_name, len(all_uids))

            for i, uid in enumerate(all_uids, start=1):
                try:
                    # Fetch headers only first (faster) for resume check
                    _, hdr_data = imap.fetch(uid, '(BODY[HEADER.FIELDS (MESSAGE-ID)])')
                    raw_hdr = hdr_data[0][1] if hdr_data and hdr_data[0] else b''
                    import email as _email
                    hdr_msg = _email.message_from_bytes(raw_hdr)
                    msg_id_hdr = (hdr_msg.get('Message-ID', '') or '').strip()[:500]

                    if msg_id_hdr and msg_id_hdr in existing_ids:
                        total_fetched += 1
                        if i % 500 == 0:
                            logger.info('[%s] %d/%d (%.0f%%) — skipping known',
                                        mailbox_name, i, len(all_uids), 100*i/len(all_uids))
                        continue

                    # Relevance pre-filter for toby@ (uses partial body fetch)
                    if mailbox_name == 'toby' and msg_id_hdr:
                        preview = fetch_body_preview(imap, uid, 500)
                        # Need subject for relevance check; fetch full headers
                        _, full_hdr_data = imap.fetch(uid, '(BODY[HEADER.FIELDS (MESSAGE-ID SUBJECT FROM)])')
                        raw_full_hdr = full_hdr_data[0][1] if full_hdr_data and full_hdr_data[0] else b''
                        full_hdr_msg = _email.message_from_bytes(raw_full_hdr)
                        from core.email_ingest.imap_client import decode_header_value
                        from email.utils import parseaddr
                        subj = decode_header_value(full_hdr_msg.get('Subject', ''))
                        _, sender_addr = parseaddr(full_hdr_msg.get('From', ''))
                        relevant, reason = is_business_relevant(sender_addr, subj, preview)
                        if not relevant:
                            # Store minimal record with skip reason, no body
                            with conn.cursor() as cur:
                                cur.execute(
                                    """
                                    INSERT INTO cairn_email_raw
                                        (message_id, mailbox, sender, subject,
                                         recipients, is_embedded, skip_reason, word_count)
                                    VALUES (%s,%s,%s,%s,'{}', FALSE, %s, 0)
                                    ON CONFLICT (message_id) DO NOTHING
                                    """,
                                    (msg_id_hdr, mailbox_name, sender_addr, subj,
                                     f'not_business_relevant:{reason}'),
                                )
                                conn.commit()
                            existing_ids.add(msg_id_hdr)
                            total_fetched += 1
                            total_skipped += 1
                            if sleep_between:
                                time.sleep(sleep_between)
                            continue

                    # Fetch full message
                    msg = fetch_message(imap, uid)
                    if msg is None:
                        total_errors += 1
                        continue

                    parsed = parse_message(msg, mailbox_name)

                    if not parsed['message_id']:
                        # Generate synthetic ID if missing
                        import hashlib
                        synthetic = f'<synthetic-{mailbox_name}-{uid.decode()}-{hashlib.md5((parsed.get("subject") or "").encode()).hexdigest()[:8]}@cairn>'
                        parsed['message_id'] = synthetic

                    total_fetched += 1
                    last_message_id = parsed['message_id']

                    # should_skip_email check
                    skip, skip_reason = should_skip_email(
                        parsed['sender'] or '', parsed['subject'] or ''
                    )
                    if skip:
                        _store_skipped(conn, parsed, skip_reason)
                        conn.commit()
                        existing_ids.add(parsed['message_id'])
                        total_skipped += 1
                    else:
                        # Sanitise before storage
                        if parsed['body_text']:
                            parsed['body_text'] = sanitise_email_content(parsed['body_text'])
                        if parsed['subject']:
                            parsed['subject'] = sanitise_email_content(parsed['subject'])

                        inserted = _upsert_email(conn, parsed, [])
                        conn.commit()
                        if inserted:
                            existing_ids.add(parsed['message_id'])
                            total_stored += 1

                    if i % CHECKPOINT_EVERY == 0:
                        _write_checkpoint(
                            conn, log_id, last_message_id,
                            total_fetched, total_stored, total_skipped, total_errors,
                        )
                        logger.info(
                            '[%s] Checkpoint %d/%d — stored=%d skipped=%d errors=%d',
                            mailbox_name, i, len(all_uids),
                            total_stored, total_skipped, total_errors,
                        )

                    if sleep_between:
                        time.sleep(sleep_between)

                except Exception as exc:
                    logger.error('[%s] Error on uid=%s: %s', mailbox_name, uid, exc, exc_info=True)
                    total_errors += 1
                    try:
                        conn.rollback()
                    except Exception:
                        pass

        finally:
            try:
                imap.logout()
            except Exception:
                pass

        _write_checkpoint(
            conn, log_id, last_message_id,
            total_fetched, total_stored, total_skipped, total_errors,
            status='complete',
        )

    summary = {
        'mailbox': mailbox_name,
        'status': 'complete',
        'total_fetched': total_fetched,
        'total_stored': total_stored,
        'total_skipped': total_skipped,
        'total_errors': total_errors,
    }
    logger.info('[%s] Ingest complete: %s', mailbox_name, summary)
    return summary


def run_bulk_ingest(
    mailboxes: list[str] | None = None,
    sleep_between: float = DEFAULT_SLEEP_BETWEEN_FETCHES,
) -> list[dict]:
    """
    Run bulk ingest across all configured mailboxes (or a subset).
    Returns a list of per-mailbox summary dicts.
    """
    if mailboxes is None:
        mailboxes = ['sales', 'toby', 'cairn']
    results = []
    for mailbox_name in mailboxes:
        result = ingest_mailbox(mailbox_name, sleep_between=sleep_between)
        results.append(result)
    return results
