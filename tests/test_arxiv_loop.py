"""Tests for core.research.arxiv_loop — arXiv research loop.

Stubs the arXiv HTTP API and the Ollama scorer so tests are
deterministic. Verifies:
  - Atom parsing (title/abstract/authors/published/pdf_url/id)
  - applicability JSON parsing (plain, fenced, prose-wrapped, bad)
  - DB helpers (insert, pick_next_candidate, mark_surfaced,
    record_verdict) via stub connection
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from core.research.arxiv_loop import (
    ArxivPaper,
    _parse_applicability_json,
    fetch_recent,
    insert_candidate,
    mark_surfaced,
    pick_next_candidate,
    record_verdict,
    score_applicability,
)


_ATOM_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2404.12345v1</id>
    <title>MemGPT: Teaching LLMs to Manage Their Own Memory</title>
    <summary>We introduce a virtual context manager that pages
    external knowledge into a bounded LLM context window,
    inspired by OS virtual memory.</summary>
    <published>2026-04-19T12:00:00Z</published>
    <author><name>Jane Doe</name></author>
    <author><name>John Smith</name></author>
    <link title="pdf" href="https://arxiv.org/pdf/2404.12345" type="application/pdf"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2404.99999v2</id>
    <title>Unrelated Paper On Sorting Algorithms</title>
    <summary>We propose a new sorting algorithm that runs in O(n log n).</summary>
    <published>2026-04-18T08:00:00Z</published>
    <author><name>Anonymous</name></author>
    <link title="pdf" href="https://arxiv.org/pdf/2404.99999" type="application/pdf"/>
  </entry>
</feed>
"""


def _fake_http(response_text: str, status: int = 200):
    class _R:
        status_code = status
        text = response_text
        def raise_for_status(self):
            if self.status_code != 200:
                raise Exception(f'HTTP {self.status_code}')
        def json(self):
            import json as _j
            try:
                return _j.loads(response_text)
            except Exception:
                return {}

    class _C:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k):
            return _R()
        def post(self, *a, **k):
            return _R()

    return _C


# ── Atom parsing ────────────────────────────────────────────────────

class TestFetchRecent:
    def test_parses_two_entries(self):
        with patch('httpx.Client', _fake_http(_ATOM_SAMPLE)):
            out = fetch_recent('agentic memory')
        assert len(out) == 2
        a = out[0]
        assert a.arxiv_id == '2404.12345'
        assert 'MemGPT' in a.title
        assert 'virtual context manager' in a.abstract.lower()
        assert a.authors == ['Jane Doe', 'John Smith']
        assert a.published_at == date(2026, 4, 19)
        assert a.pdf_url == 'https://arxiv.org/pdf/2404.12345'

    def test_http_failure_returns_empty(self):
        class _Explode:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def get(self, *a, **k):
                raise RuntimeError('network')
        with patch('httpx.Client', _Explode):
            assert fetch_recent('x') == []

    def test_parse_error_returns_empty(self):
        with patch('httpx.Client', _fake_http('not xml')):
            assert fetch_recent('x') == []


# ── Applicability JSON parsing ──────────────────────────────────────

class TestParseApplicabilityJson:
    def test_plain(self):
        assert _parse_applicability_json('{"score": 8.0, "reason": "x"}') == {
            'score': 8.0, 'reason': 'x',
        }

    def test_fenced(self):
        out = _parse_applicability_json('```json\n{"score": 5}\n```')
        assert out == {'score': 5}

    def test_prose_wrapped(self):
        out = _parse_applicability_json(
            'Here is my analysis:\n{"score": 7.5, "reason": "a"}\nDone.'
        )
        assert out == {'score': 7.5, 'reason': 'a'}

    def test_garbage(self):
        assert _parse_applicability_json('no json') is None
        assert _parse_applicability_json('') is None


# ── Applicability scoring ───────────────────────────────────────────

def _paper_fixture() -> ArxivPaper:
    return ArxivPaper(
        arxiv_id='2404.12345',
        title='MemGPT',
        abstract='virtual context manager for llms',
        authors=['X'],
        published_at=date(2026, 4, 19),
        pdf_url='https://arxiv.org/pdf/2404.12345',
    )


class TestScoreApplicability:
    def test_happy(self, monkeypatch):
        monkeypatch.setenv('OLLAMA_BASE_URL', 'http://stub')
        class _R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {'message': {'content': '{"score": 8.5, "reason": "directly applicable"}'}}
        class _C:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, *a, **k):
                return _R()
        with patch('httpx.Client', _C):
            score, reason = score_applicability(_paper_fixture())
        assert score == 8.5
        assert reason == 'directly applicable'

    def test_clamped_to_10(self, monkeypatch):
        monkeypatch.setenv('OLLAMA_BASE_URL', 'http://stub')
        class _R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {'message': {'content': '{"score": 42, "reason": "x"}'}}
        class _C:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, *a, **k):
                return _R()
        with patch('httpx.Client', _C):
            score, _ = score_applicability(_paper_fixture())
        assert score == 10.0

    def test_ollama_failure_returns_none(self, monkeypatch):
        class _Explode:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, *a, **k):
                raise RuntimeError('network')
        with patch('httpx.Client', _Explode):
            score, _ = score_applicability(_paper_fixture())
        assert score is None


# ── DB helpers (stub connection) ────────────────────────────────────

class _FakeCursor:
    def __init__(self, fetchone_rows=None):
        self._rows = list(fetchone_rows or [])
        self.sqls = []
        self.params = []
        self._last_was_insert = False

    def execute(self, sql, params):
        self.sqls.append(sql)
        self.params.append(params)
        self._last_was_insert = 'INSERT' in sql.upper()

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeConn:
    def __init__(self, fetchone_rows=None):
        self.cur = _FakeCursor(fetchone_rows)
        self.committed = 0
        self.rolled_back = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


class TestDbHelpers:
    def test_insert_candidate_happy(self):
        conn = _FakeConn(fetchone_rows=[[42]])
        new_id = insert_candidate(
            conn, _paper_fixture(),
            query='agentic memory', score=8.0, reason='good',
        )
        assert new_id == 42
        assert conn.committed == 1

    def test_insert_conflict_returns_none(self):
        conn = _FakeConn(fetchone_rows=[None])
        new_id = insert_candidate(
            conn, _paper_fixture(),
            query='x', score=5.0, reason='x',
        )
        assert new_id is None

    def test_insert_exception_rolls_back(self):
        class _RaisingConn(_FakeConn):
            def cursor(self):
                class _C:
                    def execute(self, *a, **k):
                        raise RuntimeError('db dead')
                    def __enter__(s): return s
                    def __exit__(s, *a): return False
                return _C()
        conn = _RaisingConn()
        new_id = insert_candidate(
            conn, _paper_fixture(),
            query='x', score=5.0, reason='x',
        )
        assert new_id is None

    def test_pick_next_candidate_returns_dict(self):
        conn = _FakeConn(fetchone_rows=[[
            1, 'abc', 'Title', 'Abstract', 'http://pdf', 8.0, 'because x',
        ]])
        out = pick_next_candidate(conn)
        assert out is not None
        assert out['arxiv_id'] == 'abc'
        assert out['applicability_score'] == 8.0

    def test_pick_next_empty(self):
        conn = _FakeConn(fetchone_rows=[])
        assert pick_next_candidate(conn) is None

    def test_mark_surfaced(self):
        conn = _FakeConn()
        assert mark_surfaced(conn, 42) is True
        assert conn.committed == 1

    def test_record_verdict_valid_values(self):
        for v in ('yes', 'no', 'later'):
            conn = _FakeConn()
            assert record_verdict(conn, 1, v) is True

    def test_record_verdict_invalid(self):
        conn = _FakeConn()
        assert record_verdict(conn, 1, 'maybe') is False
        assert conn.committed == 0
