"""
Tests for ``scripts.backfill.sources.crm_lessons``.

Uses the ``records=`` test hook on CrmLessonsSource so nothing goes
over HTTP — we inject shaped search results that match what the CRM
``/api/cairn/search?types=kb`` endpoint would return.
"""
from __future__ import annotations

import pytest


# Shape mirrors one result row from the CRM /api/cairn/search endpoint
_SAMPLE_RECORDS = [
    {
        'id': 'embed-uuid-1',
        'source_type': 'kb',
        'source_id': 'lsn-one-shot-walk-ins',
        'content': (
            'Bespoke one-off walk-in jobs often lose money. '
            'Category: QUOTING. '
            'Bespoke one-off walk-in jobs often lose money. '
            'What went wrong: Underestimated design and setup time. '
            'Root cause: Scope creep and unclear spec. '
            'Fix: Declined or re-scoped work. '
            'Rule: Offer defined product range only'
        ),
        'metadata': {'title': 'Bespoke one-off walk-in jobs often lose money', 'category': 'QUOTING'},
        'score': 0.12,
        'retrieval_method': 'hybrid_rrf',
    },
    {
        'id': 'embed-uuid-2',
        'source_type': 'kb',
        'source_id': 'lsn-pricing-missing',
        'content': (
            'Missing price anchor costs margin. '
            'Category: PRICING. '
            'Without a middle-option anchor, clients negotiate against the cheapest. '
            'What went wrong: Quoted one option only. '
            'Root cause: Rushed quote build. '
            'Fix: Always present three tiers.'
        ),
        'metadata': {'title': 'Missing price anchor costs margin', 'category': 'PRICING'},
        'score': 0.09,
        'retrieval_method': 'cosine',
    },
    # Duplicate deterministic_id — should be deduped
    {
        'id': 'embed-uuid-3',
        'source_type': 'kb',
        'source_id': 'lsn-one-shot-walk-ins',
        'content': 'Bespoke one-off walk-in jobs often lose money. Rule: different rule',
        'metadata': {'title': 'Duplicate'},
        'score': 0.02,
        'retrieval_method': 'cosine',
    },
    # Missing source_id — dropped
    {
        'content': 'Some content',
        'metadata': {},
        'score': 0.01,
    },
    # Empty content — dropped
    {
        'source_id': 'lsn-empty',
        'content': '',
        'metadata': {},
    },
]


def test_source_yields_deduped_records():
    from scripts.backfill.sources.crm_lessons import CrmLessonsSource
    source = CrmLessonsSource(
        api_key='test-token',
        records=_SAMPLE_RECORDS,
    )
    records = list(source.iter_records())
    # Three yield candidates (after filtering missing/empty):
    # walk-ins, pricing-anchor, walk-ins-dup
    # Dedupe removes the duplicate → 2
    assert len(records) == 2
    ids = {r.deterministic_id for r in records}
    assert 'backfill_crm_lesson_lsn-one-shot-walk-ins' in ids
    assert 'backfill_crm_lesson_lsn-pricing-missing' in ids


def test_field_extraction_walk_ins():
    from scripts.backfill.sources.crm_lessons import CrmLessonsSource
    source = CrmLessonsSource(
        api_key='test-token',
        records=[_SAMPLE_RECORDS[0]],
    )
    record = next(iter(source.iter_records()))
    assert record.source_type == 'crm_lesson'
    assert record.signal_strength == 0.95
    assert record.case_id == 'crm_lesson_lsn-one-shot-walk-ins'
    # Fix line becomes chosen_path
    assert 'Declined or re-scoped' in record.chosen_path
    # Rule line becomes verbatim_lesson
    assert record.verbatim_lesson == 'Offer defined product range only'
    assert record.verbatim_lesson_model == 'toby_verbatim'
    # What went wrong / root cause stored in raw_source_ref
    assert 'Underestimated design and setup time' in record.raw_source_ref['what_went_wrong']
    assert 'Scope creep and unclear spec' in record.raw_source_ref['root_cause']
    assert record.raw_source_ref['category'] == 'QUOTING'
    assert record.raw_source_ref['title'] == 'Bespoke one-off walk-in jobs often lose money'
    # Outcome is populated (CRM lessons always have one for retrieval)
    assert record.outcome is not None


def test_lesson_without_rule_has_no_verbatim():
    from scripts.backfill.sources.crm_lessons import CrmLessonsSource
    source = CrmLessonsSource(
        api_key='test-token',
        records=[_SAMPLE_RECORDS[1]],
    )
    record = next(iter(source.iter_records()))
    # pricing-missing has Fix but no Rule — verbatim_lesson is None
    # and the pipeline will fall through to Sonnet generation via
    # the should_generate_lesson gate.
    assert record.verbatim_lesson is None
    assert 'present three tiers' in record.chosen_path


def test_missing_api_key_raises(monkeypatch):
    """When no api_key is supplied and env var is unset, fetch should refuse."""
    from scripts.backfill.sources.crm_lessons import CrmLessonsSource
    monkeypatch.delenv('CAIRN_API_KEY', raising=False)
    source = CrmLessonsSource(api_key='')
    with pytest.raises(RuntimeError, match='CAIRN_API_KEY'):
        list(source.iter_records())


def test_deterministic_ids_are_stable():
    from scripts.backfill.sources.crm_lessons import CrmLessonsSource
    source1 = CrmLessonsSource(api_key='x', records=_SAMPLE_RECORDS[:2])
    source2 = CrmLessonsSource(api_key='x', records=_SAMPLE_RECORDS[:2])
    ids1 = [r.deterministic_id for r in source1.iter_records()]
    ids2 = [r.deterministic_id for r in source2.iter_records()]
    assert ids1 == ids2


def test_content_over_1800_chars_is_truncated():
    from scripts.backfill.sources.crm_lessons import CrmLessonsSource
    long_content = 'Title. ' + ('This is a long lesson. ' * 200) + ' Rule: final rule'
    source = CrmLessonsSource(api_key='x', records=[{
        'source_id': 'lsn-long',
        'content': long_content,
        'metadata': {'title': 'Long'},
    }])
    record = next(iter(source.iter_records()))
    # context_summary is bounded
    assert len(record.context_summary) <= 1810  # 1800 + '...'
    assert record.context_summary.endswith('...')


def test_preflight_accepts_crm_lessons(tmp_path):
    from scripts.backfill.run import preflight, KNOWN_SOURCES
    assert 'crm_lessons' in KNOWN_SOURCES
    failures = preflight(
        sources=['crm_lessons'],
        data_dir=tmp_path,
        commit_mode=False,
    )
    # Should NOT have a 'not yet implemented' failure
    assert not any('not yet implemented' in f for f in failures)
