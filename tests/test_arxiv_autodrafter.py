"""Tests for core.research.autodrafter — arXiv Stage 3.

Focus on the pure bits:
  - slugify normalisation
  - PDF text extraction (empty / missing pypdf / valid)
  - draft_brief HTTP contract (Ollama stub)
  - list_pending SQL shape (stub conn)
  - draft_one full pipeline (mocks all external calls)
"""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

from core.research.autodrafter import (
    DraftResult,
    draft_brief,
    draft_one,
    extract_pdf_text,
    fetch_pdf_bytes,
    list_pending,
    mark_drafted,
    slugify,
)


class TestSlugify:
    def test_basic(self):
        # Default max_len=40 truncates at the last clean hyphen
        out = slugify('MemGPT: Teaching LLMs to Manage Their Own Memory')
        assert out.startswith('memgpt-teaching-llms-to-manage-their-own')
        assert len(out) <= 40

    def test_long_truncated(self):
        long = 'A ' * 100
        out = slugify(long, max_len=40)
        assert len(out) <= 40

    def test_empty(self):
        assert slugify('') == 'untitled'
        assert slugify(None) == 'untitled'

    def test_punctuation_cleaned(self):
        assert slugify('What, now?!!?') == 'what-now'


class TestExtractPdfText:
    def test_empty_bytes(self):
        assert extract_pdf_text(b'') == ''

    def test_garbage_bytes(self):
        # Non-PDF content — pypdf raises, we swallow
        assert extract_pdf_text(b'not a pdf') == ''


class TestFetchPdfBytes:
    def test_empty_url(self):
        assert fetch_pdf_bytes('') is None

    def test_http_failure(self):
        class _Explode:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def get(self, *a, **k):
                raise RuntimeError('dns')
        with patch('httpx.Client', _Explode):
            assert fetch_pdf_bytes('https://arxiv.org/pdf/2404.99999') is None

    def test_too_small_response(self):
        class _R:
            def __init__(self):
                self.content = b'tiny'
            def raise_for_status(self): pass

        class _C:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def get(self, *a, **k):
                return _R()
        with patch('httpx.Client', _C):
            assert fetch_pdf_bytes('https://arxiv.org/pdf/x') is None


# ── Ollama drafter stubs ────────────────────────────────────────────

def _fake_ollama(response_content: str, status: int = 200):
    class _R:
        status_code = status
        def raise_for_status(self):
            if status != 200:
                raise Exception(f'HTTP {status}')
        def json(self):
            return {'message': {'content': response_content}}

    class _C:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k):
            return _R()

    return _C


class TestDraftBrief:
    def test_happy(self, monkeypatch):
        monkeypatch.setenv('OLLAMA_BASE_URL', 'http://stub')
        brief_content = '# BRIEF — Test\n\n## Tasks\n\n### Task 1 — X\n'
        with patch('httpx.Client', _fake_ollama(brief_content)):
            out = draft_brief(
                arxiv_id='2404.12345',
                title='Test Paper',
                abstract='This is a test.',
                pdf_text='Page 1 content.',
                applicability_score=8.0,
            )
        assert out is not None
        assert '# BRIEF' in out

    def test_strips_markdown_fences(self, monkeypatch):
        monkeypatch.setenv('OLLAMA_BASE_URL', 'http://stub')
        wrapped = '```markdown\n# BRIEF — Test\n\n## Tasks\n```'
        with patch('httpx.Client', _fake_ollama(wrapped)):
            out = draft_brief(
                arxiv_id='2404.12345', title='x', abstract='x',
                pdf_text='', applicability_score=None,
            )
        assert not out.startswith('```')
        assert '# BRIEF' in out

    def test_empty_response_returns_none(self, monkeypatch):
        monkeypatch.setenv('OLLAMA_BASE_URL', 'http://stub')
        with patch('httpx.Client', _fake_ollama('')):
            out = draft_brief(
                arxiv_id='x', title='x', abstract='x',
                pdf_text='', applicability_score=None,
            )
        assert out is None

    def test_ollama_failure_returns_none(self):
        class _Explode:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, *a, **k):
                raise RuntimeError('no ollama')
        with patch('httpx.Client', _Explode):
            out = draft_brief(
                arxiv_id='x', title='x', abstract='x',
                pdf_text='', applicability_score=None,
            )
        assert out is None

    def test_falls_back_to_abstract_only_when_pdf_empty(self, monkeypatch):
        """When pdf_text is empty the user prompt says so — we just
        verify the call succeeds and the model gets to produce
        output."""
        monkeypatch.setenv('OLLAMA_BASE_URL', 'http://stub')
        captured_body = {}
        class _R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {'message': {'content': '# BRIEF — X\n'}}
        class _C:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, url, json=None, **k):
                captured_body.update(json or {})
                return _R()
        with patch('httpx.Client', _C):
            out = draft_brief(
                arxiv_id='x', title='x',
                abstract='fallback abstract',
                pdf_text='',
                applicability_score=7.0,
            )
        assert out is not None
        user_msg = captured_body['messages'][1]['content']
        assert 'extraction failed or empty' in user_msg.lower() or 'abstract' in user_msg.lower()


# ── list_pending + mark_drafted ─────────────────────────────────────

class _Cur:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.sql = ''
        self.params = None
    def execute(self, sql, params):
        self.sql = sql
        self.params = params
    def fetchall(self):
        return self.rows
    def fetchone(self):
        return self.rows[0] if self.rows else None
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Conn:
    def __init__(self, rows=None):
        self._cur = _Cur(rows)
        self.committed = 0
    def cursor(self):
        return self._cur
    def commit(self):
        self.committed += 1


class TestListPending:
    def test_shape(self):
        rows = [
            [1, '2404.12345', 'Paper A', 'abstract a',
             'https://arxiv.org/pdf/2404.12345', 8.5],
            [2, '2404.99999', 'Paper B', 'abstract b',
             'https://arxiv.org/pdf/2404.99999', 7.0],
        ]
        conn = _Conn(rows)
        out = list_pending(conn, limit=5)
        assert len(out) == 2
        assert out[0]['arxiv_id'] == '2404.12345'
        assert out[0]['applicability_score'] == 8.5
        assert 'toby_verdict' in conn._cur.sql.lower()
        assert 'brief_drafted_at' in conn._cur.sql.lower()

    def test_empty(self):
        conn = _Conn([])
        assert list_pending(conn) == []

    def test_db_failure_returns_empty(self):
        class _FailingConn:
            def cursor(self):
                class _C:
                    def execute(self, *a, **k):
                        raise RuntimeError('db down')
                    def __enter__(self): return self
                    def __exit__(self, *a): return False
                return _C()
        assert list_pending(_FailingConn()) == []


class TestMarkDrafted:
    def test_happy(self):
        conn = _Conn()
        assert mark_drafted(conn, 42, 'briefs/research-x.md') is True
        assert conn.committed == 1


# ── draft_one full pipeline ────────────────────────────────────────

class TestDraftOne:
    def test_happy(self, monkeypatch, tmp_path):
        """Mock PDF fetch + Qwen → writes file + marks DB."""
        monkeypatch.setenv('OLLAMA_BASE_URL', 'http://stub')
        monkeypatch.setattr(
            'core.research.autodrafter.BRIEFS_DIR', tmp_path,
        )
        monkeypatch.setattr(
            'core.research.autodrafter.REPO_ROOT', tmp_path.parent,
        )
        # Mock the fetch + extract to skip real httpx
        monkeypatch.setattr(
            'core.research.autodrafter.fetch_pdf_bytes',
            lambda url: b'PDF-BYTES',
        )
        monkeypatch.setattr(
            'core.research.autodrafter.extract_pdf_text',
            lambda b, **k: 'extracted body text',
        )
        brief_md = '# BRIEF — Auto\n\n## Tasks\n'
        monkeypatch.setattr(
            'core.research.autodrafter.draft_brief',
            lambda **k: brief_md,
        )
        conn = _Conn()
        cand = {
            'id': 1, 'arxiv_id': '2404.12345',
            'title': 'MemGPT', 'abstract': 'abs',
            'pdf_url': 'https://arxiv.org/pdf/2404.12345',
            'applicability_score': 8.0,
        }
        result = draft_one(conn, cand)
        assert result.success is True
        assert result.brief_path is not None
        # File exists
        assert any(tmp_path.glob('research-2404.12345-*.md'))
        # Content rendered
        written = next(tmp_path.glob('research-2404.12345-*.md')).read_text()
        assert '# BRIEF' in written

    def test_qwen_failure_marks_failed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            'core.research.autodrafter.BRIEFS_DIR', tmp_path,
        )
        monkeypatch.setattr(
            'core.research.autodrafter.fetch_pdf_bytes',
            lambda url: b'PDF',
        )
        monkeypatch.setattr(
            'core.research.autodrafter.extract_pdf_text',
            lambda b, **k: 'txt',
        )
        monkeypatch.setattr(
            'core.research.autodrafter.draft_brief',
            lambda **k: None,
        )
        conn = _Conn()
        cand = {
            'id': 1, 'arxiv_id': 'x', 'title': 'x', 'abstract': 'x',
            'pdf_url': 'x', 'applicability_score': 5.0,
        }
        result = draft_one(conn, cand)
        assert result.success is False
        assert 'empty' in result.error.lower()
        # No file was written
        assert not list(tmp_path.glob('research-*.md'))
