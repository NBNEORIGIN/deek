"""Tests for core.brief.telegram_delivery."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from core.brief.telegram_delivery import (
    _chunk_for_telegram,
    _compact_prompt,
    find_pending_telegram_brief,
    render_brief_for_telegram,
    send_brief_via_telegram,
)


@dataclass
class _Q:
    category: str
    prompt: str


# ── Renderer ────────────────────────────────────────────────────────

class TestRenderBrief:
    def _now(self):
        return datetime(2026, 4, 24, 7, 30, tzinfo=timezone.utc)

    def test_includes_display_name_greeting(self):
        out = render_brief_for_telegram(
            display_name='Toby', generated_at=self._now(),
            questions=[_Q('belief_audit', 'Test prompt?\nReply: TRUE/FALSE')],
        )
        assert 'Hi Toby,' in out

    def test_fallback_greeting_when_no_display_name(self):
        out = render_brief_for_telegram(
            display_name='', generated_at=self._now(),
            questions=[],
        )
        assert 'Morning —' in out

    def test_includes_all_questions_numbered(self):
        questions = [
            _Q('belief_audit', 'Q1 prompt'),
            _Q('salience_calibration', 'Q2 prompt'),
            _Q('open_ended', 'Q3 prompt'),
        ]
        out = render_brief_for_telegram(
            display_name='Toby', generated_at=self._now(),
            questions=questions,
        )
        assert '1️⃣' in out
        assert '2️⃣' in out
        assert '3️⃣' in out
        assert 'Belief audit' in out
        assert 'Salience check' in out

    def test_header_date(self):
        out = render_brief_for_telegram(
            display_name='Toby', generated_at=self._now(),
            questions=[],
        )
        assert '2026-04-24' in out

    def test_drafted_briefs_section(self):
        out = render_brief_for_telegram(
            display_name='Toby', generated_at=self._now(),
            questions=[],
            drafted_briefs=[
                {'brief_path': 'briefs/research-2404.abc-foo.md',
                 'title': 'Foo paper'}
            ],
        )
        assert 'Research briefs ready' in out
        assert 'briefs/research-2404.abc-foo.md' in out


class TestCompactPrompt:
    def test_drops_reply_format_line(self):
        prompt = (
            'BELIEF AUDIT — 2 days old, used 0 times\n'
            '\n'
            'I currently believe: X\n'
            '\n'
            'Is this still true?\n'
            'Reply: TRUE / FALSE / [correction]'
        )
        out = _compact_prompt(prompt)
        assert 'TRUE / FALSE' not in out
        assert 'BELIEF AUDIT' not in out
        assert 'Is this still true?' in out

    def test_empty(self):
        assert _compact_prompt('') == ''
        assert _compact_prompt(None) == ''


# ── Chunking ───────────────────────────────────────────────────────

class TestChunkForTelegram:
    def test_short_returns_single(self):
        assert _chunk_for_telegram('hello') == ['hello']

    def test_empty(self):
        assert _chunk_for_telegram('') == []

    def test_splits_long(self):
        body = 'A' * 5000
        chunks = _chunk_for_telegram(body)
        assert len(chunks) == 2
        assert all(len(c) <= 4096 for c in chunks)


# ── Send path ──────────────────────────────────────────────────────

def _fake_telegram(status: int = 200, message_id: int | None = 42):
    class _R:
        status_code = status
        text = 'ok' if status == 200 else 'err'
        def json(self):
            if status == 200:
                return {'ok': True, 'result': {'message_id': message_id}}
            return {'ok': False, 'description': 'boom'}

    class _C:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k):
            return _R()

    return _C


class _Cur:
    def __init__(self, fetchone_queue=None):
        self.fetchone_queue = list(fetchone_queue or [])
        self.sqls: list[str] = []
        self.params: list[tuple] = []

    def execute(self, sql, params=None):
        self.sqls.append(sql)
        self.params.append(params)

    def fetchone(self):
        return self.fetchone_queue.pop(0) if self.fetchone_queue else None

    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Conn:
    def __init__(self, fetchone_queue=None):
        self.cur = _Cur(fetchone_queue)
    def cursor(self):
        return self.cur
    def commit(self): pass
    def close(self): pass


class TestSendBrief:
    def test_happy(self, monkeypatch):
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'tok')
        conn = _Conn(fetchone_queue=[[12345]])  # chat_id lookup
        with patch('httpx.Client', _fake_telegram(200, 99)):
            r = send_brief_via_telegram(
                conn, user_email='toby@x', text='Hello brief',
            )
        assert r.ok is True
        assert r.chat_id == 12345
        assert r.message_ids == [99]

    def test_missing_token(self, monkeypatch):
        monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
        conn = _Conn()
        r = send_brief_via_telegram(conn, user_email='t@x', text='x')
        assert r.ok is False
        assert 'TOKEN' in r.error

    def test_unknown_user(self, monkeypatch):
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'tok')
        conn = _Conn(fetchone_queue=[None])  # lookup miss
        r = send_brief_via_telegram(conn, user_email='ghost@x', text='x')
        assert r.ok is False
        assert 'no registered chat_id' in r.error

    def test_telegram_api_error(self, monkeypatch):
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'tok')
        conn = _Conn(fetchone_queue=[[12345]])
        with patch('httpx.Client', _fake_telegram(500)):
            r = send_brief_via_telegram(
                conn, user_email='t@x', text='hi',
            )
        assert r.ok is False
        assert 'HTTP 500' in r.error

    def test_multi_chunk_body_sends_all(self, monkeypatch):
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'tok')
        conn = _Conn(fetchone_queue=[[12345]])
        body = 'x' * 9000
        with patch('httpx.Client', _fake_telegram(200, 77)):
            r = send_brief_via_telegram(
                conn, user_email='t@x', text=body,
            )
        assert r.ok is True
        assert len(r.message_ids) >= 3


# ── Pending lookup ──────────────────────────────────────────────────

class TestFindPendingBrief:
    def test_hit(self):
        conn = _Conn(fetchone_queue=[[
            'run-abc-123',
            [{'category': 'belief_audit', 'prompt': 'x'}],
        ]])
        out = find_pending_telegram_brief(conn, 'toby@x')
        assert out is not None
        assert out['run_id'] == 'run-abc-123'
        assert len(out['questions']) == 1

    def test_miss(self):
        conn = _Conn(fetchone_queue=[None])
        assert find_pending_telegram_brief(conn, 'toby@x') is None

    def test_db_error_returns_none(self):
        class _FailConn:
            def cursor(self):
                class _C:
                    def execute(self, *a, **k):
                        raise RuntimeError('dead')
                    def __enter__(self): return self
                    def __exit__(self, *a): return False
                return _C()
        assert find_pending_telegram_brief(_FailConn(), 't@x') is None
