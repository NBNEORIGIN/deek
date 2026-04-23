"""Tests for core.email_ingest.sent_folder.

The IMAP side is mocked — tests pin the integration between the
fetch loop, the dedup check, the insert, and the association
touch. Real IMAP integration is smoke-tested live on Hetzner
after deploy (no IMAP test server available).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestMissingCredentials:
    def test_skips_gracefully(self, monkeypatch):
        """No IMAP_PASSWORD_TOBY → clean exit, status='missing_credentials'."""
        from core.email_ingest.sent_folder import poll_sent_folder
        monkeypatch.delenv('IMAP_PASSWORD_TOBY', raising=False)
        out = poll_sent_folder(mailbox_name='toby')
        assert out['status'] == 'missing_credentials'
        assert out['ingested'] == 0
        assert out['errors'] == 0


class TestFindSentFolder:
    def test_picks_first_that_selects(self):
        from core.email_ingest.sent_folder import (
            _find_sent_folder, SENT_FOLDER_CANDIDATES,
        )
        # Mock IMAP conn that says OK only for 'INBOX.Sent'
        conn = MagicMock()
        def _select(name, readonly=False):
            if name == 'INBOX.Sent':
                return ('OK', [b'data'])
            return ('NO', [b'not found'])
        conn.select = _select
        assert _find_sent_folder(conn) == 'INBOX.Sent'

    def test_returns_none_when_none_match(self):
        from core.email_ingest.sent_folder import _find_sent_folder
        conn = MagicMock()
        conn.select = lambda name, readonly=False: ('NO', [b'not found'])
        assert _find_sent_folder(conn) is None


class TestPollSentFolder:
    def test_happy_path_ingests_new_msg(self, monkeypatch):
        from core.email_ingest.sent_folder import poll_sent_folder

        # Mock IMAP wiring
        fake_imap = MagicMock()

        def fake_connect_imap(mailbox):
            return fake_imap

        monkeypatch.setattr(
            'core.email_ingest.sent_folder.connect_imap',
            fake_connect_imap,
            raising=False,
        )

        # We patch at the named-import site inside the function
        # scope — poll_sent_folder imports these from .imap_client
        # at call time, so patch the imap_client module itself
        import core.email_ingest.imap_client as imap_mod

        monkeypatch.setattr(imap_mod, 'connect_imap',
                            lambda n: fake_imap, raising=False)
        monkeypatch.setattr(imap_mod, 'fetch_all_uids',
                            lambda c, folder='INBOX': [b'1'],
                            raising=False)

        # Mock message
        fake_msg = MagicMock()
        fake_msg.get = lambda k, default='': {
            'Message-ID': '<abc-123@x>',
            'Subject': 'Re: Flowers by Julie',
            'From': 'Toby <toby@nbnesigns.com>',
            'To': 'julie@flowersbyjulie.com',
            'Date': 'Wed, 23 Apr 2026 15:00:00 +0000',
            'In-Reply-To': '<root-msg@client.com>',
            'References': '',
        }.get(k, default)
        fake_msg.is_multipart = lambda: False
        fake_msg.get_content_type = lambda: 'text/plain'
        fake_msg.get_payload = lambda decode=True: b'Hi Julie, quote attached.'
        fake_msg.get_content_charset = lambda: 'utf-8'

        monkeypatch.setattr(imap_mod, 'fetch_message',
                            lambda c, uid: fake_msg, raising=False)

        fake_imap.select = lambda name, readonly=False: ('OK', [b'ok'])
        fake_imap.logout = lambda: None

        # Mock DB + the association lookup
        import psycopg2
        fake_conn = MagicMock()
        fake_cur = MagicMock()
        fake_cur.__enter__ = lambda self: fake_cur
        fake_cur.__exit__ = lambda self, *a: False
        fake_conn.cursor = lambda: fake_cur
        # Dedup query → None (new msg)
        # Insert → returns [42]
        # Association lookup is handled separately via monkeypatch
        fake_cur.fetchone.side_effect = [None, [42]]
        monkeypatch.setattr(
            'core.email_ingest.sent_folder._connect_db',
            lambda: fake_conn,
        )

        # Simulate no existing association so we don't touch
        monkeypatch.setattr(
            'core.triage.thread_association.lookup_project_for_thread',
            lambda c, t: None,
        )

        out = poll_sent_folder(mailbox_name='toby', max_messages=5)
        assert out['status'] == 'ok'
        assert out['folder'] == 'Sent'
        assert out['ingested'] == 1
        assert out['already_seen'] == 0
        assert out['associations_touched'] == 0

    def test_already_ingested_skipped(self, monkeypatch):
        from core.email_ingest.sent_folder import poll_sent_folder
        import core.email_ingest.imap_client as imap_mod

        fake_imap = MagicMock()
        fake_imap.select = lambda name, readonly=False: ('OK', [b'ok'])
        fake_imap.logout = lambda: None

        monkeypatch.setattr(imap_mod, 'connect_imap',
                            lambda n: fake_imap, raising=False)
        monkeypatch.setattr(imap_mod, 'fetch_all_uids',
                            lambda c, folder='INBOX': [b'1'],
                            raising=False)

        fake_msg = MagicMock()
        fake_msg.get = lambda k, default='': {
            'Message-ID': '<already-seen@x>',
        }.get(k, default)
        fake_msg.is_multipart = lambda: False
        fake_msg.get_content_type = lambda: 'text/plain'
        fake_msg.get_payload = lambda decode=True: b'x'
        fake_msg.get_content_charset = lambda: 'utf-8'
        monkeypatch.setattr(imap_mod, 'fetch_message',
                            lambda c, uid: fake_msg, raising=False)

        fake_conn = MagicMock()
        fake_cur = MagicMock()
        fake_cur.__enter__ = lambda self: fake_cur
        fake_cur.__exit__ = lambda self, *a: False
        fake_conn.cursor = lambda: fake_cur
        # Dedup query → row present (already seen)
        fake_cur.fetchone.return_value = (1,)
        monkeypatch.setattr(
            'core.email_ingest.sent_folder._connect_db',
            lambda: fake_conn,
        )

        out = poll_sent_folder(mailbox_name='toby')
        assert out['already_seen'] == 1
        assert out['ingested'] == 0

    def test_no_sent_folder_status(self, monkeypatch):
        from core.email_ingest.sent_folder import poll_sent_folder
        import core.email_ingest.imap_client as imap_mod

        fake_imap = MagicMock()
        fake_imap.select = lambda name, readonly=False: ('NO', [b'nope'])
        fake_imap.logout = lambda: None

        monkeypatch.setattr(imap_mod, 'connect_imap',
                            lambda n: fake_imap, raising=False)

        out = poll_sent_folder(mailbox_name='toby')
        assert out['status'] == 'no_sent_folder'
        assert out['ingested'] == 0
