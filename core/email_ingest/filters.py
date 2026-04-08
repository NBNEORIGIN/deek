"""
PII redaction and relevance filtering for email ingestion.

Filter ordering for toby@ mailbox:
    1. is_business_relevant() — skip clearly non-business emails (toby@ only)
    2. should_skip_email()    — skip automated/transactional senders and subjects
    3. sanitise_email_content() — redact sensitive patterns before storage

For sales@ and cairn@, only steps 2 and 3 apply.
"""
import re

# ---------------------------------------------------------------------------
# PII redaction patterns
# ---------------------------------------------------------------------------

SENSITIVE_PATTERNS = [
    # IBAN
    (r'\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}([A-Z0-9]?){0,16}\b', '[IBAN_REDACTED]'),
    # UK account number — 8 digits, only when near "account" context
    (r'(?i)(account\s*(no|number|num)?[\s:]+)\b\d{8}\b', r'\1[ACCOUNT_NO_REDACTED]'),
    # Sort code — only when preceded by sort code context to avoid matching dates
    (r'(?i)(sort\s*code[\s:]+)\d{2}[-\s]\d{2}[-\s]\d{2}', r'\1[SORT_CODE_REDACTED]'),
    # Passwords / credentials
    (r'(?i)(password|passwd|pwd)\s*[:=]\s*\S+', '[PASSWORD_REDACTED]'),
    (r'(?i)(api[_\-\s]?key|token|secret)\s*[:=]\s*[A-Za-z0-9_\-]{16,}', '[CREDENTIAL_REDACTED]'),
    # UK postcodes
    (r'\b[A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2}\b', '[POSTCODE_REDACTED]'),
    # Payment card numbers (4×4 digit groups)
    (r'\b(?:\d{4}[\s\-]?){3}\d{4}\b', '[CARD_NO_REDACTED]'),
]

# ---------------------------------------------------------------------------
# Automated sender skip list
# ---------------------------------------------------------------------------

SKIP_SENDERS = [
    'noreply@', 'no-reply@', 'donotreply@',
    'notifications@', 'alerts@', 'automated@',
    '@wise.com', '@paypal.com', '@amazon.co.uk',
    '@amazon-logistics.com', '@amazonses.com',
]

TRANSACTIONAL_SUBJECTS = [
    'your order', 'order confirmation', 'payment received',
    'invoice from', 'statement from', 'your account',
    'password reset', 'verify your', 'unsubscribe',
]


def should_skip_email(sender: str, subject: str) -> tuple[bool, str]:
    """
    Returns (skip, reason).
    Applied to all mailboxes after is_business_relevant().
    """
    sender_lower = sender.lower()
    for pattern in SKIP_SENDERS:
        if pattern in sender_lower:
            return True, f'automated_sender:{pattern}'

    subject_lower = subject.lower()
    for t in TRANSACTIONAL_SUBJECTS:
        if t in subject_lower:
            return True, f'transactional_subject:{t}'

    return False, ''


# ---------------------------------------------------------------------------
# Business relevance pre-filter (toby@ mailbox only)
# ---------------------------------------------------------------------------

BUSINESS_DOMAINS = [
    'nationalsign', 'hexham', 'arlon', 'metamark', 'fellers',
    'signwarehouse', 'rowmark', 'colourgraphic', 'inktec',
    'mimaki', 'mutoh', 'roland', 'refinecolor',
    'royalmail', 'parcelforce', 'evri', 'hermesworld', 'dpd',
    'interlink', 'yodel',
    'xero.com', 'sage.com', 'paypal.com', 'stripe.com',
    'companies', 'hmrc.gov', 'gov.uk',
    'nbnesigns', 'origindesigned', 'phloe',
]

BUSINESS_KEYWORDS = [
    'sign', 'signage', 'fascia', 'vinyl', 'print', 'banner',
    'aluminium', 'acrylic', 'substrate', 'laminate', 'plaque',
    'channel letter', 'illuminat', 'led', 'fabricat',
    'quote', 'quotation', 'invoice', 'order', 'purchase',
    'delivery', 'dispatch', 'proof', 'artwork', 'design',
    'installation', 'survey', 'site visit',
    'price', 'pricing', 'cost', 'estimate', 'budget',
    'payment', 'deposit', 'balance',
    'amazon', 'etsy', 'ebay', 'listing', 'asin', 'sku',
    'fba', 'fulfilment', 'seller central',
    'mimaki', 'mutoh', 'roland',
    'nbne', 'north by north east', 'alnwick', 'phloe',
    'manufacture', 'cairn',
]

NON_BUSINESS_PATTERNS = [
    'newsletter', 'unsubscribe', 'weekly digest', 'daily digest',
    'you have been invited', 'confirm your subscription',
    'linkedin', 'facebook', 'twitter', 'instagram',
    'noreply@', 'no-reply@', 'donotreply@',
    'promotions@', 'marketing@', 'news@',
]


def is_business_relevant(sender: str, subject: str, body_preview: str) -> tuple[bool, str]:
    """
    Returns (is_relevant, reason).
    Applied to toby@ mailbox only — errors on the side of inclusion.
    """
    sender_lower = sender.lower()
    combined = (subject + ' ' + body_preview[:500]).lower()

    for domain in BUSINESS_DOMAINS:
        if domain in sender_lower:
            return True, f'business_domain:{domain}'

    for keyword in BUSINESS_KEYWORDS:
        if keyword in combined:
            return True, f'business_keyword:{keyword}'

    for pattern in NON_BUSINESS_PATTERNS:
        if pattern in sender_lower or pattern in combined:
            return False, f'non_business_pattern:{pattern}'

    # Default include — better to over-ingest than miss business content
    return True, 'default_include'


# ---------------------------------------------------------------------------
# Content sanitisation
# ---------------------------------------------------------------------------

def sanitise_email_content(text: str) -> str:
    """Redact sensitive patterns from email text before storage."""
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text
