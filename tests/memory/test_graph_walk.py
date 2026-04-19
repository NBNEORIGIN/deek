"""Unit tests for core.memory.graph_walk (Brief 3 Phase B).

DB-dependent pieces are left to the live integration run on Hetzner —
these tests cover the pure logic: config, shadow gating, fusion, score
normalisation.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from core.memory.graph_walk import (
    GraphCandidate, fuse_into, shadow_enabled,
    extract_query_entities,
)


class TestShadowEnabled:
    def test_default_true(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('DEEK_CROSSLINK_SHADOW', None)
            assert shadow_enabled() is True

    def test_explicit_false(self):
        with patch.dict(os.environ, {'DEEK_CROSSLINK_SHADOW': 'false'}):
            assert shadow_enabled() is False

    def test_explicit_true(self):
        with patch.dict(os.environ, {'DEEK_CROSSLINK_SHADOW': 'true'}):
            assert shadow_enabled() is True

    def test_yes_counts_true(self):
        with patch.dict(os.environ, {'DEEK_CROSSLINK_SHADOW': 'yes'}):
            assert shadow_enabled() is True


class TestExtractQueryEntities:
    def test_empty(self):
        assert extract_query_entities('') == []

    def test_m_number_pulled(self):
        pairs = extract_query_entities('show me M1234 history')
        assert ('m_number', 'm1234') in pairs


class TestFuseInto:
    def _cand(self, cid, score, path=None):
        return GraphCandidate(
            chunk_id=cid, graph_score=score,
            path_entities=path or [],
        )

    def test_empty_candidates_is_identity(self):
        existing = [{'chunk_id': 1, 'score': 0.5}]
        out = fuse_into(existing, [])
        assert out == existing

    def test_boosts_matching_chunk(self):
        existing = [{'chunk_id': 1, 'score': 0.5}]
        cands = [self._cand(1, 10.0, path=['customer:julie'])]
        out = fuse_into(existing, cands, weight=0.2)
        assert out[0]['score'] > 0.5
        assert 'graph_boost' in out[0]
        assert out[0]['graph_path'] == ['customer:julie']

    def test_appends_new_graph_memory(self):
        existing = [{'chunk_id': 1, 'score': 0.5}]
        cands = [self._cand(42, 5.0, path=['material:3mm acm'])]
        out = fuse_into(existing, cands, weight=0.1)
        assert len(out) == 2
        appended = out[-1]
        assert appended['chunk_id'] == 42
        assert appended['chunk_type'] == 'memory'
        assert appended['match_quality'] == 'graph'

    def test_impressions_score_used_as_base(self):
        """If impressions_score is present, the boost applies on top."""
        existing = [{'chunk_id': 1, 'score': 0.2, 'impressions_score': 0.9}]
        cands = [self._cand(1, 10.0)]
        out = fuse_into(existing, cands, weight=0.2)
        # Base should be the impressions score (0.9), not plain score (0.2)
        assert out[0]['score'] > 0.9

    def test_constant_scores_get_full_boost(self):
        """Equal graph_scores → all candidates are equally signal;
        contrast with impressions rerank where constant = zero. See
        comment in graph_walk.fuse_into."""
        existing = [{'chunk_id': 1, 'score': 0.5}, {'chunk_id': 2, 'score': 0.5}]
        cands = [self._cand(1, 5.0), self._cand(2, 5.0)]
        out = fuse_into(existing, cands, weight=0.2)
        # Both boosts should equal the weight
        for d in out[:2]:
            assert d.get('graph_boost', 0.0) == pytest.approx(0.2)


class TestWalkForQueryGracefulFailure:
    def test_no_db_url(self, monkeypatch):
        monkeypatch.delenv('DATABASE_URL', raising=False)
        from core.memory.graph_walk import walk_for_query
        assert walk_for_query('anything') == []

    def test_empty_query(self):
        from core.memory.graph_walk import walk_for_query
        assert walk_for_query('') == []

    def test_no_entities(self, monkeypatch):
        """A query with no extractable entities returns [] without DB access."""
        monkeypatch.setenv('DATABASE_URL', 'postgres://invalid/invalid')
        from core.memory.graph_walk import walk_for_query
        # Empty body with no canonical names shouldn't even try to connect
        assert walk_for_query('just prose no entities here') == []


class TestLogShadow:
    def test_log_without_candidates(self, tmp_path, monkeypatch):
        log = tmp_path / 'graph_shadow.jsonl'
        monkeypatch.setenv('DEEK_CROSSLINK_SHADOW_LOG', str(log))
        # Need to reimport to pick up new env var
        import importlib
        import core.memory.graph_walk as gw
        importlib.reload(gw)
        gw.log_shadow('test query', old_top=[], graph_candidates=[])
        assert log.exists()

    def test_log_with_candidates(self, tmp_path, monkeypatch):
        log = tmp_path / 'graph_shadow.jsonl'
        monkeypatch.setenv('DEEK_CROSSLINK_SHADOW_LOG', str(log))
        import importlib
        import core.memory.graph_walk as gw
        importlib.reload(gw)
        cand = gw.GraphCandidate(chunk_id=42, graph_score=3.14, path_entities=['x'])
        gw.log_shadow('q', [{'chunk_id': 1}], [cand])
        content = log.read_text(encoding='utf-8')
        import json
        record = json.loads(content.strip())
        assert record['query'] == 'q'
        assert record['graph_top'][0]['chunk_id'] == 42
        assert record['graph_top'][0]['graph_score'] == 3.14
