"""
Tests for scripts.backfill.sources.crm_reflection.

Uses the records= hook so no HTTP and no Haiku calls happen —
each test injects an already-reflected row and asserts the
record builder produces the expected RawHistoricalRecord shape.
Also covers the JSON parser's tolerance of Haiku output quirks
and the content-hash dedupe path.
"""
from __future__ import annotations

import pytest


def test_builds_project_record_from_reflection():
    from scripts.backfill.sources.crm_reflection import CrmReflectionSource

    row = {
        'entity_type': 'project',
        'entity_id': 'prj-bakery-001',
        'content_hash': 'abc123def456abcd',
        'raw_metadata': {
            'project_name': 'Bakery front sign',
            'client': 'Bakery Barn',
            'stage': 'QUOTED',
            'value': 2400,
        },
        'reflection': {
            'archetype_tags': ['pricing', 'cooperative'],
            'context_summary': (
                'Independent bakery requested an illuminated shopfront. '
                'Budget was firm, three options presented. Client selected '
                'the middle option.'
            ),
            'chosen_path': 'Led with the middle option at exactly budget ceiling.',
            'preventative_rule': (
                'For small independent clients with firm budgets, anchor '
                'on the middle of three options and design it to land '
                'exactly at the ceiling.'
            ),
            'risk_flags': [],
            'confidence': 'high',
        },
    }

    source = CrmReflectionSource(api_key='test', records=[row])
    records = list(source.iter_records())
    assert len(records) == 1

    r = records[0]
    assert r.source_type == 'crm_reflection'
    assert r.deterministic_id == 'backfill_crm_reflection_project_prj-bakery-001'
    assert r.case_id == 'crm_reflection_project_prj-bakery-001'
    assert r.signal_strength == 0.8  # confidence=high
    assert r.archetype_tags == ['pricing', 'cooperative']
    assert 'middle option' in r.chosen_path
    assert r.verbatim_lesson is not None
    assert 'anchor' in r.verbatim_lesson
    assert r.verbatim_lesson_model == 'haiku_reflection'
    assert r.raw_source_ref['entity_type'] == 'project'
    assert r.raw_source_ref['content_hash'] == 'abc123def456abcd'
    assert r.raw_source_ref['crm_metadata']['stage'] == 'QUOTED'


def test_builds_client_record_with_medium_confidence():
    from scripts.backfill.sources.crm_reflection import CrmReflectionSource

    row = {
        'entity_type': 'client',
        'entity_id': 'cli-real-fitness',
        'content_hash': 'ffffffffffffffff',
        'raw_metadata': {'name': 'Real Fitness'},
        'reflection': {
            'archetype_tags': ['pricing', 'information_asymmetric'],
            'context_summary': (
                'Gym with multiple signage needs and variable budget. '
                'Communication is casual and cashflow-sensitive.'
            ),
            'chosen_path': 'Laddered pricing with CAD mockups offered.',
            'preventative_rule': 'Track cash-constrained engaged clients as warm, not cold.',
            'risk_flags': ['payment_risk', 'scope_vague'],
            'confidence': 'medium',
        },
    }
    source = CrmReflectionSource(api_key='test', records=[row])
    records = list(source.iter_records())
    assert len(records) == 1

    r = records[0]
    assert r.signal_strength == 0.7
    assert set(r.raw_source_ref['risk_flags']) == {'payment_risk', 'scope_vague'}
    assert r.raw_source_ref['confidence'] == 'medium'


def test_drops_records_with_empty_summary():
    from scripts.backfill.sources.crm_reflection import CrmReflectionSource

    row = {
        'entity_type': 'project',
        'entity_id': 'prj-empty',
        'content_hash': 'xxxx',
        'raw_metadata': {},
        'reflection': {
            'archetype_tags': ['one_shot'],
            'context_summary': '',  # empty — should be dropped
            'chosen_path': 'did something',
            'preventative_rule': 'do something',
            'confidence': 'low',
        },
    }
    source = CrmReflectionSource(api_key='test', records=[row])
    assert list(source.iter_records()) == []


def test_invalid_tags_are_filtered_out():
    from scripts.backfill.sources.crm_reflection import CrmReflectionSource

    row = {
        'entity_type': 'project',
        'entity_id': 'prj-bad-tags',
        'content_hash': 'yyyy',
        'raw_metadata': {},
        'reflection': {
            'archetype_tags': ['pricing', 'INVENTED_TAG', 'cooperative', 'nope'],
            'context_summary': 'Some summary here.',
            'chosen_path': 'Did this.',
            'preventative_rule': 'Do that.',
            'confidence': 'high',
        },
    }
    source = CrmReflectionSource(api_key='test', records=[row])
    records = list(source.iter_records())
    assert len(records) == 1
    assert records[0].archetype_tags == ['pricing', 'cooperative']


def test_low_confidence_maps_to_low_signal_strength():
    from scripts.backfill.sources.crm_reflection import CrmReflectionSource

    row = {
        'entity_type': 'client',
        'entity_id': 'cli-minimal',
        'content_hash': 'z',
        'raw_metadata': {},
        'reflection': {
            'archetype_tags': ['one_shot'],
            'context_summary': 'Minimal data available.',
            'chosen_path': 'Nothing yet.',
            'preventative_rule': '',
            'confidence': 'low',
        },
    }
    source = CrmReflectionSource(api_key='test', records=[row])
    r = next(iter(source.iter_records()))
    assert r.signal_strength == 0.55
    assert r.verbatim_lesson is None  # empty rule → no verbatim


def test_empty_preventative_rule_does_not_set_verbatim_lesson():
    from scripts.backfill.sources.crm_reflection import CrmReflectionSource

    row = {
        'entity_type': 'project',
        'entity_id': 'prj-no-rule',
        'content_hash': '1',
        'raw_metadata': {},
        'reflection': {
            'archetype_tags': ['operational'],
            'context_summary': 'Routine sign production completed without notable issue.',
            'chosen_path': 'Produced on schedule.',
            'preventative_rule': '',
            'confidence': 'high',
        },
    }
    source = CrmReflectionSource(api_key='test', records=[row])
    r = next(iter(source.iter_records()))
    assert r.verbatim_lesson is None


def test_parse_json_output_handles_code_fences():
    from scripts.backfill.sources.crm_reflection import _parse_json_output

    fenced = '```json\n{"archetype_tags":["pricing"],"context_summary":"x","chosen_path":"y","preventative_rule":"z","confidence":"high"}\n```'
    parsed = _parse_json_output(fenced)
    assert parsed is not None
    assert parsed['archetype_tags'] == ['pricing']


def test_parse_json_output_extracts_from_surrounding_prose():
    from scripts.backfill.sources.crm_reflection import _parse_json_output

    messy = (
        "Here's the reflection:\n\n"
        '{"archetype_tags":["operational"],"context_summary":"x",'
        '"chosen_path":"y","preventative_rule":"z","confidence":"medium"}\n\n'
        'Hope that helps!'
    )
    parsed = _parse_json_output(messy)
    assert parsed is not None
    assert parsed['confidence'] == 'medium'


def test_parse_json_output_returns_none_for_garbage():
    from scripts.backfill.sources.crm_reflection import _parse_json_output
    assert _parse_json_output('') is None
    assert _parse_json_output('not json at all') is None


def test_sha256_helper_returns_short_hex():
    from scripts.backfill.sources.crm_reflection import _sha256
    h = _sha256('hello world')
    assert len(h) == 16
    assert all(c in '0123456789abcdef' for c in h)
    # Deterministic
    assert _sha256('hello world') == h


def test_missing_api_key_raises(monkeypatch):
    from scripts.backfill.sources.crm_reflection import CrmReflectionSource
    monkeypatch.delenv('CAIRN_API_KEY', raising=False)
    source = CrmReflectionSource(api_key='')
    with pytest.raises(RuntimeError, match='CAIRN_API_KEY'):
        list(source.iter_records())


def test_preflight_accepts_crm_reflection(tmp_path):
    from scripts.backfill.run import preflight, KNOWN_SOURCES
    assert 'crm_reflection' in KNOWN_SOURCES
    failures = preflight(
        sources=['crm_reflection'],
        data_dir=tmp_path,
        commit_mode=False,
    )
    assert not any('not yet implemented' in f for f in failures)
