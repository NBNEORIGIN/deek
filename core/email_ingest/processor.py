"""
Ongoing cairn@ inbox processor.

Polls cairn@ every 15 minutes (via Windows Scheduled Task).
Classifies incoming mail into two types:

    Type 1 — Forwarded business email
        Sender is sales@ or toby@, or subject contains 'Fwd:'.
        Standard ingest + embed. Label: forwarded_business.

    Type 2 — Direct notes to Cairn
        Sent directly to cairn@, not forwarded.
        Ingest + embed immediately (higher priority).
        Labels: direct_note, wiki_candidate.
        These are Toby's voice notes into the knowledge base.
"""
import logging
from datetime import datetime, timezone

from core.email_ingest.db import get_conn
from core.email_ingest.filters import should_skip_email, sanitise_email_content
from core.email_ingest.imap_client import (
    connect_imap,
    fetch_all_uids,
    fetch_message,
    parse_message,
)
from core.email_ingest.embedder import embed_email_batch

logger = logging.getLogger(__name__)

CAIRN_INBOX = 'cairn'

# Senders whose forwarded mail should be treated as business email
FORWARDING_SOURCES = {
    'sales@nbnesigns.co.uk',
    'toby@nbnesigns.com',
}


def _classify_labels(parsed: dict) -> list[str]:
    """Return labels list based on sender and subject."""
    sender = (parsed['sender'] or '').lower()
    subject = (parsed['subject'] or '').lower()

    is_forwarded = (
        sender in {s.lower() for s in FORWARDING_SOURCES}
        or subject.startswith('fwd:')
        or subject.startswith('fw:')
    )
    if is_forwarded:
        return ['forwarded_business']

    # Direct note to Cairn
    return ['direct_note', 'wiki_candidate']


def _load_known_ids() -> set[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_id FROM cairn_email_raw WHERE mailbox=%s",
                (CAIRN_INBOX,),
            )
            return {row[0] for row in cur.fetchall()}


def _store_email(parsed: dict, labels: list[str]) -> bool:
    """Upsert email into cairn_email_raw. Returns True if newly inserted."""
    with get_conn() as conn:
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
            inserted = cur.rowcount == 1
            conn.commit()
    return inserted


def process_cairn_inbox(embed_immediately: bool = True) -> dict:
    """
    Check cairn@ for new messages, ingest and embed them.
    Called by the Windows Scheduled Task every 15 minutes.

    Returns summary: {new_messages, forwarded, direct_notes, wiki_candidates, errors}
    """
    logger.info('[cairn@] Processing inbox')
    known_ids = _load_known_ids()

    new_messages = 0
    forwarded = 0
    direct_notes = 0
    wiki_candidates = 0
    errors = 0

    try:
        imap = connect_imap(CAIRN_INBOX)
    except EnvironmentError as exc:
        logger.error('[cairn@] Cannot connect: %s', exc)
        return {'status': 'error', 'reason': str(exc)}

    try:
        uids = fetch_all_uids(imap, 'INBOX')
        logger.info('[cairn@] %d messages in inbox', len(uids))

        for uid in uids:
            try:
                msg = fetch_message(imap, uid)
                if msg is None:
                    errors += 1
                    continue

                parsed = parse_message(msg, CAIRN_INBOX)

                if not parsed['message_id']:
                    import hashlib
                    parsed['message_id'] = (
                        f'<synthetic-cairn-{uid.decode()}-'
                        f'{hashlib.md5((parsed.get("subject") or "").encode()).hexdigest()[:8]}@cairn>'
                    )

                if parsed['message_id'] in known_ids:
                    continue

                skip, skip_reason = should_skip_email(
                    parsed['sender'] or '', parsed['subject'] or ''
                )
                if skip:
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                INSERT INTO cairn_email_raw
                                    (message_id, mailbox, sender, subject, recipients,
                                     is_embedded, skip_reason, word_count)
                                VALUES (%s,%s,%s,%s,'{}', FALSE, %s, 0)
                                ON CONFLICT (message_id) DO NOTHING
                                """,
                                (
                                    parsed['message_id'], CAIRN_INBOX,
                                    parsed['sender'], parsed['subject'], skip_reason,
                                ),
                            )
                            conn.commit()
                    known_ids.add(parsed['message_id'])
                    continue

                # Sanitise before storage
                if parsed['body_text']:
                    parsed['body_text'] = sanitise_email_content(parsed['body_text'])
                if parsed['subject']:
                    parsed['subject'] = sanitise_email_content(parsed['subject'])

                labels = _classify_labels(parsed)
                inserted = _store_email(parsed, labels)

                if inserted:
                    known_ids.add(parsed['message_id'])
                    new_messages += 1

                    if 'forwarded_business' in labels:
                        forwarded += 1
                    if 'direct_note' in labels:
                        direct_notes += 1
                    if 'wiki_candidate' in labels:
                        wiki_candidates += 1

                    logger.info(
                        '[cairn@] New: %s | labels=%s',
                        parsed['subject'], labels,
                    )

            except Exception as exc:
                logger.error('[cairn@] Error processing uid=%s: %s', uid, exc, exc_info=True)
                errors += 1

    finally:
        try:
            imap.logout()
        except Exception:
            pass

    # Embed newly ingested messages
    if embed_immediately and new_messages > 0:
        logger.info('[cairn@] Embedding %d new messages', new_messages)
        try:
            embed_result = embed_email_batch(batch_size=new_messages + 10)
            logger.info('[cairn@] Embed result: %s', embed_result)
        except Exception as exc:
            logger.error('[cairn@] Embedding failed: %s', exc)

    result = {
        'status': 'complete',
        'new_messages': new_messages,
        'forwarded': forwarded,
        'direct_notes': direct_notes,
        'wiki_candidates': wiki_candidates,
        'errors': errors,
    }
    logger.info('[cairn@] Done: %s', result)
    return result
