"""
Source — crm_reflection.

Reads live CRM project and client rows via the CRM's
``/api/cairn/search`` endpoint, runs each through Claude Haiku
for structured reflection (archetype, pattern, preventative
rule, risk flags), and writes the result into
``cairn_intel.decisions`` as a new source type so it surfaces
alongside disputes, b2b_quotes, and crm_lessons in
``retrieve_similar_decisions``.

This is the "LLM-as-analyst" layer. The raw CRM data is already
searchable via ``search_crm`` (cosine + BM25 hybrid). The
reflection adds interpreted wisdom — what archetype is this,
what pattern repeats, what should future-us do differently —
that raw search can't produce.

Scope for v1
------------

- project (89 rows)  → reflection captures engagement archetype + risks
- client  (92 rows)  → reflection captures relationship pattern + signals

Deliberately excluded:
- material — mostly static catalog rows, low reflection value
- kb (LessonLearned) — already ingested as crm_lesson source, would
  double up

Idempotency via content hash
----------------------------

Each CRM entity's content is hashed at fetch time. The record's
``raw_source_ref.content_hash`` holds the last-seen value. On
re-run, if the stored hash matches the live hash, the Haiku call
is skipped — we already have a fresh reflection. This means the
source is cheap to re-run frequently and only touches the LLM
when an entity has actually changed. The Hetzner cron calls this
every 15 minutes.

Fully independent of the dev box — the source calls the CRM via
HTTPS and writes to ``cairn_intel.decisions`` via the normal
pipeline. When run inside the Hetzner ``deploy-deek-api-1``
container, it talks to the Hetzner deek-db directly.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx
import psycopg2

from .base import HistoricalSource, RawHistoricalRecord, RawOutcome


log = logging.getLogger(__name__)


CRM_DEFAULT_BASE_URL = 'https://crm.nbnesigns.co.uk'
CRM_SEARCH_PATH = '/api/cairn/search'
CRM_REQUEST_TIMEOUT = 15.0

# Max rows per CRM search call. The CRM's /api/cairn/search
# caps limit at 50 server-side, so we paginate via different
# queries per source_type.
CRM_SEARCH_LIMIT = 50

# The 8-tag canonical taxonomy — must match the pipeline's
# ArchetypeTagger so retrieval is consistent.
VALID_TAGS = {
    'adversarial', 'cooperative', 'time_pressured',
    'information_asymmetric', 'repeated_game', 'one_shot',
    'pricing', 'operational',
}


# ── Haiku reflection prompts ────────────────────────────────────────────


_PROJECT_SYSTEM = (
    'You are a signage-business analyst reflecting on a single project '
    'from the NBNE CRM. Given the project data, extract STRUCTURED '
    'wisdom — not a description of what happened, but the rule future-us '
    'should apply to the next project of this shape. '
    '\n\n'
    'Return STRICT JSON only, no prose, no code fences. Fields:\n'
    '{\n'
    '  "archetype_tags": list of 1-4 tags from this closed set: '
    'adversarial, cooperative, time_pressured, information_asymmetric, '
    'repeated_game, one_shot, pricing, operational\n'
    '  "context_summary": 2-3 sentence factual restatement\n'
    '  "chosen_path": what the NBNE team did (from notes), one sentence\n'
    '  "preventative_rule": the rule future-us should apply, one sentence, '
    'actionable verb. Empty string if the project carries no useful rule.\n'
    '  "risk_flags": 0-4 short strings flagging concerns (scope_creep, '
    'payment_risk, design_risk, client_churn, etc). Empty list if none.\n'
    '  "confidence": "high" if notes are rich and outcome clear, "medium" '
    'if partial, "low" if minimal data\n'
    '}'
)


_CLIENT_SYSTEM = (
    'You are a signage-business analyst reflecting on a client record '
    'from the NBNE CRM. Given the client data, extract STRUCTURED wisdom '
    'about HOW to engage with clients of this shape in the future. '
    '\n\n'
    'Return STRICT JSON only, no prose, no code fences. Fields:\n'
    '{\n'
    '  "archetype_tags": list of 1-4 tags from this closed set: '
    'adversarial, cooperative, time_pressured, information_asymmetric, '
    'repeated_game, one_shot, pricing, operational\n'
    '  "context_summary": 2-3 sentence factual restatement\n'
    '  "chosen_path": how the relationship has been handled so far, '
    'one sentence. Empty string if there is no notable engagement history.\n'
    '  "preventative_rule": the rule future-us should apply when this '
    'client enquires again, one sentence. Empty string if no rule applies.\n'
    '  "risk_flags": 0-4 short strings (payment_risk, scope_vague, '
    'single_touch, high_maintenance, etc). Empty list if none.\n'
    '  "confidence": "high" | "medium" | "low"\n'
    '}'
)


# ── Source class ────────────────────────────────────────────────────────


class CrmReflectionSource:
    """Pulls project + client CRM rows, reflects via Haiku, emits records."""

    name: str = 'crm_reflection'
    source_type: str = 'crm_reflection'

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        anthropic_api_key: str | None = None,
        haiku_model: str | None = None,
        intel_db_url: str | None = None,
        records: list[dict] | None = None,
    ):
        self.base_url = (
            base_url or os.getenv('CRM_BASE_URL') or CRM_DEFAULT_BASE_URL
        ).rstrip('/')
        self.api_key = api_key or os.getenv('DEEK_API_KEY') or os.getenv('CAIRN_API_KEY') or os.getenv('CLAW_API_KEY', '')
        self.anthropic_key = (
            anthropic_api_key or os.getenv('ANTHROPIC_API_KEY', '')
        )
        self.haiku_model = haiku_model or os.getenv(
            'CAIRN_INTEL_BULK_MODEL', 'claude-haiku-4-5-20251001'
        )
        # The DB we check for existing reflections (hash dedupe).
        # Default = the same DATABASE_URL the pipeline will write to.
        self.intel_db_url = intel_db_url or os.getenv('DATABASE_URL', '')

        # Test hook — bypass HTTP + LLM when records are pre-supplied.
        self._injected_records = records
        self._anthropic_client: Any = None

    # ── Iteration entry point ───────────────────────────────────────────

    def iter_records(self) -> Iterator[RawHistoricalRecord]:
        if self._injected_records is not None:
            for row in self._injected_records:
                rec = _build_record_from_reflection(row)
                if rec is not None:
                    yield rec
            return

        if not self.api_key:
            raise RuntimeError(
                'crm_reflection: DEEK_API_KEY is not set — cannot '
                'authenticate against the CRM /api/cairn/search endpoint'
            )
        local_model = os.getenv('OLLAMA_CLASSIFIER_MODEL', '').strip()
        if not self.anthropic_key and not local_model:
            raise RuntimeError(
                'crm_reflection: neither ANTHROPIC_API_KEY nor '
                'OLLAMA_CLASSIFIER_MODEL is set — cannot run reflection'
            )

        # Preload the existing hashes so we can dedupe cheaply before
        # spending any Haiku budget.
        existing_hashes = self._load_existing_hashes()

        projects = self._fetch_crm('project', 'project')
        clients = self._fetch_crm('client', 'client')
        log.info(
            'crm_reflection: fetched %d projects, %d clients (existing hashes=%d)',
            len(projects), len(clients), len(existing_hashes),
        )

        for crm_row in projects + clients:
            entity_id = crm_row.get('source_id') or crm_row.get('id')
            if not entity_id:
                continue
            entity_type = crm_row.get('source_type') or 'unknown'
            content = (crm_row.get('content') or '').strip()
            if not content:
                continue

            content_hash = _sha256(content)
            deterministic_id = f'backfill_crm_reflection_{entity_type}_{entity_id}'

            # Skip if we already have a fresh reflection for this entity
            if existing_hashes.get(deterministic_id) == content_hash:
                continue

            try:
                reflection = self._reflect(
                    entity_type=entity_type,
                    content=content,
                    metadata=crm_row.get('metadata') or {},
                )
            except Exception as exc:
                log.warning(
                    'crm_reflection: Haiku call failed for %s: %s',
                    deterministic_id, exc,
                )
                continue

            if reflection is None:
                continue

            record = _build_record(
                entity_type=entity_type,
                entity_id=entity_id,
                content_hash=content_hash,
                raw_metadata=crm_row.get('metadata') or {},
                reflection=reflection,
            )
            if record is not None:
                yield record

    # ── CRM search pagination ───────────────────────────────────────────

    def _fetch_crm(self, source_type: str, broad_query: str) -> list[dict]:
        """Pull all rows of one source_type via /api/cairn/search.

        The CRM's endpoint caps ``limit`` at 50 server-side. We use a
        broad stem query that matches every row's content (the word
        'project', 'client', etc always appears in the flattened
        content) — this is hacky but avoids needing a new endpoint
        for bulk export.
        """
        params = {
            'q': broad_query,
            'types': source_type,
            'limit': CRM_SEARCH_LIMIT,
        }
        try:
            with httpx.Client(timeout=CRM_REQUEST_TIMEOUT) as client:
                response = client.get(
                    f'{self.base_url}{CRM_SEARCH_PATH}',
                    params=params,
                    headers={'Authorization': f'Bearer {self.api_key}'},
                )
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f'crm_reflection: timeout calling {self.base_url}: {exc}'
            )
        if response.status_code != 200:
            raise RuntimeError(
                f'crm_reflection: HTTP {response.status_code} from '
                f'{self.base_url}{CRM_SEARCH_PATH}: {response.text[:200]}'
            )
        data = response.json()
        return data.get('results') or []

    # ── Haiku reflection call ───────────────────────────────────────────

    def _get_anthropic_client(self):
        if self._anthropic_client is None:
            import anthropic
            self._anthropic_client = anthropic.Anthropic(api_key=self.anthropic_key)
        return self._anthropic_client

    def _reflect(
        self,
        entity_type: str,
        content: str,
        metadata: dict,
    ) -> dict | None:
        """Call Haiku with the right prompt for this entity type."""
        if entity_type == 'project':
            system = _PROJECT_SYSTEM
        elif entity_type == 'client':
            system = _CLIENT_SYSTEM
        else:
            return None

        # Give the model richer context via the metadata block
        metadata_hint = (
            json.dumps(metadata, default=str)[:800] if metadata else '{}'
        )
        user_prompt = (
            f'Entity content:\n{content[:3000]}\n\n'
            f'Entity metadata:\n{metadata_hint}\n\n'
            'Return strict JSON only.'
        )

        # ── Primary path: local Ollama with format=json ───────────────
        local_model = os.getenv('OLLAMA_CLASSIFIER_MODEL', '').strip()
        if local_model:
            local_result = _reflect_via_ollama(system, user_prompt, local_model)
            if local_result is not None:
                return local_result
            # Fall through to Haiku on local failure

        # ── Fallback: Haiku API ───────────────────────────────────────
        if not self.anthropic_key:
            return None
        client = self._get_anthropic_client()
        resp = client.messages.create(
            model=self.haiku_model,
            max_tokens=500,
            system=system,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
        text = _first_text(resp).strip()
        return _parse_json_output(text)

    # ── Existing hash lookup for dedupe ─────────────────────────────────

    def _load_existing_hashes(self) -> dict[str, str]:
        """Return {deterministic_id: content_hash} for existing crm_reflection rows."""
        if not self.intel_db_url:
            return {}
        try:
            conn = psycopg2.connect(self.intel_db_url, connect_timeout=5)
        except Exception as exc:
            log.warning(
                'crm_reflection: could not connect to intel DB for '
                'hash dedupe: %s — all entities will be re-reflected',
                exc,
            )
            return {}
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, raw_source_ref->>'content_hash'
                    FROM cairn_intel.decisions
                    WHERE source_type = 'crm_reflection'
                    """
                )
                return {row[0]: row[1] for row in cur.fetchall() if row[1]}
        finally:
            conn.close()


# ── Record builders ─────────────────────────────────────────────────────


def _build_record(
    entity_type: str,
    entity_id: str,
    content_hash: str,
    raw_metadata: dict,
    reflection: dict,
) -> RawHistoricalRecord | None:
    """Convert a successful Haiku reflection into a RawHistoricalRecord."""
    context_summary = (reflection.get('context_summary') or '').strip()
    if not context_summary:
        return None

    tags_raw = reflection.get('archetype_tags') or []
    if not isinstance(tags_raw, list):
        return None
    tags = [
        t.strip().lower() for t in tags_raw
        if isinstance(t, str) and t.strip().lower() in VALID_TAGS
    ][:4]

    chosen_path = (reflection.get('chosen_path') or '').strip() or (
        f'Engagement recorded in CRM {entity_type} record.'
    )

    preventative = (reflection.get('preventative_rule') or '').strip()
    verbatim_lesson = preventative or None

    confidence = (reflection.get('confidence') or 'medium').lower()
    # Map confidence → signal_strength. Hand-written disputes get 0.95;
    # these are LLM-derived so cap below that.
    signal_strength = {
        'high': 0.8,
        'medium': 0.7,
        'low': 0.55,
    }.get(confidence, 0.7)

    risk_flags = reflection.get('risk_flags') or []
    if not isinstance(risk_flags, list):
        risk_flags = []

    raw_source_ref: dict = {
        'entity_type': entity_type,
        'entity_id': entity_id,
        'content_hash': content_hash,
        'crm_metadata': raw_metadata,
        'risk_flags': risk_flags,
        'confidence': confidence,
    }

    deterministic_id = f'backfill_crm_reflection_{entity_type}_{entity_id}'
    case_id = f'crm_reflection_{entity_type}_{entity_id}'

    outcome = RawOutcome(
        observed_at=datetime.now(tz=timezone.utc),
        actual_result=(
            f'CRM {entity_type} reflection generated by Haiku. '
            + (f'Flags: {", ".join(risk_flags)}.' if risk_flags else '')
        ),
    )

    return RawHistoricalRecord(
        deterministic_id=deterministic_id,
        source_type='crm_reflection',
        decided_at=datetime.now(tz=timezone.utc),
        chosen_path=chosen_path,
        context_summary=context_summary,
        archetype_tags=tags,
        rejected_paths=None,
        signal_strength=signal_strength,
        case_id=case_id,
        raw_source_ref=raw_source_ref,
        needs_privacy_scrub=False,
        needs_privacy_review=False,
        outcome=outcome,
        verbatim_lesson=verbatim_lesson,
        verbatim_lesson_model='haiku_reflection',
    )


def _build_record_from_reflection(row: dict) -> RawHistoricalRecord | None:
    """Test hook: when records= is passed, treat each row as an already-
    reflected row shaped like {entity_type, entity_id, content_hash,
    raw_metadata, reflection}. Useful for testing without HTTP + LLM."""
    return _build_record(
        entity_type=row.get('entity_type', 'project'),
        entity_id=row.get('entity_id', 'test-id'),
        content_hash=row.get('content_hash', 'sha-test'),
        raw_metadata=row.get('raw_metadata', {}),
        reflection=row.get('reflection', {}),
    )


# ── Helpers ─────────────────────────────────────────────────────────────


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def _first_text(resp: Any) -> str:
    try:
        for block in resp.content:
            if getattr(block, 'type', '') == 'text':
                return block.text
    except Exception:
        pass
    return ''


def _reflect_via_ollama(system: str, user_prompt: str, model: str) -> dict | None:
    """Run a reflection call against local Ollama with format=json.

    Returns the parsed dict on success, or None on any failure
    (network, timeout, bad JSON) so the caller can fall back to Haiku.
    Never raises.
    """
    base_url = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434').rstrip('/')
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f'{base_url}/api/chat',
                json={
                    'model': model,
                    'messages': [
                        {'role': 'system', 'content': system},
                        {'role': 'user', 'content': user_prompt},
                    ],
                    'stream': False,
                    'format': 'json',
                    'options': {'num_predict': 500, 'temperature': 0.0},
                },
            )
    except Exception as exc:
        log.warning('crm_reflection: Ollama call failed (%s) — falling back to Haiku',
                    type(exc).__name__)
        return None

    if r.status_code != 200:
        log.warning('crm_reflection: Ollama HTTP %d — falling back to Haiku',
                    r.status_code)
        return None

    try:
        raw = (r.json().get('message', {}) or {}).get('content', '').strip()
    except Exception:
        return None
    if not raw:
        return None

    parsed = _parse_json_output(raw)
    if not parsed:
        log.warning('crm_reflection: local JSON parse failed — falling back to Haiku')
        return None
    log.info('crm_reflection: local model=%s produced reflection', model)
    return parsed


def _parse_json_output(raw: str) -> dict | None:
    """Parse Haiku's JSON output, tolerating a few common quirks."""
    if not raw:
        return None
    # Strip markdown code fences if the model wraps the JSON
    raw = raw.strip()
    if raw.startswith('```'):
        # Remove leading fence + optional lang tag
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```\s*$', '', raw)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    # Try to extract a JSON object from inside a larger string
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    log.warning('crm_reflection: could not parse reflection JSON: %s', raw[:200])
    return None
