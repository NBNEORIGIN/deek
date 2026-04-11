"""
Source 6 — b2b_quotes.

Reads ``scripts/backfill/data/b2b_quotes.yml`` (authored by Toby,
same hard gate as disputes.yml) and yields one ``RawHistoricalRecord``
per phase of each named quote. Most quotes are single-phase — one
quote goes out, one outcome comes back — but the YAML supports multi-
phase cases for long negotiations.

Key invariants from the brief (section 2.6):

- ``signal_strength = 0.8`` on every row.
- ``source_type = 'b2b_quote'`` for all rows.
- ``needs_privacy_scrub = True`` — b2b quotes mention counterparty
  names that go through the regex + Haiku rewrite pass (real
  commercial entities are acceptable in context_summary, but the
  pipeline scrub is still run defensively).
- Lesson generation: Sonnet (not Opus) when the gate passes and no
  verbatim lesson is supplied.
- Dissents (rejected_paths) are the canonical structure — the brief
  calls out the Bakery Barn three-option quote as the reference
  shape. Every rejected alternative becomes a ``module_dissents``
  row via the pipeline's standard dissent-write step.

Optional email enrichment
-------------------------

For each named client in the YAML the source can merge the latest
matching email thread into ``raw_source_ref.email_thread_excerpt``.
This is disabled by default because it requires a DB connection the
source iterator doesn't own. Toggle it via ``enrich_from_emails=True``
in the constructor and pass a ``db_url``.

YAML schema mirrors disputes.yml (section 2.5). Phases are ordered
as decided; the last phase receives the case-level
``lessons_in_your_own_words`` as its verbatim lesson.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml

from .base import HistoricalSource, RawHistoricalRecord, RawOutcome


class B2BQuoteYamlError(ValueError):
    """Raised when b2b_quotes.yml is malformed in a way that should halt the run."""


class B2BQuotesSource:
    """YAML-backed historical source for named B2B quote decisions."""

    name: str = 'b2b_quotes'
    source_type: str = 'b2b_quote'

    def __init__(
        self,
        yaml_path: Path,
        enrich_from_emails: bool = False,
        db_url: str | None = None,
    ):
        self.yaml_path = Path(yaml_path)
        self.enrich_from_emails = enrich_from_emails
        self.db_url = db_url
        if not self.yaml_path.exists():
            raise B2BQuoteYamlError(
                f'b2b_quotes.yml not found at {self.yaml_path}. '
                'This file is authored by Toby — the importer cannot '
                'generate quote histories synthetically.'
            )
        if enrich_from_emails and not db_url:
            raise B2BQuoteYamlError(
                'enrich_from_emails=True requires db_url'
            )

    def iter_records(self) -> Iterator[RawHistoricalRecord]:
        raw = yaml.safe_load(self.yaml_path.read_text(encoding='utf-8'))
        if raw is None:
            return
        if not isinstance(raw, list):
            raise B2BQuoteYamlError(
                f'{self.yaml_path} must be a YAML list of cases, '
                f'got {type(raw).__name__}'
            )

        enrichment_cache: dict[str, dict] = {}
        for case in raw:
            if self.enrich_from_emails:
                key = _case_client_key(case)
                if key and key not in enrichment_cache:
                    enrichment_cache[key] = _fetch_email_enrichment(
                        db_url=self.db_url or '',
                        client_name=key,
                    )
            yield from _iter_case(
                case=case,
                yaml_path=self.yaml_path,
                enrichment_cache=enrichment_cache,
            )


def _case_client_key(case: Any) -> str | None:
    if not isinstance(case, dict):
        return None
    client = case.get('client') or case.get('case_id')
    if isinstance(client, str) and client.strip():
        return client.strip()
    return None


def _iter_case(
    case: Any,
    yaml_path: Path,
    enrichment_cache: dict,
) -> Iterator[RawHistoricalRecord]:
    if not isinstance(case, dict):
        raise B2BQuoteYamlError(
            f'{yaml_path}: every case must be a mapping, got {type(case).__name__}'
        )

    case_id = case.get('case_id')
    if not case_id or not isinstance(case_id, str):
        raise B2BQuoteYamlError(
            f'{yaml_path}: case missing required string field case_id'
        )

    phases = case.get('phases')
    if not isinstance(phases, list) or not phases:
        raise B2BQuoteYamlError(
            f"{yaml_path}: case '{case_id}' must have a non-empty phases list"
        )

    case_level_lesson = case.get('lessons_in_your_own_words')
    if case_level_lesson is not None and not isinstance(case_level_lesson, str):
        raise B2BQuoteYamlError(
            f"{yaml_path}: case '{case_id}' lessons_in_your_own_words must be a string"
        )
    if isinstance(case_level_lesson, str):
        case_level_lesson = case_level_lesson.strip() or None

    enrichment = enrichment_cache.get(_case_client_key(case) or '')

    last_idx = len(phases) - 1
    for i, phase in enumerate(phases):
        yield _build_phase_record(
            case=case,
            phase=phase,
            case_id=case_id,
            phase_index=i,
            is_last_phase=(i == last_idx),
            case_level_lesson=case_level_lesson if i == last_idx else None,
            enrichment=enrichment,
            yaml_path=yaml_path,
        )


def _build_phase_record(
    case: dict,
    phase: Any,
    case_id: str,
    phase_index: int,
    is_last_phase: bool,
    case_level_lesson: str | None,
    enrichment: dict | None,
    yaml_path: Path,
) -> RawHistoricalRecord:
    if not isinstance(phase, dict):
        raise B2BQuoteYamlError(
            f"{yaml_path}: case '{case_id}' phase {phase_index} must be a mapping"
        )

    phase_name = phase.get('phase') or f'phase_{phase_index}'
    if not isinstance(phase_name, str):
        raise B2BQuoteYamlError(
            f"{yaml_path}: case '{case_id}' phase {phase_index} 'phase' must be a string"
        )

    context_raw = phase.get('context')
    if not isinstance(context_raw, str) or not context_raw.strip():
        raise B2BQuoteYamlError(
            f"{yaml_path}: case '{case_id}' phase '{phase_name}' missing context"
        )
    context = context_raw.strip()

    chosen_path = phase.get('chosen_path')
    if not isinstance(chosen_path, str) or not chosen_path.strip():
        raise B2BQuoteYamlError(
            f"{yaml_path}: case '{case_id}' phase '{phase_name}' missing chosen_path"
        )
    chosen_path = chosen_path.strip()

    decided_at = _coerce_date(
        phase.get('decided_at'),
        where=f"case '{case_id}' phase '{phase_name}'",
        yaml_path=yaml_path,
    )

    rejected_paths = _coerce_rejected(
        phase.get('rejected_alternatives'),
        where=f"case '{case_id}' phase '{phase_name}'",
        yaml_path=yaml_path,
    )

    outcome_text = phase.get('outcome')
    score = phase.get('chosen_path_score')
    metrics = phase.get('metrics')
    if metrics is not None and not isinstance(metrics, dict):
        raise B2BQuoteYamlError(
            f"{yaml_path}: case '{case_id}' phase '{phase_name}' metrics must be a mapping"
        )

    outcome: RawOutcome | None = None
    if isinstance(outcome_text, str) and outcome_text.strip():
        outcome = RawOutcome(
            observed_at=decided_at,
            actual_result=outcome_text.strip(),
            chosen_path_score=float(score) if score is not None else None,
            metrics=metrics,
        )

    verbatim_lesson = case_level_lesson if is_last_phase else None

    deterministic_id = f'backfill_b2b_{case_id}_{phase_name}'

    raw_source_ref: dict = {
        'yaml_path': str(yaml_path),
        'case_id': case_id,
        'phase': phase_name,
        'phase_index': phase_index,
    }
    client = case.get('client')
    if isinstance(client, str) and client.strip():
        raw_source_ref['client'] = client.strip()
    quote_value = case.get('quote_value_gbp')
    if quote_value is not None:
        raw_source_ref['quote_value_gbp'] = quote_value
    if enrichment:
        raw_source_ref['email_enrichment'] = enrichment

    return RawHistoricalRecord(
        deterministic_id=deterministic_id,
        source_type='b2b_quote',
        decided_at=decided_at,
        chosen_path=chosen_path,
        context_summary=context,
        archetype_tags=None,  # Haiku picks the tags
        rejected_paths=rejected_paths,
        signal_strength=0.8,
        case_id=case_id,
        raw_source_ref=raw_source_ref,
        # B2B quotes mention counterparty names — defensively scrub.
        needs_privacy_scrub=True,
        needs_privacy_review=False,
        outcome=outcome,
        verbatim_lesson=verbatim_lesson,
        verbatim_lesson_model='toby_verbatim',
    )


def _coerce_date(raw: Any, where: str, yaml_path: Path) -> datetime:
    if raw is None:
        raise B2BQuoteYamlError(
            f'{yaml_path}: {where} missing decided_at'
        )
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    import datetime as _dt
    if isinstance(raw, _dt.date):
        return datetime.combine(raw, time(0, 0), tzinfo=timezone.utc)
    if isinstance(raw, str):
        cleaned = raw.strip()
        try:
            parsed = datetime.fromisoformat(cleaned)
        except ValueError as exc:
            raise B2BQuoteYamlError(
                f"{yaml_path}: {where} decided_at '{raw}' is not a valid ISO date"
            ) from exc
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise B2BQuoteYamlError(
        f'{yaml_path}: {where} decided_at must be a date or ISO string'
    )


def _coerce_rejected(raw: Any, where: str, yaml_path: Path) -> list[dict] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise B2BQuoteYamlError(
            f'{yaml_path}: {where} rejected_alternatives must be a list'
        )
    out: list[dict] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise B2BQuoteYamlError(
                f'{yaml_path}: {where} rejected_alternatives[{i}] must be a mapping'
            )
        path = item.get('path')
        if not isinstance(path, str) or not path.strip():
            raise B2BQuoteYamlError(
                f'{yaml_path}: {where} rejected_alternatives[{i}].path is required'
            )
        entry: dict = {'path': path.strip()}
        reason = item.get('reason')
        if isinstance(reason, str) and reason.strip():
            entry['reason'] = reason.strip()
        out.append(entry)
    return out or None


def _fetch_email_enrichment(db_url: str, client_name: str) -> dict:
    """Fetch the most relevant email thread for a client name.

    Best-effort: returns a short excerpt + msg_id for attachment to
    ``raw_source_ref.email_enrichment``. Silently returns an empty
    dict if the DB is unreachable or no matches are found.
    """
    if not db_url or not client_name:
        return {}
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=5)
    except Exception:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT message_id, subject, LEFT(body_text, 600), received_at
                   FROM cairn_email_raw
                   WHERE (subject ILIKE %s OR body_text ILIKE %s)
                     AND body_text IS NOT NULL
                   ORDER BY received_at DESC
                   LIMIT 1""",
                (f'%{client_name}%', f'%{client_name}%'),
            )
            row = cur.fetchone()
    except Exception:
        return {}
    finally:
        conn.close()
    if not row:
        return {}
    msg_id, subject, body_excerpt, received_at = row
    return {
        'message_id': msg_id,
        'subject': subject,
        'excerpt': body_excerpt,
        'received_at': received_at.isoformat() if received_at else None,
    }
