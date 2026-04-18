"""
Email classifier for the triage pipeline.

Given an email's sender, subject, and body, calls Claude Haiku with
a tight structured prompt and returns:

    {
        'classification': 'new_enquiry' | 'existing_project_reply'
                          | 'automation' | 'personal' | 'unclassified',
        'confidence': 'high' | 'medium' | 'low',
        'reason': <short string>,
        'client_name_guess': <string or empty>,
        'project_hint': <string or empty — subject line hint at a CRM project>,
    }

The classifier is deliberately narrow. It does NOT try to extract
pricing, intent, or detailed scope — that's the job of
``analyze_enquiry`` which runs downstream for rows classified as
``new_enquiry``.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any


log = logging.getLogger(__name__)


MAX_BODY_CHARS_FOR_HAIKU = 3500
MAX_BODY_CHARS_FOR_LOCAL = 2500  # Smaller local context window

DEFAULT_HAIKU_MODEL = 'claude-haiku-4-5-20251001'

# Local Ollama primary path. When OLLAMA_CLASSIFIER_MODEL is set
# (e.g. qwen2.5:7b-instruct), the classifier tries the local model
# first and only falls back to Haiku on error or malformed output.
# Set the model explicitly — we do NOT default to any model because
# classifier quality depends heavily on the chosen base (qwen 2.5
# has reliable JSON mode; gemma does not).
OLLAMA_CLASSIFIER_MODEL_ENV = 'OLLAMA_CLASSIFIER_MODEL'
OLLAMA_REQUEST_TIMEOUT_SEC = 30.0

VALID_CLASSIFICATIONS = {
    'new_enquiry',
    'existing_project_reply',
    'automation',
    'personal',
    'unclassified',
}


_CLASSIFIER_SYSTEM = (
    'You are a careful email triage agent for NBNE, a Northumberland '
    'signage manufacturer. Given one incoming email (sender, subject, '
    'body), classify it into ONE category so the rest of the pipeline '
    'knows what to do next.\n\n'
    'Categories (closed list, pick exactly one):\n'
    '  new_enquiry             — a potential client asking for a quote, '
    'a product, a service, or information about pricing / availability. '
    'Could be cold (new contact) or warm (via referral). The defining '
    'signal is "they want something from NBNE".\n'
    '  existing_project_reply  — a continuation of a project NBNE is '
    'already working on. Signals: "Re:" in the subject referencing a '
    'prior thread, client name matching a live project, specific '
    'reference numbers (NBNE-YYYY-NNN), or follow-up language '
    '("as discussed", "the signs we talked about").\n'
    '  automation              — a machine-generated email: '
    'newsletters, order confirmations from suppliers, platform '
    'notifications (Amazon, eBay, Stripe, etc), delivery tracking, '
    'password resets, calendar invites, marketing blasts.\n'
    '  personal                — a personal message unrelated to NBNE '
    'business. Family, friends, non-business acquaintances.\n'
    '  unclassified            — you genuinely cannot tell. Use this '
    'sparingly; only if the email is ambiguous or malformed.\n\n'
    'Return STRICT JSON ONLY. No prose, no code fences:\n'
    '{\n'
    '  "classification": "new_enquiry" | "existing_project_reply" | '
    '"automation" | "personal" | "unclassified",\n'
    '  "confidence": "high" | "medium" | "low",\n'
    '  "reason": "one short sentence explaining the classification",\n'
    '  "client_name_guess": "extracted client/business name, or empty string",\n'
    '  "project_hint": "any project reference found in the subject, or empty string"\n'
    '}'
)


def classify_email(
    email: dict,
    anthropic_api_key: str | None = None,
    haiku_model: str | None = None,
) -> dict:
    """Classify one email.

    Routing:
    1. Cheap regex sender blocklist — catches automation senders with
       zero model calls
    2. Local Ollama (if OLLAMA_CLASSIFIER_MODEL is set) — Qwen 2.5 7B
       with format=json for reliable JSON output
    3. Haiku API fallback — only if local is unset or fails

    ``email`` must have keys: sender, subject, body_text.
    Returns a dict with classification, confidence, reason, etc.
    On failure, returns a dict with classification='unclassified'
    and confidence='low' — never raises.
    """
    sender = (email.get('sender') or '').strip()
    subject = (email.get('subject') or '').strip()
    body = (email.get('body_text') or '').strip()

    if not body and not subject:
        return _fallback('email has no subject and no body')

    # Automation senders can be caught cheaply without burning a model
    # call — same blocklist logic as the material_prices source.
    if _is_automation_sender(sender):
        return {
            'classification': 'automation',
            'confidence': 'high',
            'reason': 'sender matches automation / platform blocklist',
            'client_name_guess': '',
            'project_hint': '',
        }

    # ── Primary path: local Ollama ────────────────────────────────────
    local_model = os.getenv(OLLAMA_CLASSIFIER_MODEL_ENV, '').strip()
    if local_model:
        local_result = _classify_via_ollama(
            sender=sender, subject=subject, body=body, model=local_model,
        )
        if local_result is not None:
            return local_result
        # Fall through to Haiku on local failure

    # ── Fallback: Haiku API ───────────────────────────────────────────
    key = anthropic_api_key or os.getenv('ANTHROPIC_API_KEY', '').strip()
    if not key:
        return _fallback(
            'ANTHROPIC_API_KEY not set and local classifier unavailable',
        )

    model = haiku_model or os.getenv(
        'CAIRN_INTEL_BULK_MODEL', DEFAULT_HAIKU_MODEL,
    )

    user_prompt = (
        f'Sender: {sender or "(unknown)"}\n'
        f'Subject: {subject or "(no subject)"}\n\n'
        f'Body:\n{body[:MAX_BODY_CHARS_FOR_HAIKU]}\n\n'
        'Classify now. Return JSON only.'
    )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=model,
            max_tokens=260,
            system=_CLASSIFIER_SYSTEM,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
    except Exception as exc:
        log.warning('classifier: Haiku call failed: %s', exc)
        return _fallback(f'Haiku call failed: {type(exc).__name__}')

    raw = _first_text(resp).strip()
    parsed = _parse_json(raw)
    if not parsed:
        return _fallback('classifier JSON parse failed')

    return _normalise_parsed(parsed)


def _classify_via_ollama(
    sender: str, subject: str, body: str, model: str,
) -> dict | None:
    """Classify via local Ollama with format=json enforcement.

    Returns the classification dict on success, or None on any failure
    (network, timeout, bad JSON, empty response) so the caller can
    fall back to Haiku cleanly. Never raises.
    """
    base_url = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434').rstrip('/')
    user_prompt = (
        f'Sender: {sender or "(unknown)"}\n'
        f'Subject: {subject or "(no subject)"}\n\n'
        f'Body:\n{body[:MAX_BODY_CHARS_FOR_LOCAL]}\n\n'
        'Classify now. Return JSON only.'
    )
    try:
        import httpx
        with httpx.Client(timeout=OLLAMA_REQUEST_TIMEOUT_SEC) as client:
            r = client.post(
                f'{base_url}/api/chat',
                json={
                    'model': model,
                    'messages': [
                        {'role': 'system', 'content': _CLASSIFIER_SYSTEM},
                        {'role': 'user', 'content': user_prompt},
                    ],
                    'stream': False,
                    'format': 'json',
                    'options': {'num_predict': 260, 'temperature': 0.0},
                },
            )
    except Exception as exc:
        log.warning('classifier: Ollama call failed (%s) — falling back to Haiku',
                    type(exc).__name__)
        return None

    if r.status_code != 200:
        log.warning('classifier: Ollama HTTP %d — falling back to Haiku',
                    r.status_code)
        return None

    try:
        raw = (r.json().get('message', {}) or {}).get('content', '').strip()
    except Exception:
        return None
    if not raw:
        return None

    parsed = _parse_json(raw)
    if not parsed:
        log.warning('classifier: local JSON parse failed — falling back to Haiku')
        return None

    log.info('classifier: local model=%s classified as %s',
             model, parsed.get('classification', '?'))
    return _normalise_parsed(parsed)


def _normalise_parsed(parsed: dict) -> dict:
    """Coerce a parsed model output into the canonical classifier schema."""
    classification = parsed.get('classification', 'unclassified')
    if classification not in VALID_CLASSIFICATIONS:
        classification = 'unclassified'
    return {
        'classification': classification,
        'confidence': parsed.get('confidence', 'medium'),
        'reason': (parsed.get('reason') or '').strip()[:500],
        'client_name_guess': (parsed.get('client_name_guess') or '').strip()[:200],
        'project_hint': (parsed.get('project_hint') or '').strip()[:200],
    }


def _is_automation_sender(sender: str) -> bool:
    """Cheap regex-based automation detector — same patterns the
    material_prices source uses, plus a few email-specific ones."""
    if not sender:
        return False
    lowered = sender.lower()
    patterns = (
        'noreply', 'no-reply', 'donotreply', 'automated', 'notification',
        'notifications@', 'postmark.com', 'stripe.com', 'amazonaws.com',
        'newsletter', 'unsubscribe', 'googlegroups', 'etsy.com',
        'ebay.co.uk', 'facebook.com', 'facebookmail.com', 'linkedin.com',
        'zendesk', 'intercom', 'hubspot', 'mailchimp', 'sendgrid',
        'calendly', 'zoom.us', 'slack.com', 'trustpilot',
        '@amazon.co.uk', '@amazon.com', 'alerts@', 'updates@',
        'mailer@', 'accounts@',
    )
    return any(p in lowered for p in patterns)


def _fallback(reason: str) -> dict:
    return {
        'classification': 'unclassified',
        'confidence': 'low',
        'reason': reason,
        'client_name_guess': '',
        'project_hint': '',
    }


def _first_text(resp: Any) -> str:
    try:
        for block in resp.content:
            if getattr(block, 'type', '') == 'text':
                return block.text
    except Exception:
        pass
    return ''


def _parse_json(raw: str) -> dict | None:
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
