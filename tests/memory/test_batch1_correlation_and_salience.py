"""Tests for the two Batch-1 fixes:

1. In-Reply-To correlation in find_run_for_reply (brief reply processor
   was misattributing replies when multiple briefs went out same day).

2. salience_signals-based boost in impressions.rerank (the JSONB
   was previously write-only; retriever couldn't see toby_flag).
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from core.brief.replies import find_run_for_reply
from core.memory.impressions import rerank


# ── In-Reply-To correlation ──────────────────────────────────────────

class _FakeCursor:
    def __init__(self, rows: list[list]):
        self._rows = rows
        self.last_sql: str = ''
        self.last_params: tuple | None = None

    def execute(self, sql, params):
        self.last_sql = sql
        self.last_params = params

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, rows: list[list]):
        self._cursor = _FakeCursor(rows)

    def cursor(self):
        return self._cursor


class TestFindRunForReply:
    def test_in_reply_to_match_preferred(self):
        # First cursor.fetchone() returns the match-by-message-id hit.
        conn = _FakeConn([['run-abc', []]])
        result = find_run_for_reply(
            conn, 'toby@x', date(2026, 4, 22),
            in_reply_to='<msg-123@deek.nbnesigns.co.uk>',
        )
        assert result is not None
        assert result[0] == 'run-abc'
        # SQL used outgoing_message_id clause
        assert 'outgoing_message_id' in conn._cursor.last_sql

    def test_falls_back_to_date_when_no_msgid(self):
        # First lookup (msgid) returns nothing, second (date) hits.
        conn = _FakeConn([None, ['run-xyz', []]])
        result = find_run_for_reply(
            conn, 'toby@x', date(2026, 4, 22),
            in_reply_to='<unknown@x>',
        )
        assert result is not None
        assert result[0] == 'run-xyz'

    def test_date_match_when_no_in_reply_to(self):
        """Legacy path — reply with no thread id still correlates."""
        conn = _FakeConn([['run-legacy', []]])
        result = find_run_for_reply(
            conn, 'toby@x', date(2026, 4, 22),
            in_reply_to=None,
        )
        assert result is not None
        assert result[0] == 'run-legacy'
        # Date clause was used, not msgid
        assert 'outgoing_message_id' not in conn._cursor.last_sql

    def test_no_match_returns_none(self):
        conn = _FakeConn([None, None])
        result = find_run_for_reply(
            conn, 'toby@x', date(2026, 4, 22),
            in_reply_to='<x@x>',
        )
        assert result is None


# ── salience_signals rerank boost ────────────────────────────────────

def _candidate(salience: float = 1.0, signals: dict | None = None,
               rrf_score: float = 0.5, file: str = 'x') -> dict:
    return {
        'chunk_id': id(file) % 10000,
        'file': file,
        'chunk_type': 'memory',
        'salience': salience,
        'last_accessed_at': None,
        'access_count': 0,
        'dedupe_key': file,
        'rrf_score': rrf_score,
        'salience_signals': signals or {},
    }


class TestRerankSignalsBoost:
    def test_toby_flag_boosts(self):
        a = _candidate(file='a', rrf_score=0.5, signals={'toby_flag': 1.0})
        b = _candidate(file='b', rrf_score=0.5, signals={})
        out, _ = rerank([a, b], rrf_scores=[0.5, 0.5])
        assert out[0]['file'] == 'a'  # a beats b because toby_flag
        assert out[0]['impressions_debug']['signals_boost'] > 0
        assert out[1]['impressions_debug']['signals_boost'] == 0

    def test_via_triage_reply_boosts(self):
        a = _candidate(
            file='a', rrf_score=0.5,
            signals={'via': 'triage_reply_note'},
        )
        b = _candidate(file='b', rrf_score=0.5, signals={})
        out, _ = rerank([a, b], rrf_scores=[0.5, 0.5])
        assert out[0]['file'] == 'a'

    def test_via_memory_brief_boosts(self):
        a = _candidate(
            file='a', rrf_score=0.5,
            signals={'via': 'memory_brief_reply'},
        )
        b = _candidate(file='b', rrf_score=0.5, signals={})
        out, _ = rerank([a, b], rrf_scores=[0.5, 0.5])
        assert out[0]['file'] == 'a'

    def test_no_signals_no_boost(self):
        a = _candidate(file='a', rrf_score=0.5)
        b = _candidate(file='b', rrf_score=0.4)
        out, _ = rerank([a, b], rrf_scores=[0.5, 0.4])
        # No signals boost means raw RRF ordering wins
        assert out[0]['file'] == 'a'
        assert out[0]['impressions_debug']['signals_boost'] == 0
        assert out[1]['impressions_debug']['signals_boost'] == 0

    def test_toby_flag_clamped_at_1(self):
        """Extreme toby_flag values don't balloon the boost."""
        a = _candidate(file='a', rrf_score=0.1, signals={'toby_flag': 10.0})
        b = _candidate(file='b', rrf_score=0.5)
        out, _ = rerank([a, b], rrf_scores=[0.1, 0.5])
        # 10.0 should still only get the 0.15 boost (clamped to 1.0)
        boost_a = next(
            c['impressions_debug']['signals_boost']
            for c in out if c['file'] == 'a'
        )
        assert boost_a == pytest.approx(0.15)

    def test_empty_candidates_no_crash(self):
        assert rerank([], rrf_scores=[]) == ([], [])

    def test_bad_signals_value_doesnt_crash(self):
        """If the JSONB has corrupt data, reranker still returns."""
        a = _candidate(
            file='a', rrf_score=0.5,
            signals={'toby_flag': 'not a number'},
        )
        out, _ = rerank([a], rrf_scores=[0.5])
        assert len(out) == 1
        assert out[0]['impressions_debug']['signals_boost'] == 0
