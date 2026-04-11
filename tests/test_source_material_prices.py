"""
Tests for scripts.backfill.sources.material_prices.

Uses the records= hook so no DB hits and no Haiku calls happen.
Each test injects a shaped email + extraction dict and asserts
the record builder produces the expected RawHistoricalRecord.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


def _make_email(
    message_id='msg-test-1',
    mailbox='toby',
    sender='first.fix@example.co.uk',
    subject='Dibond price list Q2 2025',
    body_text='placeholder',
    received_at=None,
):
    return {
        'id': 1,
        'message_id': message_id,
        'mailbox': mailbox,
        'sender': sender,
        'subject': subject,
        'body_text': body_text,
        'received_at': received_at or datetime(2025, 4, 15, 10, 30, tzinfo=timezone.utc),
    }


def test_builds_record_from_valid_extraction():
    from scripts.backfill.sources.material_prices import MaterialPricesSource

    row = {
        'email': _make_email(),
        'extraction': {
            'has_price': True,
            'supplier': 'First Fix',
            'material': '1.5mm Dibond',
            'unit': 'per sheet',
            'price_gbp': 32.50,
            'notes': 'Q2 2025 price list, no change from Q1',
            'confidence': 'high',
        },
    }

    source = MaterialPricesSource(records=[row])
    records = list(source.iter_records())
    assert len(records) == 1

    r = records[0]
    assert r.source_type == 'material_price'
    assert r.deterministic_id.startswith('backfill_material_price_')
    assert 'First Fix' in r.context_summary
    assert '1.5mm Dibond' in r.context_summary
    assert '£32.50' in r.context_summary
    assert 'per sheet' in r.context_summary
    assert r.chosen_path.startswith('Recorded First Fix price benchmark')
    assert r.verbatim_lesson is not None
    assert 'First Fix benchmark' in r.verbatim_lesson
    assert '£32.50' in r.verbatim_lesson
    assert r.verbatim_lesson_model == 'haiku_extraction'
    assert r.archetype_tags == ['pricing', 'operational']
    assert r.signal_strength == 0.9  # confidence=high
    assert r.raw_source_ref['supplier'] == 'First Fix'
    assert r.raw_source_ref['material'] == '1.5mm Dibond'
    assert r.raw_source_ref['price_gbp'] == 32.50
    assert r.raw_source_ref['unit'] == 'per sheet'
    assert r.raw_source_ref['confidence'] == 'high'


def test_drops_record_when_has_price_false():
    from scripts.backfill.sources.material_prices import MaterialPricesSource
    row = {
        'email': _make_email(),
        'extraction': {
            'has_price': False,
            'supplier': '',
            'material': '',
            'price_gbp': None,
            'confidence': 'low',
        },
    }
    source = MaterialPricesSource(records=[row])
    assert list(source.iter_records()) == []


def test_drops_record_when_supplier_missing():
    from scripts.backfill.sources.material_prices import MaterialPricesSource
    row = {
        'email': _make_email(),
        'extraction': {
            'has_price': True,
            'supplier': '',
            'material': '3mm Foamex',
            'unit': 'per sheet',
            'price_gbp': 12.00,
            'confidence': 'medium',
        },
    }
    assert list(MaterialPricesSource(records=[row]).iter_records()) == []


def test_drops_record_when_price_gbp_null():
    from scripts.backfill.sources.material_prices import MaterialPricesSource
    row = {
        'email': _make_email(),
        'extraction': {
            'has_price': True,
            'supplier': 'SignsXpress',
            'material': 'Vinyl wrap',
            'unit': 'per roll',
            'price_gbp': None,
            'confidence': 'high',
        },
    }
    assert list(MaterialPricesSource(records=[row]).iter_records()) == []


def test_confidence_maps_to_signal_strength():
    from scripts.backfill.sources.material_prices import MaterialPricesSource

    def build(confidence):
        row = {
            'email': _make_email(),
            'extraction': {
                'has_price': True,
                'supplier': 'Test Supplier',
                'material': 'Test Material',
                'unit': 'each',
                'price_gbp': 1.0,
                'confidence': confidence,
            },
        }
        return next(iter(MaterialPricesSource(records=[row]).iter_records()))

    assert build('high').signal_strength == 0.9
    assert build('medium').signal_strength == 0.8
    assert build('low').signal_strength == 0.65


def test_record_contains_received_at_in_raw_source_ref():
    from scripts.backfill.sources.material_prices import MaterialPricesSource
    received = datetime(2024, 11, 2, 14, 0, tzinfo=timezone.utc)
    row = {
        'email': _make_email(received_at=received),
        'extraction': {
            'has_price': True,
            'supplier': 'X',
            'material': 'Y',
            'unit': 'each',
            'price_gbp': 5.0,
            'confidence': 'medium',
        },
    }
    r = next(iter(MaterialPricesSource(records=[row]).iter_records()))
    assert r.decided_at == received
    assert '2024-11-02' in r.raw_source_ref['received_at']


def test_deterministic_id_is_hash_of_message_id():
    from scripts.backfill.sources.material_prices import MaterialPricesSource
    import hashlib
    row = {
        'email': _make_email(message_id='unique-msg-abc123'),
        'extraction': {
            'has_price': True,
            'supplier': 'S',
            'material': 'M',
            'unit': 'each',
            'price_gbp': 1.0,
            'confidence': 'high',
        },
    }
    r = next(iter(MaterialPricesSource(records=[row]).iter_records()))
    expected = hashlib.sha256('unique-msg-abc123'.encode('utf-8')).hexdigest()[:16]
    assert r.deterministic_id == f'backfill_material_price_{expected}'


def test_missing_env_raises(monkeypatch):
    from scripts.backfill.sources.material_prices import MaterialPricesSource
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    source = MaterialPricesSource(email_db_url='', anthropic_api_key='')
    with pytest.raises(RuntimeError, match='DATABASE_URL'):
        list(source.iter_records())


def test_parse_json_output_handles_fences():
    from scripts.backfill.sources.material_prices import _parse_json_output
    fenced = '```json\n{"has_price":true,"supplier":"X","material":"Y","price_gbp":10,"confidence":"high"}\n```'
    parsed = _parse_json_output(fenced)
    assert parsed is not None
    assert parsed['has_price'] is True
    assert parsed['supplier'] == 'X'


def test_parse_json_output_returns_none_for_garbage():
    from scripts.backfill.sources.material_prices import _parse_json_output
    assert _parse_json_output('') is None
    assert _parse_json_output('not json') is None


def test_preflight_accepts_material_prices(tmp_path):
    from scripts.backfill.run import preflight, KNOWN_SOURCES
    assert 'material_prices' in KNOWN_SOURCES
    failures = preflight(
        sources=['material_prices'],
        data_dir=tmp_path,
        commit_mode=False,
    )
    assert not any('not yet implemented' in f for f in failures)
