"""
Tests for ``scripts.backfill.sources.amazon``.

Pure unit tests on the inflection detector + record builder. The
integration test against the real ``ami_business_report_data``
table is guarded by the same orphan-lock issue that affects
claw_code_chunks — skipped when the table is not reachable within
5 seconds.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest


# ── Pure unit tests ────────────────────────────────────────────────────


def _asin_row(
    month_str: str,
    asin: str,
    sessions: int,
    units: int,
    revenue: float,
    title: str = 'Sample Title',
):
    from scripts.backfill.sources.amazon import AsinMonthly
    return AsinMonthly(
        month=datetime.fromisoformat(f'{month_str}-01').replace(tzinfo=timezone.utc),
        asin=asin,
        title=title,
        sessions=sessions,
        units_ordered=units,
        ordered_product_sales=Decimal(str(revenue)),
    )


def test_detects_revenue_doubling():
    from scripts.backfill.sources.amazon import _detect_inflections
    rows = [
        _asin_row('2025-01', 'B01', sessions=500, units=20, revenue=400),
        _asin_row('2025-02', 'B01', sessions=1100, units=50, revenue=1000),
    ]
    inflections = _detect_inflections(rows)
    assert len(inflections) == 1
    inf = inflections[0]
    assert inf.curr.asin == 'B01'
    assert inf.revenue_delta == 600
    assert inf.sessions_delta_pct > 50


def test_ignores_small_deltas():
    from scripts.backfill.sources.amazon import _detect_inflections
    rows = [
        _asin_row('2025-01', 'B02', sessions=500, units=20, revenue=400),
        _asin_row('2025-02', 'B02', sessions=520, units=21, revenue=405),
    ]
    assert _detect_inflections(rows) == []


def test_noise_floor_filters_tiny_absolute_delta():
    """A 100% change on £5 should not land — absolute delta too small."""
    from scripts.backfill.sources.amazon import _detect_inflections
    rows = [
        _asin_row('2025-01', 'B03', sessions=5, units=1, revenue=5),
        _asin_row('2025-02', 'B03', sessions=10, units=2, revenue=10),
    ]
    assert _detect_inflections(rows) == []


def test_multiple_asins_multiple_pairings():
    from scripts.backfill.sources.amazon import _detect_inflections
    rows = [
        _asin_row('2025-01', 'B01', 500, 20, 400),
        _asin_row('2025-02', 'B01', 1100, 50, 1000),
        _asin_row('2025-03', 'B01', 600, 25, 450),  # big drop
        _asin_row('2025-01', 'B02', 200, 10, 200),
        _asin_row('2025-02', 'B02', 210, 10, 210),  # below noise
    ]
    inflections = _detect_inflections(rows)
    assert len(inflections) == 2  # B01 has two inflections, B02 has zero
    assert {i.curr.asin for i in inflections} == {'B01'}


def test_build_record_shape():
    from scripts.backfill.sources.amazon import Inflection, _build_record
    prev = _asin_row('2025-01', 'B01', 500, 20, 400)
    curr = _asin_row('2025-02', 'B01', 1100, 50, 1000)
    inf = Inflection(
        prev=prev,
        curr=curr,
        revenue_delta=600.0,
        sessions_delta_pct=120.0,
        units_delta_pct=150.0,
    )
    record = _build_record(inf, gate_threshold=600.0)
    assert record.deterministic_id == 'backfill_amazon_B01_2025_02'
    assert record.source_type == 'amazon'
    assert record.signal_strength == 0.85
    assert 'B01' in record.context_summary
    assert '£600' in record.context_summary
    assert record.outcome is not None
    # Top-scoring inflection (at gate_threshold) → score ≥ 0.7 so the
    # pipeline lesson gate fires.
    assert record.outcome.chosen_path_score >= 0.7
    assert record.outcome.metrics['asin'] == 'B01'


def test_score_is_negative_for_downward_inflection():
    from scripts.backfill.sources.amazon import Inflection, _build_record
    prev = _asin_row('2025-01', 'B01', 1100, 50, 1000)
    curr = _asin_row('2025-02', 'B01', 500, 20, 400)
    inf = Inflection(
        prev=prev,
        curr=curr,
        revenue_delta=-600.0,
        sessions_delta_pct=-55.0,
        units_delta_pct=-60.0,
    )
    record = _build_record(inf, gate_threshold=600.0)
    assert record.outcome.chosen_path_score < 0
    assert 'down' in record.context_summary


def test_score_below_gate_when_far_from_top():
    from scripts.backfill.sources.amazon import Inflection, _build_record
    prev = _asin_row('2025-01', 'B01', 100, 5, 100)
    curr = _asin_row('2025-02', 'B01', 200, 10, 200)
    inf = Inflection(
        prev=prev,
        curr=curr,
        revenue_delta=100.0,
        sessions_delta_pct=100.0,
        units_delta_pct=100.0,
    )
    # gate_threshold is much larger — this small inflection should
    # score well below 0.7 so it doesn't call Sonnet.
    record = _build_record(inf, gate_threshold=10000.0)
    assert record.outcome.chosen_path_score < 0.7


def test_pct_change_handles_zero_baseline():
    from scripts.backfill.sources.amazon import _pct_change
    assert _pct_change(0, 0) == 0.0
    assert _pct_change(0, 10) != 0.0   # sentinel-large
    assert _pct_change(10, 20) == 100.0
    assert _pct_change(10, 5) == -50.0
