"""Unit tests for core.dream.filter (Brief 4 Phase A Tasks 3 + 4).

DB-dependent pieces (`filter_and_score`, `_fetch_existing_embeddings`,
etc) are exercised by the live dry-run on Hetzner. These tests cover
the pure gate logic + scoring so the discipline is enforced
regardless of infrastructure state.
"""
from __future__ import annotations

import pytest

from core.dream.filter import (
    grounding_check,
    specificity_check,
    actionability_check,
    duplication_check,
    compute_score,
    _entity_type_diversity,
    _reload_anti_patterns,
    _key_terms,
)


class TestKeyTerms:
    def test_strips_stop_words(self):
        terms = _key_terms('the quick brown lynx jumps over the lazy crows')
        assert 'quick' in terms
        assert 'brown' in terms
        assert 'lynx' in terms  # 4 chars
        assert 'the' not in terms
        assert 'over' in terms
        # 'fox' / 'dog' / 'cat' are 3 chars — filtered by min_len=4

    def test_min_length(self):
        terms = _key_terms('go to the shop for milk')
        assert 'shop' in terms  # 4
        assert 'milk' in terms
        assert 'go' not in terms  # too short
        assert 'to' not in terms

    def test_dedupes(self):
        # 'acm' is 3 chars — filtered by min_len=4. Use a >=4 char
        # repeated term to test dedupe.
        terms = _key_terms('panels Panels PANELS')
        assert terms.count('panels') == 1
        assert len(terms) == 1


# ── Gate 1: Grounding ────────────────────────────────────────────────

class TestGrounding:
    def test_fewer_than_three_sources_rejected(self):
        ok, sig = grounding_check('pattern text', [1, 2], {1: 'x', 2: 'y'})
        assert ok is False
        assert 'sources' in sig['reason']

    def test_cited_id_missing_rejected(self):
        ok, sig = grounding_check(
            'acm panels on shopfronts',
            [1, 2, 99],
            {1: 'acm panels installed', 2: 'shopfront project'},
        )
        assert ok is False
        assert 'unknown memory' in sig['reason']

    def test_low_coverage_rejected(self):
        ok, sig = grounding_check(
            'xenobiology requires philosophical framework analysis',
            [1, 2, 3],
            {1: 'a b c', 2: 'd e f', 3: 'g h i'},
        )
        assert ok is False
        # Coverage should be 0.0 — no terms overlap
        assert sig['term_coverage'] == 0.0

    def test_reasonable_coverage_passes(self):
        ok, sig = grounding_check(
            'shopfront acm installation planning',
            [1, 2, 3],
            {
                1: 'installed acm panels on shopfront yesterday',
                2: 'planning meeting for upcoming shopfront',
                3: 'acm material arrived',
            },
        )
        assert ok is True
        assert sig['term_coverage'] >= 0.3

    def test_exactly_three_sources_meets_floor(self):
        ok, _ = grounding_check(
            'alpha beta gamma pattern',
            [1, 2, 3],
            {1: 'alpha beta', 2: 'beta gamma', 3: 'gamma alpha'},
        )
        assert ok is True


# ── Gate 2: Specificity ──────────────────────────────────────────────

class TestSpecificity:
    def setup_method(self):
        _reload_anti_patterns()

    def test_platitude_rejected(self):
        ok, sig = specificity_check('customers prefer quick turnaround')
        assert ok is False
        assert 'anti-pattern' in sig['reason']

    def test_specific_passes(self):
        ok, _ = specificity_check(
            'Flowers By Julie jobs tend to ship in 14 days'
        )
        assert ok is True

    def test_case_insensitive(self):
        ok, _ = specificity_check('Customers Prefer Stuff')
        assert ok is False

    def test_reduce_costs_rejected(self):
        # Brutally generic business-speak
        ok, _ = specificity_check('we should reduce costs where possible')
        assert ok is False


# ── Gate 3: Actionability ────────────────────────────────────────────

class TestActionability:
    def test_no_cue_rejected(self):
        ok, sig = actionability_check(
            'A pattern exists across these memories about things'
        )
        assert ok is False
        assert sig['reason'] == 'no actionability cue'

    def test_price_cue_passes(self):
        ok, _ = actionability_check('jobs over £5,000 need a site visit')
        assert ok is True

    def test_timeframe_cue_passes(self):
        ok, _ = actionability_check('respond within 2 days')
        assert ok is True

    def test_channel_cue_passes(self):
        ok, _ = actionability_check('email follow-ups work better than phone')
        assert ok is True

    def test_m_number_cue_passes(self):
        ok, _ = actionability_check('M1234 style jobs need earlier quote approval')
        assert ok is True


# ── Gate 4: Duplication ──────────────────────────────────────────────

class TestDuplication:
    def test_no_existing_passes(self):
        ok, _ = duplication_check('new pattern', None, [])
        assert ok is True

    def test_no_embedding_fn_passes(self):
        ok, _ = duplication_check('new pattern', None, [[1.0, 0.0]])
        assert ok is True

    def test_exact_duplicate_rejected(self):
        vec = [1.0, 0.0, 0.0]
        ok, sig = duplication_check(
            'identical pattern',
            embedding_fn=lambda t: vec,
            existing_embeddings=[vec],
        )
        assert ok is False
        assert sig['max_similarity'] == pytest.approx(1.0)

    def test_orthogonal_passes(self):
        ok, sig = duplication_check(
            'novel pattern',
            embedding_fn=lambda t: [1.0, 0.0, 0.0],
            existing_embeddings=[[0.0, 1.0, 0.0]],
        )
        assert ok is True
        assert sig['max_similarity'] == pytest.approx(0.0)


# ── Scoring ──────────────────────────────────────────────────────────

class TestScoring:
    def test_zero_inputs(self):
        s = compute_score(0.0, 0, 0.0, False)
        assert s == 0.0

    def test_perfect_score(self):
        s = compute_score(1.0, 10, 1.0, True)
        assert s == pytest.approx(1.0)

    def test_capped_at_ten_sources(self):
        # more than 10 sources shouldn't exceed the cap
        s1 = compute_score(1.0, 10, 1.0, True)
        s2 = compute_score(1.0, 100, 1.0, True)
        assert s1 == s2

    def test_actionability_worth_0_2(self):
        with_action = compute_score(0.5, 5, 0.5, True)
        without = compute_score(0.5, 5, 0.5, False)
        assert with_action - without == pytest.approx(0.2)


class TestEntityDiversity:
    def test_no_entities(self):
        assert _entity_type_diversity([], {}) == 0.0

    def test_one_type(self):
        assert _entity_type_diversity(
            ['a', 'b'], {'a': 'customer', 'b': 'customer'},
        ) == 0.3

    def test_two_types(self):
        assert _entity_type_diversity(
            ['a', 'b'],
            {'a': 'customer', 'b': 'material'},
        ) == 0.6

    def test_three_types(self):
        assert _entity_type_diversity(
            ['a', 'b', 'c'],
            {'a': 'customer', 'b': 'material', 'c': 'supplier'},
        ) == 1.0

    def test_four_types_still_one(self):
        assert _entity_type_diversity(
            ['a', 'b', 'c', 'd'],
            {'a': 'customer', 'b': 'material', 'c': 'supplier', 'd': 'module'},
        ) == 1.0
