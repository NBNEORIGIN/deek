"""
IMAP connection and message parsing utilities.

Handles:
    - SSL connection to IONOS (or any IMAP host)
    - Header decoding (RFC 2047 encoded words)
    - Plain text body extraction with HTML fallback
    - Thread ID extraction (In-Reply-To / References)
    - Partial body fetch for relevance pre-filtering (BODY[TEXT]<0.500>)
"""
import os
import imaplib
import email
import logging
from email.header import decode_header as _decode_header
from email.utils import parseaddr, parsedate_to_datetime
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

IONOS_IMAP_HOST = os.getenv('IMAP_HOST', 'imap.ionos.co.uk')
IMAP_PORT = 993

MAILBOX_CONFIG: dict[str, dict] = {
    'sales': {
        'host': IONOS_IMAP_HOST,
        'user': 'sales@nbnesigns.co.uk',
        'password_env': 'IMAP_PASSWORD_SALES',
    },
    'toby': {
        'host': IONOS_IMAP_HOST,
        'user': 'toby@nbnesigns.com',
        'password_env': 'IMAP_PASSWORD_TOBY',
    },
    'cairn': {
        'host': IONOS_IMAP_HOST,
        'user': 'cairn@nbnesigns.com',
        'password_env': 'IMAP_PASSWORD_CAIRN',
    },
}


def connect_imap(mailbox_name: str) -> imaplib.IMAP4_SSL:
    """Open an authenticated IMAP SSL connection for the named mailbox."""
    cfg = MAILBOX_CONFIG[mailbox_name]
    password = os.environ.get(cfg['password_env'], '')
    if not password:
        raise EnvironmentError(
            f"{cfg['password_env']} is not set — cannot connect to {mailbox_name}"
        )
    conn = imaplib.IMAP4_SSL(cfg['host'], IMAP_PORT)
    conn.login(cfg['user'], password)
    return conn


def decode_header_value(value: str | None) -> str:
    """Decode RFC 2047 encoded-word headers to a plain UTF-8 string."""
    if not value:
        return ''
    parts = []
    for fragment, charset in _decode_header(value):
        if isinstance(fragment, bytes):
            try:
                parts.append(fragment.decode(charset or 'utf-8', errors='replace'))
            except (LookupError, UnicodeDecodeError):
                parts.append(fragment.decode('utf-8', errors='replace'))
        else:
            parts.append(fragment)
    return ''.join(parts)


def extract_body_text(msg: email.message.Message) -> tuple[str, str]:
    """
    Returns (plain_text, html_text) extracted from a message.
    Walks MIME parts; prefers text/plain, falls back to stripped text/html.
    """
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get('Content-Disposition', ''))
            if 'attachment' in disposition:
                continue
            if content_type == 'text/plain':
                plain_parts.append(_decode_part(part))
            elif content_type == 'text/html':
                html_parts.append(_decode_part(part))
    else:
        content_type = msg.get_content_type()
        if content_type == 'text/plain':
            plain_parts.append(_decode_part(msg))
        elif content_type == 'text/html':
            html_parts.append(_decode_part(msg))

    plain = '\n'.join(plain_parts).strip()
    html = '\n'.join(html_parts).strip()

    # If no plain text, strip HTML tags for a rough text fallback
    if not plain and html:
        import re
        plain = re.sub(r'<[^>]+>', ' ', html)
        plain = re.sub(r'\s+', ' ', plain).strip()

    return plain, html


def _decode_part(part: email.message.Message) -> str:
    """Decode a single MIME part payload to a string."""
    payload = part.get_payload(decode=True)
    if not payload:
        return ''
    charset = part.get_content_charset() or 'utf-8'
    try:
        return payload.decode(charset, errors='replace')
    except (LookupError, UnicodeDecodeError):
        return payload.decode('utf-8', errors='replace')


def parse_received_at(msg: email.message.Message) -> datetime | None:
    """Parse the Date header into a timezone-aware datetime."""
    date_str = msg.get('Date', '')
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def get_thread_id(msg: email.message.Message) -> str | None:
    """Extract thread ID from In-Reply-To header, falling back to References."""
    in_reply_to = msg.get('In-Reply-To', '').strip()
    if in_reply_to:
        return in_reply_to[:500]
    refs = msg.get('References', '').strip()
    if refs:
        # First message-id in the References chain is the thread root
        parts = refs.split()
        if parts:
            return parts[0][:500]
    return None


def fetch_all_uids(conn: imaplib.IMAP4_SSL, folder: str = 'INBOX') -> list[bytes]:
    """Select a folder and return all message UIDs."""
    conn.select(folder, readonly=True)
    _, data = conn.search(None, 'ALL')
    if not data or not data[0]:
        return []
    return data[0].split()


def fetch_message(conn: imaplib.IMAP4_SSL, uid: bytes) -> email.message.Message | None:
    """Fetch a full RFC822 message by UID. Returns None on fetch failure."""
    try:
        _, msg_data = conn.fetch(uid, '(RFC822)')
        if not msg_data or not msg_data[0]:
            return None
        raw = msg_data[0][1]
        if not isinstance(raw, bytes):
            return None
        return email.message_from_bytes(raw)
    except Exception as exc:
        logger.warning('fetch_message uid=%s failed: %s', uid, exc)
        return None


def fetch_body_preview(conn: imaplib.IMAP4_SSL, uid: bytes, max_bytes: int = 500) -> str:
    """
    Fetch only the first max_bytes of the message body for relevance pre-filtering.
    Uses RFC 3501 partial fetch. Falls back to empty string on failure.
    """
    try:
        _, msg_data = conn.fetch(uid, f'(BODY[TEXT]<0.{max_bytes}>)')
        if not msg_data or not msg_data[0]:
            return ''
        raw = msg_data[0][1]
        if isinstance(raw, bytes):
            return raw.decode('utf-8', errors='replace')
        return str(raw)
    except Exception:
        return ''


def parse_message(
    msg: email.message.Message,
    mailbox_name: str,
) -> dict:
    """
    Parse a raw email.message.Message into a dict ready for DB insertion.
    Does NOT apply PII filters — call sanitise_email_content() separately.
    """
    message_id = (msg.get('Message-ID', '') or '').strip()[:500]
    subject = decode_header_value(msg.get('Subject', ''))
    raw_from = msg.get('From', '')
    _, sender = parseaddr(raw_from)
    sender = sender or raw_from

    raw_to = msg.get('To', '') or ''
    raw_cc = msg.get('Cc', '') or ''
    recipients = [
        addr
        for _, addr in (
            [parseaddr(r.strip()) for r in raw_to.split(',')]
            + [parseaddr(r.strip()) for r in raw_cc.split(',')]
        )
        if addr
    ]

    body_text, body_html = extract_body_text(msg)
    received_at = parse_received_at(msg)
    thread_id = get_thread_id(msg)
    word_count = len(body_text.split()) if body_text else 0

    return {
        'message_id': message_id,
        'mailbox': mailbox_name,
        'sender': sender[:500] if sender else None,
        'recipients': recipients,
        'subject': subject[:1000] if subject else None,
        'body_text': body_text,
        'body_html': body_html,
        'received_at': received_at,
        'thread_id': thread_id,
        'word_count': word_count,
    }
