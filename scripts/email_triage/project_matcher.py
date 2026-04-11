"""
Match an incoming email to an existing CRM project.

Uses the CRM's ``/api/cairn/search`` endpoint (live pgvector + BM25
hybrid retrieval) with the email's sender + subject + client name
guess as the search text, filtered to ``types=['project', 'client']``.
If the top match scores above ``MIN_MATCH_SCORE``, returns the
project_id; otherwise returns None.

The match is deliberately conservative — false positives mean the
triage runner misroutes an email to the wrong project, which
confuses the audit trail. Better to return None than to guess wrong.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx


log = logging.getLogger(__name__)


CRM_DEFAULT_BASE_URL = 'https://crm.nbnesigns.co.uk'
CRM_SEARCH_PATH = '/api/cairn/search'
CRM_REQUEST_TIMEOUT = 10.0

# Minimum RRF score from the CRM hybrid search to accept as a match.
# The CRM's scores typically range 0.01-0.10 for decent matches; we
# require higher than 0.025 to filter out weak semantic hits.
MIN_MATCH_SCORE = 0.025


def match_project(
    email: dict,
    classifier_result: dict,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict:
    """Return a dict with keys {project_id, match_score} or empties.

    ``email`` must have sender, subject, body_text.
    ``classifier_result`` comes from classifier.classify_email().

    If the classifier returned ``classification='existing_project_reply'``
    with a ``client_name_guess`` or ``project_hint``, those are used
    as the CRM search query. Otherwise the subject line is used.
    """
    base = (base_url or os.getenv('CRM_BASE_URL') or CRM_DEFAULT_BASE_URL).rstrip('/')
    token = api_key or os.getenv('CAIRN_API_KEY', '').strip()
    if not token:
        return {'project_id': '', 'match_score': 0.0}

    # Build the best query we can from the signals available
    query_parts: list[str] = []
    project_hint = (classifier_result.get('project_hint') or '').strip()
    client_name = (classifier_result.get('client_name_guess') or '').strip()
    subject = (email.get('subject') or '').strip()
    sender = (email.get('sender') or '').strip()

    if project_hint:
        query_parts.append(project_hint)
    if client_name:
        query_parts.append(client_name)
    if subject:
        # Strip "Re:" / "Fw:" prefixes
        cleaned = subject
        for prefix in ('Re:', 'RE:', 'Fw:', 'FW:', 'Fwd:', 'FWD:'):
            cleaned = cleaned.lstrip(prefix).strip()
        query_parts.append(cleaned)
    if sender:
        # Include the sender email local part — often matches
        # clientEmail in Prisma.
        query_parts.append(sender.split('@')[0])

    if not query_parts:
        return {'project_id': '', 'match_score': 0.0}

    query = ' '.join(query_parts)[:300]

    try:
        with httpx.Client(timeout=CRM_REQUEST_TIMEOUT) as client:
            response = client.get(
                f'{base}{CRM_SEARCH_PATH}',
                params={
                    'q': query,
                    'types': 'project,client',
                    'limit': 5,
                },
                headers={'Authorization': f'Bearer {token}'},
            )
    except Exception as exc:
        log.warning('project_matcher: CRM search failed: %s', exc)
        return {'project_id': '', 'match_score': 0.0}

    if response.status_code != 200:
        log.warning(
            'project_matcher: CRM search HTTP %d — %s',
            response.status_code, response.text[:200],
        )
        return {'project_id': '', 'match_score': 0.0}

    try:
        data = response.json()
    except Exception:
        return {'project_id': '', 'match_score': 0.0}

    results = data.get('results') or []
    if not results:
        return {'project_id': '', 'match_score': 0.0}

    # Prefer project rows over client rows — a project ID is what the
    # triage runner actually wants for attaching activity updates.
    project_rows = [r for r in results if r.get('source_type') == 'project']
    target_rows = project_rows or results

    top = target_rows[0]
    score = float(top.get('score', 0.0))
    if score < MIN_MATCH_SCORE:
        return {'project_id': '', 'match_score': score}

    return {
        'project_id': top.get('source_id', ''),
        'match_score': score,
        'project_name': (top.get('metadata') or {}).get('project_name', ''),
    }
