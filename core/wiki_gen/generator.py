"""
Core wiki generation helpers.

    get_embedding()      — singleton CodeIndexer embed (same pattern as email embedder)
    call_claude()        — Anthropic API call, returns (text, tokens)
    subject_to_title()   — strip email prefixes, normalise
    title_to_filename()  — safe slug filename
    classify_module()    — tag article to Cairn module
    quality_check()      — two-tier gate: local heuristics + optional Claude
    write_wiki_article() — write to disk + chunk + embed into claw_code_chunks
"""
import hashlib
import json
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
# override=True ensures .env values take precedence over any stale
# Windows environment variables set before the process started.
load_dotenv(Path(__file__).resolve().parents[2] / '.env', override=True)

import anthropic

from core.wiki_gen.db import get_conn, get_db_url

logger = logging.getLogger(__name__)

# Derive paths from __file__ — never hardcode
_CLAW_ROOT = Path(__file__).resolve().parents[2]
_WIKI_DIR = _CLAW_ROOT / 'wiki' / 'modules'

MAX_CHUNK_CHARS = 1500
CLAUDE_MODEL = 'claude-sonnet-4-5'

# ---------------------------------------------------------------------------
# Embedding — singleton CodeIndexer (avoids reconnecting per call)
# ---------------------------------------------------------------------------

_indexer = None


def _get_indexer():
    global _indexer
    if _indexer is None:
        from core.context.indexer import CodeIndexer
        _indexer = CodeIndexer(
            project_id='claw',
            codebase_path=str(_CLAW_ROOT),
            db_url=get_db_url(),
        )
    return _indexer


def get_embedding(text: str) -> list[float]:
    """Embed text using CodeIndexer — same provider chain as the rest of Cairn."""
    return _get_indexer().embed(text)


# ---------------------------------------------------------------------------
# Claude API
# ---------------------------------------------------------------------------

def call_claude(prompt: str, max_tokens: int = 2048) -> tuple[str, int]:
    """
    Call Claude and return (response_text, total_tokens_used).
    Uses ANTHROPIC_API_KEY from environment.
    """
    client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        messages=[{'role': 'user', 'content': prompt}],
    )
    tokens = msg.usage.input_tokens + msg.usage.output_tokens
    return msg.content[0].text, tokens


# ---------------------------------------------------------------------------
# Title / filename helpers
# ---------------------------------------------------------------------------

def subject_to_title(subject: str) -> str:
    """Strip common email prefixes and normalise to a clean article title."""
    cleaned = re.sub(
        r'^(Re:|Fwd:|FW:|How\s+to:|How-to:)\s*',
        '',
        subject,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned or subject


def title_to_filename(title: str) -> str:
    """Convert an article title to a safe wiki filename (slug.md)."""
    slug = re.sub(r'[^a-z0-9\-]', '-', title.lower())
    slug = re.sub(r'-+', '-', slug).strip('-')
    return f'{slug}.md'


# ---------------------------------------------------------------------------
# Module classifier
# ---------------------------------------------------------------------------

_MODULE_KEYWORDS: dict[str, list[str]] = {
    'amazon': ['amazon', 'etsy', 'ebay', 'listing', 'asin',
               'sku', 'marketplace', 'seller central', 'fba'],
    'phloe': ['phloe', 'booking', 'tenant', 'demnurse', 'karate',
              'appointment', 'class timetable'],
    'manufacture': ['manufacture', 'production', 'make', 'print', 'cut',
                    'machine', 'printer', 'mimaki', 'mutoh', 'roland',
                    'rolf', 'laminator', 'plotter'],
    'crm': ['quote', 'invoice', 'client', 'crm', 'project',
            'survey', 'installation', 'customer', 'enquiry'],
    'ledger': ['price', 'pricing', 'cost', 'margin', 'ledger',
               'revenue', 'profit', 'hourly rate', 'labour cost'],
}


def classify_module(title: str, content: str) -> str:
    """Tag article to the appropriate Cairn module. Checks title first, then content."""
    for module, keywords in _MODULE_KEYWORDS.items():
        if any(w in title.lower() for w in keywords):
            return module
    for module, keywords in _MODULE_KEYWORDS.items():
        if any(w in content.lower() for w in keywords):
            return module
    return 'general'


# ---------------------------------------------------------------------------
# Quality gate (two-tier)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Strong NBNE operational signals — at least one required for pass
# ---------------------------------------------------------------------------
_STRONG_NBNE_SIGNALS = [
    'nbne', 'alnwick', 'northumberland', 'fascia', 'signmaker',
    'mimaki', 'mutoh', 'roland', 'rolf',
    'first lite', 'firstlite', 'first-lite',
    'metamark', 'arlon', 'fellers', 'nationalsign',
    'channel letter', 'halo-lit', 'backlit',
]

# Weak signals — at least 2 required if no strong signal present
_WEAK_NBNE_SIGNALS = [
    'signage', 'vinyl', 'aluminium', 'acrylic', 'led module',
    'led strip', 'powder coat', 'fabricat', 'substrate',
    'amazon seller', 'etsy shop', 'ebay seller',
    'supplier', 'quote', 'installation',
]

# Spam title patterns — reject immediately if title matches any of these
_SPAM_TITLE_PATTERNS = [
    r'(?i)\bweekly\b.{0,40}\b(update|digest|news|trade)\b',
    r'(?i)\bdaily\b.{0,30}\b(update|digest|news)\b',
    r'(?i)\b(feedback|satisfaction)\s+survey\b',
    r'(?i)\bwebinar\b',
    r'(?i)\bnewsletter\b',
    r'(?i)\bunsubscribe\b',
    r'(?i)\b(close\s+friend|friend\s+post)\b',
    r'(?i)\bmarketing\s+list\b',
    r'(?i)\bpromotional\s+offer\b',
    r'(?i)\btrade\s+(update|deal|news)\b',
    r'(?i)\bindustry\s+(news|insight|trend)\b',
    r'(?i)\bq[1-4]\s+(wrap|round)[- ]up\b',
    r'(?i)\b(star\s+buy|deal\s+of\s+the\s+(day|week))\b',
    r'(?i)\brewards?\s+program\b',
    r'(?i)\b(survey|questionnaire|feedback)\s+request\b',
]

# Spam body signals — reject if article contains 2+ of these
_SPAM_BODY_SIGNALS = [
    r'(?i)click\s+here\s+to\s+(unsubscribe|view|read)',
    r'(?i)(if\s+you\s+(no longer|didn.t)\s+(wish|want)|to\s+opt[- ]out)',
    r'(?i)view\s+(in|this|the)\s+(browser|online|email)',
    r'(?i)this\s+(email|message)\s+(was|is)\s+(sent|delivered)\s+to\b',
    r'(?i)\byou\s+(are|were|have\s+been)\s+subscribed\b',
    r'(?i)\bsustainable\s+logistics\b',
    r'(?i)\btrade\s+compliance\b',
    r'(?i)\bcybersecurity\s+(tips|update|news)\b',
]


def is_spam_article(title: str, content: str) -> tuple[bool, str]:
    """
    Return (is_spam, reason). Runs entirely locally — no API calls.

    Checks:
      1. Title against known spam/newsletter patterns
      2. Content for marketing boilerplate phrases
      3. NBNE operational signal density
    """
    # 1. Title check
    for pat in _SPAM_TITLE_PATTERNS:
        if re.search(pat, title):
            return True, f'spam_title:{pat[:40]}'

    content_lower = content.lower()
    title_lower = title.lower()
    combined = title_lower + ' ' + content_lower

    # 2. Body spam signals — reject if 2+ present
    spam_hits = sum(
        1 for pat in _SPAM_BODY_SIGNALS if re.search(pat, content)
    )
    if spam_hits >= 2:
        return True, f'spam_body_signals:{spam_hits}'

    # 3. NBNE signal density — must have strong signal OR 2+ weak signals
    has_strong = any(s in combined for s in _STRONG_NBNE_SIGNALS)
    if has_strong:
        return False, ''

    weak_count = sum(1 for s in _WEAK_NBNE_SIGNALS if s in combined)
    if weak_count < 2:
        return True, f'insufficient_nbne_signals:strong=0,weak={weak_count}'

    return False, ''


_QUALITY_CHECK_PROMPT = """Review this draft wiki article for NBNE's internal knowledge base.
Return JSON only: {{"pass": true}} or {{"pass": false, "reason": "..."}}.

NBNE is a sign fabrication and print company in Alnwick, Northumberland.

This article was synthesised from real business emails spanning multiple years,
so pricing figures will naturally vary — this is expected and acceptable.

Reject ONLY if:
- Specific bank account numbers, sort codes, passwords, or API credentials are present
- The article is completely incoherent and cannot be understood
- The article is primarily about a third-party company's promotions, not NBNE operations

Do NOT reject for:
- Pricing ranges or figures that differ across sections (historical variation is normal)
- Information that may be outdated
- Minor inconsistencies in estimates

Article:
{article}
"""


def quality_check(article: str, title: str = '') -> tuple[bool, str, int]:
    """
    Three-tier quality gate. Returns (passed, reason, tokens_used).

    Tier 1 — structural checks (free)
    Tier 2 — spam/relevance checks via is_spam_article() (free)
    Tier 3 — Claude for articles containing financial figures (paid)
    """
    # Tier 1 — structural checks
    if len(article.split()) < 200:
        return False, 'too_short', 0

    if re.search(r'\b[A-Z]{2}\d{2}[A-Z0-9]{11,}\b', article):
        return False, 'possible_iban', 0

    if re.search(r'(?i)(password|passwd)\s*[:=]\s*\S{6,}', article):
        return False, 'possible_credential', 0

    # Tier 2 — spam/relevance check
    # If title not provided, extract from first H1 line of article
    if not title:
        first_line = article.strip().splitlines()[0] if article.strip() else ''
        title = first_line.lstrip('#').strip()

    spam, spam_reason = is_spam_article(title, article)
    if spam:
        return False, spam_reason, 0

    # Tier 3 — Claude for articles containing financial figures
    if re.search(r'£\d+|\biban\b|\baccount\b', article.lower()):
        try:
            response_text, tokens = call_claude(
                _QUALITY_CHECK_PROMPT.format(article=article[:3000]),
                max_tokens=256,
            )
            json_match = re.search(r'\{[^{}]+\}', response_text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                return result.get('pass', False), result.get('reason', 'claude_fail'), tokens
            logger.warning('quality_check: no JSON in Claude response, defaulting to pass')
            return True, 'claude_no_json', tokens
        except Exception as exc:
            logger.warning('quality_check Claude call failed: %s', exc)
            return True, 'claude_unavailable', 0

    return True, 'local_pass', 0


# ---------------------------------------------------------------------------
# Article chunking
# ---------------------------------------------------------------------------

def _chunk_article(title: str, content: str) -> list[str]:
    """
    Split a wiki article into embeddable chunks.

    Strategy:
      1. Split on ## section headers — one chunk per section, prefixed with
         the article title for retrieval context.
      2. If a section exceeds MAX_CHUNK_CHARS, slide a word window through it.
      3. If no ## headers exist (short articles), treat as single chunk or
         apply word windowing if over the limit.

    Always prefixes chunks with the article title so retrieval context is
    preserved even when a section is returned in isolation.
    """
    sections = re.split(r'\n(?=## )', content.strip())

    chunks: list[str] = []

    for section in sections:
        if not section.strip():
            continue
        prefixed = f'# {title}\n\n{section}' if not section.startswith('#') else section
        if len(prefixed) <= MAX_CHUNK_CHARS:
            chunks.append(prefixed)
        else:
            # Slide a word window through the section
            words = prefixed.split()
            window = 250  # ~1250 chars at 5 chars/word
            overlap = 25
            step = window - overlap
            for start in range(0, len(words), step):
                chunk = ' '.join(words[start:start + window])
                if chunk.strip():
                    chunks.append(chunk[:MAX_CHUNK_CHARS])

    return chunks or [content[:MAX_CHUNK_CHARS]]


# ---------------------------------------------------------------------------
# Write wiki article to disk + claw_code_chunks
# ---------------------------------------------------------------------------

def write_wiki_article(
    title: str,
    content: str,
    module: str,
    source_email_id: int | None = None,
) -> str:
    """
    Write article markdown to disk and embed all sections into claw_code_chunks.

    Handles UPDATE semantics: deletes any existing chunks for this file_path
    before inserting new ones, so regenerated articles replace stale embeddings.

    Returns the filepath written.
    """
    from pgvector.psycopg2 import register_vector

    filename = title_to_filename(title)
    filepath = _WIKI_DIR / filename
    file_path_key = f'wiki/modules/{filename}'

    # Write markdown to disk
    _WIKI_DIR.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding='utf-8')
    logger.info('Wrote wiki article: %s', filepath)

    # Chunk article into embeddable sections
    chunks = _chunk_article(title, content)
    logger.info('Embedding %d chunks for "%s"', len(chunks), title)

    with get_conn() as conn:
        register_vector(conn)

        with conn.cursor() as cur:
            # Delete stale chunks for this article (UPDATE semantics)
            cur.execute(
                "DELETE FROM claw_code_chunks WHERE project_id='claw' AND file_path LIKE %s",
                (f'{file_path_key}%',),
            )

            for i, chunk in enumerate(chunks):
                content_hash = hashlib.sha256(chunk.encode()).hexdigest()
                embedding = get_embedding(chunk)
                chunk_file_path = f'{file_path_key}/{i}' if len(chunks) > 1 else file_path_key

                cur.execute(
                    """
                    INSERT INTO claw_code_chunks
                        (project_id, file_path, chunk_content, chunk_type,
                         chunk_name, content_hash, embedding, subproject_id)
                    SELECT %s, %s, %s, %s, %s, %s, %s, %s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM claw_code_chunks
                        WHERE content_hash = %s AND project_id = 'claw'
                    )
                    """,
                    (
                        'claw',
                        chunk_file_path,
                        chunk,
                        'wiki',
                        title,
                        content_hash,
                        embedding,
                        module,
                        content_hash,
                    ),
                )

        conn.commit()

    logger.info('Embedded "%s" (%d chunks) into claw_code_chunks', title, len(chunks))
    return str(filepath)


# ---------------------------------------------------------------------------
# Generation log
# ---------------------------------------------------------------------------

def log_generation(
    source_type: str,
    topic: str,
    source_email_ids: list[int],
    article_title: str,
    wiki_filename: str | None,
    quality_passed: bool,
    quality_reason: str,
    chunk_count: int,
    tokens_used: int,
) -> None:
    """Write a row to cairn_wiki_generation_log."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cairn_wiki_generation_log
                    (source_type, topic, source_email_ids, article_title,
                     wiki_filename, quality_passed, quality_reason,
                     chunk_count, tokens_used)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    source_type,
                    topic,
                    source_email_ids,
                    article_title,
                    wiki_filename,
                    quality_passed,
                    quality_reason,
                    chunk_count,
                    tokens_used,
                ),
            )
            conn.commit()
