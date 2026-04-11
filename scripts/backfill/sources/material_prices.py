"""
Source — material_prices.

Extracts supplier + material + price observations from the
``cairn_email_raw`` archive and writes them into
``cairn_intel.decisions`` as ``source_type='material_price'``.

Why this exists
---------------

NBNE's material costs drift over time: aluminium composite, vinyl
films, acrylic sheet, paint, blanks. The CRM has a ``Material``
table but it's a human-curated catalog, not a historical price
trail — we can't ask Cairn "what was the last price we paid for
1.5mm Dibond from First Fix?" today because that data lives only
in email threads with suppliers.

This source is the first pass of that data trail. It scans the
toby@ / sales@ / cairn@ inboxes for messages that look like
supplier pricing correspondence (invoices, quotes, price lists,
tariff changes), runs each candidate through Claude Haiku with
a structured extraction prompt, and writes one cairn_intel
decision per price observation — where the ``preventative_rule``
is the benchmark to cite next time.

Each observation surfaces via ``retrieve_similar_decisions`` so
the chat agent can answer questions like:

  "How much does 1.5mm Dibond typically cost?"
  "What's our usual aluminium composite supplier?"
  "Have we seen a price hike on vinyl recently?"

Fit inside cairn_intel
----------------------

Using the existing ``cairn_intel.decisions`` table (not a new
prices table) is deliberate. It means:

- No new chat tool required — retrieve_similar_decisions already
  handles the query surface
- No new schema to maintain
- Price observations rank alongside disputes, reflections and
  lessons for cross-domain retrieval ("client X asked about ACM,
  we have their project history AND our current ACM prices")

Filter heuristics
-----------------

Pre-LLM filter is conservative — we only call Haiku on messages
that look like supplier correspondence to save budget:

1. Mailbox in {toby, sales, cairn}
2. body_text length between 100 and 12000 chars
3. sender NOT matching a blocklist of newsletters / platform
   automation (amazon.*, stripe.*, postmark.*, noreply@, etc)
4. Contains at least one pricing keyword (price, quote, cost,
   invoice, £, $, quote, tariff, rate card, pro forma)
5. Contains at least one material keyword (aluminium, acrylic,
   vinyl, dibond, foamex, acm, pvc, polycarbonate, mdf, correx,
   composite, sheet, blank, adhesive, laminate, banner, tape)

Not guaranteed to catch every price email and will have some
false positives — Haiku is the second filter. Any message that
Haiku can't extract a structured price from is dropped.

Idempotency
-----------

Deterministic ID: ``backfill_material_price_{message_id_sha[:16]}``
so re-runs upsert. The existing cairn_intel.decisions ON CONFLICT
pattern handles it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Iterator

import psycopg2

from .base import HistoricalSource, RawHistoricalRecord, RawOutcome


log = logging.getLogger(__name__)


MAX_BODY_CHARS_FOR_HAIKU = 6000
MIN_BODY_CHARS = 100
MAX_BODY_CHARS = 12000
DEFAULT_MAX_CANDIDATES = 500

PRICING_KEYWORDS = (
    'price', 'pricing', 'quote', 'quoted', 'cost', 'costing',
    'invoice', 'pro forma', 'tariff', 'rate card', 'each',
    '£', '€', '$', 'per sheet', 'per roll', 'per metre', 'per meter',
)

MATERIAL_KEYWORDS = (
    'aluminium', 'aluminum', 'acrylic', 'vinyl', 'dibond', 'acm',
    'foamex', 'foam board', 'pvc', 'polycarbonate', 'mdf', 'correx',
    'composite', 'sheet', 'blank', 'adhesive', 'laminate', 'banner',
    'tape', 'film', 'powder', 'paint', 'ink', 'cartridge', 'toner',
)

# Block senders that are automation / platform, not supplier correspondence
BLOCK_SENDER_PATTERNS = (
    'noreply', 'no-reply', 'donotreply', 'automated', 'notification',
    'postmark.com', 'stripe.com', 'amazonaws.com', 'amazon.co.uk',
    'amazon.com', 'newsletter', 'unsubscribe', 'googlegroups',
    'etsy.com', 'ebay.co.uk', 'facebook.com', 'linkedin.com',
    'zendesk', 'intercom', 'hubspot', 'mailchimp', 'sendgrid',
    'calendly', 'zoom.us',
)


_EXTRACTION_SYSTEM = (
    'You are a procurement analyst scanning supplier emails for '
    'material pricing observations. Given the email body, extract '
    'STRUCTURED pricing data ONLY IF the email actually contains a '
    'specific supplier price for a specific material.\n\n'
    'Return STRICT JSON only, no prose, no code fences. Fields:\n'
    '{\n'
    '  "has_price": true/false — only true if the email cites a '
    'specific numeric price for a specific material from an '
    'identifiable supplier\n'
    '  "supplier": short supplier name, or "" if unclear\n'
    '  "material": short material name (e.g. "1.5mm Dibond", '
    '"3mm Foamex", "vinyl wrap"), or "" if unclear\n'
    '  "unit": unit of pricing ("per sheet", "per roll", "per m2", '
    '"each", "per litre"), or "" if unclear\n'
    '  "price_gbp": numeric price in GBP if visible. If another '
    'currency, convert approximately or leave as null\n'
    '  "notes": optional 1-sentence context (bulk deal, '
    'price increase, discontinued, etc)\n'
    '  "confidence": "high" | "medium" | "low"\n'
    '}\n\n'
    'If has_price is false, return the other fields as empty '
    'strings / null. Do not guess.'
)


# ── Source class ────────────────────────────────────────────────────────


class MaterialPricesSource:
    """Extracts supplier pricing observations from the email archive."""

    name: str = 'material_prices'
    source_type: str = 'material_price'

    def __init__(
        self,
        email_db_url: str | None = None,
        anthropic_api_key: str | None = None,
        haiku_model: str | None = None,
        mailboxes: list[str] | None = None,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        records: list[dict] | None = None,
    ):
        # Default: same DB as cairn_intel. cairn_email_raw lives
        # alongside cairn_intel on nbne1's claw database AND also
        # on Hetzner's cairn DB (depending on which side this runs).
        self.email_db_url = email_db_url or os.getenv('DATABASE_URL', '')
        self.anthropic_key = anthropic_api_key or os.getenv('ANTHROPIC_API_KEY', '')
        self.haiku_model = haiku_model or os.getenv(
            'CAIRN_INTEL_BULK_MODEL', 'claude-haiku-4-5-20251001'
        )
        self.mailboxes = mailboxes or ['toby', 'sales', 'cairn']
        self.max_candidates = max_candidates
        self._injected_records = records
        self._anthropic_client: Any = None

    def iter_records(self) -> Iterator[RawHistoricalRecord]:
        if self._injected_records is not None:
            for row in self._injected_records:
                record = _build_price_record(
                    email=row['email'],
                    extraction=row['extraction'],
                )
                if record is not None:
                    yield record
            return

        if not self.email_db_url:
            raise RuntimeError(
                'material_prices: DATABASE_URL not set — cannot reach '
                'cairn_email_raw'
            )
        if not self.anthropic_key:
            raise RuntimeError(
                'material_prices: ANTHROPIC_API_KEY not set — cannot '
                'run Haiku extraction'
            )

        candidates = self._fetch_candidate_emails()
        log.info('material_prices: %d candidate emails selected', len(candidates))

        for email in candidates:
            try:
                extraction = self._extract(email['body_text'])
            except Exception as exc:
                log.warning(
                    'material_prices: Haiku extraction failed on %s: %s',
                    email['message_id'], exc,
                )
                continue

            if not extraction or not extraction.get('has_price'):
                continue

            record = _build_price_record(email=email, extraction=extraction)
            if record is not None:
                yield record

    # ── Candidate email selection ───────────────────────────────────────

    def _fetch_candidate_emails(self) -> list[dict]:
        conn = psycopg2.connect(self.email_db_url, connect_timeout=8)
        try:
            with conn.cursor() as cur:
                cur.execute('SET statement_timeout = 60000')

                # Build the big WHERE using positional params; the
                # ILIKE OR chains are unavoidable because we don't
                # have pg_trgm on cairn_email_raw.
                pricing_or = ' OR '.join(
                    ['body_text ILIKE %s'] * len(PRICING_KEYWORDS)
                )
                material_or = ' OR '.join(
                    ['body_text ILIKE %s'] * len(MATERIAL_KEYWORDS)
                )
                block_not = ' AND '.join(
                    ['COALESCE(sender, \'\') NOT ILIKE %s'] * len(BLOCK_SENDER_PATTERNS)
                )
                params: list[Any] = []
                params.extend([f'%{kw}%' for kw in PRICING_KEYWORDS])
                params.extend([f'%{kw}%' for kw in MATERIAL_KEYWORDS])
                params.extend([f'%{p}%' for p in BLOCK_SENDER_PATTERNS])
                params.extend([self.mailboxes, MIN_BODY_CHARS, MAX_BODY_CHARS, self.max_candidates])

                sql = f"""
                    SELECT id, message_id, mailbox, sender, subject,
                           body_text, received_at
                    FROM cairn_email_raw
                    WHERE body_text IS NOT NULL
                      AND ({pricing_or})
                      AND ({material_or})
                      AND ({block_not})
                      AND mailbox = ANY(%s)
                      AND LENGTH(body_text) BETWEEN %s AND %s
                    ORDER BY received_at DESC NULLS LAST
                    LIMIT %s
                """
                cur.execute(sql, params)
                rows = cur.fetchall()
                col_names = [d[0] for d in cur.description]
        finally:
            conn.close()
        return [dict(zip(col_names, row)) for row in rows]

    # ── Haiku extraction call ───────────────────────────────────────────

    def _get_anthropic_client(self):
        if self._anthropic_client is None:
            import anthropic
            self._anthropic_client = anthropic.Anthropic(api_key=self.anthropic_key)
        return self._anthropic_client

    def _extract(self, body_text: str) -> dict | None:
        body = (body_text or '')[:MAX_BODY_CHARS_FOR_HAIKU]
        client = self._get_anthropic_client()
        resp = client.messages.create(
            model=self.haiku_model,
            max_tokens=400,
            system=_EXTRACTION_SYSTEM,
            messages=[{'role': 'user', 'content': body}],
        )
        return _parse_json_output(_first_text(resp))


# ── Record builder ──────────────────────────────────────────────────────


def _build_price_record(
    email: dict,
    extraction: dict,
) -> RawHistoricalRecord | None:
    if not extraction.get('has_price'):
        return None

    supplier = (extraction.get('supplier') or '').strip()
    material = (extraction.get('material') or '').strip()
    unit = (extraction.get('unit') or '').strip()
    price_gbp = extraction.get('price_gbp')
    notes = (extraction.get('notes') or '').strip()
    confidence = (extraction.get('confidence') or 'medium').lower()

    if not supplier or not material:
        return None
    if price_gbp is None:
        return None

    # Format price with appropriate precision
    try:
        price_float = float(price_gbp)
    except Exception:
        return None

    price_txt = f'£{price_float:,.2f}'
    observed_date = email.get('received_at')
    observed_date_iso = (
        observed_date.strftime('%Y-%m-%d') if observed_date else 'unknown date'
    )

    context_summary = (
        f'{supplier} priced {material} at {price_txt}{" " + unit if unit else ""} '
        f'on {observed_date_iso}. Source: email from {email.get("sender") or "unknown sender"}, '
        f'subject "{(email.get("subject") or "").strip()[:80]}".'
    )
    if notes:
        context_summary += f' Note: {notes}'

    chosen_path = (
        f'Recorded {supplier} price benchmark: {material} at {price_txt}'
        + (f' {unit}' if unit else '')
        + f' ({observed_date_iso})'
    )

    preventative_rule = (
        f'When quoting work involving {material}, reference '
        f'{supplier} benchmark of {price_txt}'
        + (f' {unit}' if unit else '')
        + f' from {observed_date_iso}.'
    )

    signal_strength = {
        'high': 0.9,
        'medium': 0.8,
        'low': 0.65,
    }.get(confidence, 0.8)

    # Deterministic ID from the source email's message_id
    msg_id = email.get('message_id') or str(email.get('id'))
    short_hash = hashlib.sha256(msg_id.encode('utf-8')).hexdigest()[:16]
    deterministic_id = f'backfill_material_price_{short_hash}'
    case_id = f'material_price_{supplier.lower().replace(" ", "_")}_{material.lower().replace(" ", "_")[:30]}'

    raw_source_ref = {
        'message_id': msg_id,
        'mailbox': email.get('mailbox'),
        'sender': email.get('sender'),
        'subject': email.get('subject'),
        'received_at': observed_date.isoformat() if observed_date else None,
        'supplier': supplier,
        'material': material,
        'unit': unit,
        'price_gbp': price_float,
        'confidence': confidence,
        'notes': notes,
    }

    outcome = RawOutcome(
        observed_at=observed_date if observed_date else datetime.now(tz=timezone.utc),
        actual_result=(
            f'Price recorded in email archive. Supplier: {supplier}. '
            f'Material: {material}. Price: {price_txt}'
            + (f' {unit}' if unit else '')
            + '.'
        ),
    )

    decided_at = observed_date
    if decided_at and decided_at.tzinfo is None:
        decided_at = decided_at.replace(tzinfo=timezone.utc)
    if not decided_at:
        decided_at = datetime.now(tz=timezone.utc)

    return RawHistoricalRecord(
        deterministic_id=deterministic_id,
        source_type='material_price',
        decided_at=decided_at,
        chosen_path=chosen_path,
        context_summary=context_summary,
        # The 'pricing' tag always applies; 'operational' is the
        # secondary anchor.
        archetype_tags=['pricing', 'operational'],
        rejected_paths=None,
        signal_strength=signal_strength,
        case_id=case_id,
        raw_source_ref=raw_source_ref,
        needs_privacy_scrub=False,
        needs_privacy_review=False,
        outcome=outcome,
        verbatim_lesson=preventative_rule,
        verbatim_lesson_model='haiku_extraction',
    )


# ── Helpers ─────────────────────────────────────────────────────────────


def _first_text(resp: Any) -> str:
    try:
        for block in resp.content:
            if getattr(block, 'type', '') == 'text':
                return block.text
    except Exception:
        pass
    return ''


def _parse_json_output(raw: str) -> dict | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```\s*$', '', raw)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return None
