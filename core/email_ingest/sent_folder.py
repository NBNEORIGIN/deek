"""Outbound Sent-folder poll — Phase B of thread associations.

Polls Toby's Sent folder via IMAP. For each new sent message:
  1. Persist to cairn_email_raw with direction='outbound'
  2. Extract thread_id (In-Reply-To → References → own message_id)
  3. If thread_id is already in email_thread_associations, touch
     the association (bump last_message_at + message_count)

Why: Toby replies to clients from his own mail client. Those
replies never go through Deek's drafting flow, so without this
poll, the CRM project has no memory of what Toby actually said.
Phase A captured INBOUND persistence; Phase B captures the
OUTBOUND side and closes the conversation loop.

Requires ``IMAP_PASSWORD_TOBY`` env var set on the Deek host. The
cairn@ poll remains independent — different mailbox, different
credentials, different cadence.

Degrades gracefully: missing env var → module logs warning and
returns without raising. Cron exits with 0 so no noisy alerts.
"""
from __future__ import annotations

import imaplib
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# Candidate folder names IONOS + common clients use. Picker tries
# each in order; first SELECT that succeeds wins.
SENT_FOLDER_CANDIDATES = (
    'Sent',
    'INBOX.Sent',
    '"Sent Items"',
    '"Sent Messages"',
    '[Gmail]/Sent Mail',
)


def _find_sent_folder(conn: imaplib.IMAP4_SSL) -> str | None:
    """Try the candidate folder names in order; return the first
    that SELECTs successfully (readonly)."""
    for name in SENT_FOLDER_CANDIDATES:
        try:
            result, _ = conn.select(name, readonly=True)
            if result == 'OK':
                return name
        except Exception:
            continue
    return None


def _connect_db():
    import psycopg2
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        raise RuntimeError('DATABASE_URL not set')
    return psycopg2.connect(db_url, connect_timeout=5)


def _already_ingested(conn, message_id: str) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT 1 FROM cairn_email_raw WHERE message_id = %s',
                (message_id,),
            )
            return cur.fetchone() is not None
    except Exception:
        return False


def _insert_outbound(
    conn, *, message_id: str, mailbox: str,
    sender: str, recipients: list[str], subject: str,
    body_text: str, body_html: str,
    received_at: datetime | None, thread_id: str | None,
    word_count: int,
) -> int | None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO cairn_email_raw
                    (message_id, mailbox, sender, recipients,
                     subject, body_text, body_html, received_at,
                     thread_id, word_count, direction, is_embedded)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           'outbound', FALSE)
                   ON CONFLICT (message_id) DO NOTHING
                   RETURNING id""",
                (message_id, mailbox, sender, recipients or [],
                 subject, body_text, body_html, received_at,
                 thread_id, int(word_count or 0)),
            )
            row = cur.fetchone()
            conn.commit()
        return int(row[0]) if row else None
    except Exception as exc:
        logger.warning('[sent-folder] insert failed: %s', exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def _touch_association(conn, thread_id: str | None) -> int | None:
    """If this outbound's thread_id is already associated with a
    project, bump last_message_at + message_count. Returns the
    association id or None."""
    if not thread_id:
        return None
    try:
        from core.triage.thread_association import lookup_project_for_thread
        assoc = lookup_project_for_thread(conn, thread_id)
        if not assoc:
            return None
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE cairn_intel.email_thread_associations
                      SET last_message_at = NOW(),
                          message_count = message_count + 1
                    WHERE id = %s""",
                (int(assoc.id),),
            )
            conn.commit()
        return int(assoc.id)
    except Exception as exc:
        logger.warning('[sent-folder] touch_association failed: %s', exc)
        return None


def poll_sent_folder(
    mailbox_name: str = 'toby',
    *,
    max_messages: int = 50,
) -> dict:
    """Poll one Sent folder and ingest new outbound messages.

    Returns a summary dict for cron logging:
      {ingested, already_seen, associations_touched, errors, folder}
    """
    from .imap_client import (
        connect_imap, decode_header_value, extract_body_text,
        fetch_all_uids, fetch_message, get_thread_id, parse_received_at,
    )

    summary = {
        'ingested': 0, 'already_seen': 0, 'associations_touched': 0,
        'errors': 0, 'folder': None, 'status': 'ok',
    }

    # 1. IMAP connect — skip gracefully if creds missing
    try:
        conn_imap = connect_imap(mailbox_name)
    except EnvironmentError as exc:
        logger.warning('[sent-folder] skipping %s: %s', mailbox_name, exc)
        summary['status'] = 'missing_credentials'
        return summary
    except Exception as exc:
        logger.warning('[sent-folder] imap connect failed: %s', exc)
        summary['status'] = 'imap_connect_failed'
        summary['errors'] += 1
        return summary

    try:
        folder = _find_sent_folder(conn_imap)
        if folder is None:
            logger.warning('[sent-folder] no Sent folder found on %s',
                           mailbox_name)
            summary['status'] = 'no_sent_folder'
            return summary
        summary['folder'] = folder

        uids = fetch_all_uids(conn_imap, folder=folder)
        # Take the most recent N — process in reverse so newest first
        recent_uids = uids[-max_messages:] if uids else []

        try:
            conn_db = _connect_db()
        except Exception as exc:
            logger.warning('[sent-folder] db connect failed: %s', exc)
            summary['status'] = 'db_connect_failed'
            summary['errors'] += 1
            return summary

        try:
            for uid in recent_uids:
                try:
                    msg = fetch_message(conn_imap, uid)
                    if msg is None:
                        continue
                    message_id = (msg.get('Message-ID') or '').strip()
                    if not message_id:
                        continue
                    if _already_ingested(conn_db, message_id):
                        summary['already_seen'] += 1
                        continue
                    subject = decode_header_value(msg.get('Subject', ''))
                    sender = decode_header_value(msg.get('From', ''))
                    to_addrs = [
                        a.strip() for a in
                        decode_header_value(msg.get('To', '')).split(',')
                        if a.strip()
                    ]
                    body_text, body_html = extract_body_text(msg)
                    thread_id = get_thread_id(msg) or message_id
                    word_count = len((body_text or '').split())
                    received_at = parse_received_at(msg)

                    new_id = _insert_outbound(
                        conn_db,
                        message_id=message_id,
                        mailbox=mailbox_name,
                        sender=sender,
                        recipients=to_addrs,
                        subject=subject,
                        body_text=body_text[:100000] if body_text else '',
                        body_html=body_html[:100000] if body_html else '',
                        received_at=received_at,
                        thread_id=thread_id,
                        word_count=word_count,
                    )
                    if new_id:
                        summary['ingested'] += 1
                        touched = _touch_association(conn_db, thread_id)
                        if touched:
                            summary['associations_touched'] += 1
                    else:
                        summary['already_seen'] += 1
                except Exception as exc:
                    summary['errors'] += 1
                    logger.warning(
                        '[sent-folder] uid=%s failed: %s', uid, exc,
                    )
        finally:
            try:
                conn_db.close()
            except Exception:
                pass
    finally:
        try:
            conn_imap.logout()
        except Exception:
            pass

    return summary


__all__ = ['poll_sent_folder']
