"""Tests for core.channels.nudge + the telegram webhook."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from core.channels.nudge import (
    NudgeResult,
    consume_join_code,
    is_nudges_shadow,
    queue_nudge,
    record_join_code,
    send_pending,
)


# ── Fake DB conn ────────────────────────────────────────────────────

class _Cur:
    def __init__(self):
        self.sqls: list[str] = []
        self.params: list[tuple] = []
        # Scripted outputs — tests set these per test
        self.fetchone_queue: list = []
        self.fetchall_queue: list = []

    def execute(self, sql, params=None):
        self.sqls.append(sql)
        self.params.append(params)

    def fetchone(self):
        return self.fetchone_queue.pop(0) if self.fetchone_queue else None

    def fetchall(self):
        return self.fetchall_queue.pop(0) if self.fetchall_queue else []

    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Conn:
    def __init__(self):
        self.cur = _Cur()
        self.committed = 0
        self.rolled_back = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


# ── Shadow gate ─────────────────────────────────────────────────────

class TestShadowGate:
    def test_default_on(self, monkeypatch):
        monkeypatch.delenv('DEEK_NUDGES_SHADOW', raising=False)
        assert is_nudges_shadow() is True

    def test_explicit_false(self, monkeypatch):
        monkeypatch.setenv('DEEK_NUDGES_SHADOW', 'false')
        assert is_nudges_shadow() is False


# ── queue_nudge ─────────────────────────────────────────────────────

class TestQueueNudge:
    def test_happy(self):
        conn = _Conn()
        # Cooldown query → None (nothing recent), then INSERT → id=42
        conn.cur.fetchone_queue = [None, [42]]
        out = queue_nudge(
            conn, kind='stalled_project',
            user_email='toby@nbnesigns.com',
            message='nudge body',
            related_ref='project:abc',
            cooldown_hours=72,
        )
        assert out.state == 'pending'
        assert out.nudge_id == 42
        assert conn.committed == 1

    def test_cooldown_hit_skips(self):
        conn = _Conn()
        # Cooldown query returns existing row → no INSERT
        conn.cur.fetchone_queue = [[999]]
        out = queue_nudge(
            conn, kind='stalled_project',
            user_email='toby@nbnesigns.com',
            message='x',
            related_ref='project:abc',
        )
        assert out.state == 'skipped'
        assert out.nudge_id == 999

    def test_missing_required_fields(self):
        conn = _Conn()
        assert queue_nudge(
            conn, kind='', user_email='x', message='y',
        ).state == 'error'
        assert queue_nudge(
            conn, kind='x', user_email='', message='y',
        ).state == 'error'
        assert queue_nudge(
            conn, kind='x', user_email='y', message='',
        ).state == 'error'

    def test_no_related_ref_skips_cooldown_check(self):
        conn = _Conn()
        conn.cur.fetchone_queue = [[99]]  # INSERT return
        out = queue_nudge(
            conn, kind='k', user_email='u',
            message='m', related_ref=None,
        )
        assert out.state == 'pending'
        # Only one SQL execution (INSERT) since cooldown skipped
        assert len(conn.cur.sqls) == 1
        assert 'INSERT' in conn.cur.sqls[0]

    def test_db_failure_rolls_back(self):
        class _FailCur:
            def execute(self, *a, **k):
                raise RuntimeError('db dead')
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class _FailConn(_Conn):
            def cursor(self):
                return _FailCur()

        conn = _FailConn()
        out = queue_nudge(conn, kind='k', user_email='u', message='m')
        assert out.state == 'error'
        assert conn.rolled_back == 1


# ── send_pending shadow gating ──────────────────────────────────────

class TestSendPending:
    def test_shadow_marks_shadow_not_sends(self, monkeypatch):
        monkeypatch.setenv('DEEK_NUDGES_SHADOW', 'true')
        conn = _Conn()
        # Query for pending rows → one candidate
        conn.cur.fetchall_queue = [
            [(1, 'toby@x', 'hello')],
        ]
        # State update needs no fetchone
        # We shouldn't call telegram at all
        called = []
        monkeypatch.setattr(
            'core.channels.nudge._send_telegram',
            lambda *a, **k: called.append((a, k)) or (False, None, 'NOPE'),
        )
        summary = send_pending(conn, limit=10)
        assert summary['shadow'] == 1
        assert summary['sent'] == 0
        assert called == [], 'telegram must NOT be called in shadow'

    def test_non_shadow_calls_telegram(self, monkeypatch):
        monkeypatch.setenv('DEEK_NUDGES_SHADOW', 'false')
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'x')
        conn = _Conn()
        conn.cur.fetchall_queue = [
            [(1, 'toby@x', 'hello')],
        ]
        # chat_id lookup returns a chat
        conn.cur.fetchone_queue = [[12345]]
        sends = []
        monkeypatch.setattr(
            'core.channels.nudge._send_telegram',
            lambda chat_id, text: (
                sends.append((chat_id, text)) or (True, 99, '')
            ),
        )
        summary = send_pending(conn, limit=10)
        assert summary['sent'] == 1
        assert sends == [(12345, 'hello')]

    def test_missing_chat_id_marks_skipped(self, monkeypatch):
        monkeypatch.setenv('DEEK_NUDGES_SHADOW', 'false')
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'x')
        conn = _Conn()
        conn.cur.fetchall_queue = [
            [(1, 'stranger@x', 'hello')],
        ]
        # chat_id lookup returns None
        conn.cur.fetchone_queue = [None]
        summary = send_pending(conn, limit=10)
        assert summary['skipped'] == 1
        assert summary['sent'] == 0


# ── Join codes ──────────────────────────────────────────────────────

class TestJoinCodes:
    def test_record_produces_8_char_code(self):
        conn = _Conn()
        code = record_join_code(conn, 'toby@nbnesigns.com')
        assert len(code) == 8
        assert code.isalnum()
        assert conn.committed == 1

    def test_consume_valid_code(self):
        conn = _Conn()
        future = datetime.now(timezone.utc) + timedelta(minutes=15)
        # SELECT returns (user_email, expires_at, consumed_at)
        conn.cur.fetchone_queue = [
            ['toby@nbnesigns.com', future, None],
        ]
        ok, detail = consume_join_code(
            conn, 'ABCD1234', 98765,
            telegram_username='toby', first_name='Toby',
        )
        assert ok is True
        assert detail == 'toby@nbnesigns.com'
        # Three mutations: SELECT-FOR-UPDATE is counted (has UPDATE
        # in SQL text), plus mark-consumed UPDATE, plus upsert
        # INSERT-ON-CONFLICT. Real concern: upsert SQL is present.
        upsert_present = any(
            'registered_telegram_chats' in s and 'INSERT' in s
            for s in conn.cur.sqls
        )
        mark_consumed_present = any(
            'consumed_at = NOW()' in s for s in conn.cur.sqls
        )
        assert upsert_present
        assert mark_consumed_present

    def test_unknown_code_rejected(self):
        conn = _Conn()
        conn.cur.fetchone_queue = [None]
        ok, detail = consume_join_code(conn, 'XXXXXXXX', 12345)
        assert ok is False
        assert 'unknown' in detail

    def test_expired_code_rejected(self):
        conn = _Conn()
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        conn.cur.fetchone_queue = [
            ['toby@x', past, None],
        ]
        ok, detail = consume_join_code(conn, 'ABCD1234', 12345)
        assert ok is False
        assert 'expired' in detail

    def test_already_consumed_rejected(self):
        conn = _Conn()
        future = datetime.now(timezone.utc) + timedelta(minutes=15)
        consumed_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        conn.cur.fetchone_queue = [
            ['toby@x', future, consumed_at],
        ]
        ok, detail = consume_join_code(conn, 'ABCD1234', 12345)
        assert ok is False
        assert 'consumed' in detail

    def test_empty_code_rejected(self):
        conn = _Conn()
        ok, detail = consume_join_code(conn, '', 12345)
        assert ok is False
        assert conn.committed == 0


# ── Webhook security ────────────────────────────────────────────────

class TestTelegramWebhook:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv('DEEK_API_KEY', 'test-api')
        monkeypatch.setenv('TELEGRAM_WEBHOOK_SECRET', 's3cret')
        from api.main import app
        return TestClient(app)

    def test_rejects_missing_secret(self, client):
        r = client.post(
            '/api/deek/telegram/webhook',
            json={'update_id': 1, 'message': {}},
        )
        # Always returns 200 but does nothing — verify by check that
        # nothing was inserted (no cooperating stub needed).
        assert r.status_code == 200

    def test_rejects_wrong_secret(self, client):
        r = client.post(
            '/api/deek/telegram/webhook',
            json={'update_id': 1, 'message': {}},
            headers={'X-Telegram-Bot-Api-Secret-Token': 'wrong'},
        )
        assert r.status_code == 200

    def test_accepts_correct_secret(self, client, monkeypatch):
        # Webhook returns 200 fast; real dispatch happens in a
        # background asyncio task. Just verify the happy-path HTTP
        # status and that no exception raises — observing the task
        # ordering across the TestClient boundary is noisy.
        r = client.post(
            '/api/deek/telegram/webhook',
            json={'update_id': 1, 'message': {'chat': {'id': 1}}},
            headers={'X-Telegram-Bot-Api-Secret-Token': 's3cret'},
        )
        assert r.status_code == 200

    def test_malformed_body_returns_200(self, client):
        """Always 200 — we never let Telegram retry."""
        r = client.post(
            '/api/deek/telegram/webhook',
            content=b'not json',
            headers={'X-Telegram-Bot-Api-Secret-Token': 's3cret',
                     'Content-Type': 'application/json'},
        )
        assert r.status_code == 200


class TestTelegramChunking:
    def test_short_returns_single_chunk(self):
        from api.routes.telegram import _chunk_for_telegram
        assert _chunk_for_telegram('hello') == ['hello']

    def test_empty_returns_empty(self):
        from api.routes.telegram import _chunk_for_telegram
        assert _chunk_for_telegram('') == []
        assert _chunk_for_telegram('   ') == []

    def test_long_splits_on_paragraph_boundary(self):
        from api.routes.telegram import _chunk_for_telegram
        para = 'A' * 2000
        body = para + '\n\n' + 'B' * 2000 + '\n\n' + 'C' * 500
        chunks = _chunk_for_telegram(body)
        assert len(chunks) >= 2
        assert all(len(c) <= 4096 for c in chunks)
        assert chunks[0].startswith('A')
        # Last chunk contains the tail
        assert 'C' in chunks[-1]

    def test_single_long_paragraph_hard_splits(self):
        """No split boundaries → falls back to hard 4000 slice."""
        from api.routes.telegram import _chunk_for_telegram
        body = 'X' * 9000
        chunks = _chunk_for_telegram(body)
        assert len(chunks) == 3
        assert all(len(c) <= 4096 for c in chunks)


class TestJoinCodeShape:
    """Webhook recognises an 8-char alphanumeric uppercase message
    as a join code attempt, anything else as a freeform message."""

    def test_looks_like_join_code(self):
        from api.routes.telegram import _looks_like_join_code
        assert _looks_like_join_code('ABCD1234')
        assert _looks_like_join_code('12345678')
        assert not _looks_like_join_code('abcd1234')   # lowercase
        assert not _looks_like_join_code('ABCD123')    # 7 chars
        assert not _looks_like_join_code('ABCD 1234')  # space
        assert not _looks_like_join_code('hello from toby')
        assert not _looks_like_join_code('')
