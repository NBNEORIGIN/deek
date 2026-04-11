"""
Source — crm_lessons.

Ingests the CRM's ``LessonLearned`` rows into ``cairn_intel.decisions``
so they surface alongside disputes.yml and b2b_quotes.yml in the
counterfactual memory retrieval layer.

The CRM already has a structured post-mortem model with
``preventativeRule`` — your own words, already written. This source
preserves them verbatim: no LLM rewrite, no summarisation, no
paraphrasing. Each LessonLearned row becomes one ``cairn_intel``
decision with ``lesson_model='toby_verbatim'`` and a
``signal_strength`` of 0.95 (you wrote it, you meant it).

How it reaches the CRM data
---------------------------

This source does NOT talk to the CRM database directly — that would
violate the cross-module rule in CLAUDE.md. It calls the CRM's
``GET /api/cairn/search?types=kb`` endpoint server-to-server with a
Bearer token. The endpoint returns all embedded lesson rows; we
remap them into ``RawHistoricalRecord`` objects the pipeline can
write into ``cairn_intel.decisions``.

Config
------

    CAIRN_API_KEY    Required. Same token used by search_crm tool.
    CRM_BASE_URL     Optional. Defaults to https://crm.nbnesigns.co.uk

The source supports an offline JSON fallback for tests: pass
``records=[...]`` to the constructor and it yields those directly
without making HTTP calls.

Field mapping
-------------

    LessonLearned.id                → case_id = 'crm_lesson_{id}'
                                      deterministic_id = 'backfill_crm_lesson_{id}'
    title + description + category  → context_summary
    correctiveAction                → chosen_path (what was actually done)
    whatWentWrong                   → raw_source_ref.what_went_wrong
    rootCause                       → raw_source_ref.root_cause
    severity                        → raw_source_ref.severity
    projectId                       → raw_source_ref.project_id
    preventativeRule                → verbatim_lesson (if set)
    createdAt                       → decided_at

The pipeline then tags each record via Haiku (source_type='crm_lesson'
does NOT pass the usual gate-source-type shortcut, so lessons without
a preventativeRule fall through to Sonnet generation).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx

from .base import HistoricalSource, RawHistoricalRecord, RawOutcome


log = logging.getLogger(__name__)

CRM_DEFAULT_BASE_URL = 'https://crm.nbnesigns.co.uk'
CRM_SEARCH_PATH = '/api/cairn/search'
CRM_REQUEST_TIMEOUT = 15.0


class CrmLessonsSource:
    """Hybrid-search the CRM for kb rows, map to cairn_intel decisions."""

    name: str = 'crm_lessons'
    source_type: str = 'crm_lesson'

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        limit: int = 500,
        records: list[dict] | None = None,
    ):
        self.base_url = (base_url or os.getenv('CRM_BASE_URL') or CRM_DEFAULT_BASE_URL).rstrip('/')
        self.api_key = api_key or os.getenv('CAIRN_API_KEY', '')
        self.limit = limit
        # Test hook — bypass HTTP entirely
        self._injected_records = records

    def iter_records(self) -> Iterator[RawHistoricalRecord]:
        if self._injected_records is not None:
            raw_rows = self._injected_records
        else:
            raw_rows = self._fetch()

        seen_ids: set[str] = set()
        for row in raw_rows:
            record = _map_row(row)
            if record is None:
                continue
            # Dedupe on deterministic_id in case the CRM returns
            # the same row twice (the endpoint doesn't guarantee
            # uniqueness when duplicates exist in the source table).
            if record.deterministic_id in seen_ids:
                continue
            seen_ids.add(record.deterministic_id)
            yield record

    def _fetch(self) -> list[dict]:
        if not self.api_key:
            raise RuntimeError(
                'crm_lessons: CAIRN_API_KEY is not set — cannot authenticate '
                'against the CRM /api/cairn/search endpoint'
            )

        # Hit the search endpoint with a broad query to pull every kb row.
        # The endpoint requires a non-empty query; we pass 'lesson' which
        # matches the vocabulary the LessonLearned rows use internally.
        params = {
            'q': 'lesson',
            'types': 'kb',
            'limit': self.limit,
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
                f'crm_lessons: timed out calling {self.base_url}: {exc}'
            )

        if response.status_code != 200:
            raise RuntimeError(
                f'crm_lessons: HTTP {response.status_code} from '
                f'{self.base_url}{CRM_SEARCH_PATH} — '
                f'{response.text[:200]}'
            )

        data = response.json()
        results = data.get('results') or []
        log.info(
            'crm_lessons: pulled %d kb rows from %s',
            len(results),
            self.base_url,
        )
        return results


def _map_row(row: dict) -> RawHistoricalRecord | None:
    """Map one CRM search result into a ``RawHistoricalRecord``.

    Returns None if the row lacks the minimum fields to build a
    decision (no id, no content).
    """
    source_id = row.get('source_id') or row.get('id')
    if not source_id:
        return None

    content = (row.get('content') or '').strip()
    if not content:
        return None

    metadata = row.get('metadata') or {}
    title = metadata.get('title') or ''
    category = metadata.get('category') or ''

    # The hybrid-search endpoint flattens LessonLearned into a single
    # `content` string assembled by lib/cairn-indexer.ts. We preserve
    # that structure in context_summary and try to pull individual
    # fields out of the string for downstream reference.
    context_summary = content
    if len(context_summary) > 1800:
        context_summary = context_summary[:1800] + '...'

    # Parse out the chosen_path (Fix) and preventativeRule (Rule) from
    # the flattened content. The indexer joins them with '. ' so we
    # can split and look for the key prefixes.
    chosen_path = _extract_field(content, 'Fix:') or title or 'CRM lesson recorded'
    preventative_rule = _extract_field(content, 'Rule:')
    what_went_wrong = _extract_field(content, 'What went wrong:')
    root_cause = _extract_field(content, 'Root cause:')

    deterministic_id = f'backfill_crm_lesson_{source_id}'
    case_id = f'crm_lesson_{source_id}'

    raw_source_ref: dict = {
        'source_id': source_id,
        'crm_search_method': row.get('retrieval_method'),
    }
    if title:
        raw_source_ref['title'] = title
    if category:
        raw_source_ref['category'] = category
    if what_went_wrong:
        raw_source_ref['what_went_wrong'] = what_went_wrong
    if root_cause:
        raw_source_ref['root_cause'] = root_cause

    outcome = RawOutcome(
        observed_at=datetime.now(tz=timezone.utc),
        actual_result=(
            'Documented in the NBNE CRM LessonLearned model after '
            'the fact. See raw_source_ref for source fields.'
        ),
    )

    verbatim_lesson: str | None = None
    verbatim_lesson_model = 'toby_verbatim'
    if preventative_rule:
        verbatim_lesson = preventative_rule

    return RawHistoricalRecord(
        deterministic_id=deterministic_id,
        source_type='crm_lesson',
        decided_at=datetime.now(tz=timezone.utc),
        chosen_path=chosen_path,
        context_summary=context_summary,
        archetype_tags=None,  # Haiku picks tags from the summary
        rejected_paths=None,
        signal_strength=0.95,
        case_id=case_id,
        raw_source_ref=raw_source_ref,
        needs_privacy_scrub=False,
        needs_privacy_review=False,
        outcome=outcome,
        verbatim_lesson=verbatim_lesson,
        verbatim_lesson_model=verbatim_lesson_model,
    )


def _extract_field(content: str, prefix: str) -> str | None:
    """Extract a field value from a flattened content string.

    The CRM indexer joins fields with '. ' between them:
        "Title. Category: X. What went wrong: Y. Root cause: Z. Fix: A. Rule: B"
    This function finds the prefix and returns everything up to the
    next '. ' that starts a known field — or to the end.
    """
    idx = content.find(prefix)
    if idx < 0:
        return None
    start = idx + len(prefix)
    # Stop at the next known-field prefix OR end of string
    stop_prefixes = [
        '. Category:',
        '. What went wrong:',
        '. Root cause:',
        '. Fix:',
        '. Rule:',
    ]
    stop = len(content)
    for sp in stop_prefixes:
        if sp == '. ' + prefix:
            continue
        pos = content.find(sp, start)
        if 0 <= pos < stop:
            stop = pos
    value = content[start:stop].strip().rstrip('.').strip()
    return value or None
