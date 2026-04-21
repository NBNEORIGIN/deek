"""Unit tests for Triage Phase C — CRM project-folder PATCH + fallback.

The Deek-side helper probes a dedicated CRM endpoint first, falls
back gracefully to the Phase B note-body path on 404 / 405 (endpoint
not yet deployed on the CRM repo side).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core.triage.replies import _patch_crm_project_folder


def _fake_client(status: int, body: dict | None = None):
    class _R:
        status_code = status
        text = 'mock body'
        def json(self):
            return body or {}

    class _C:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def patch(self, *a, **k):
            return _R()

    return _C


class TestPatchHelper:
    def test_no_project_id(self):
        out = _patch_crm_project_folder('', 'D:\\path')
        assert out['applied'] is False
        assert 'no-op' in out['note']

    def test_no_path(self):
        out = _patch_crm_project_folder('pid', '')
        assert out['applied'] is False
        assert 'no-op' in out['note']

    def test_whitespace_path(self):
        out = _patch_crm_project_folder('pid', '   ')
        assert out['applied'] is False

    def test_no_token(self, monkeypatch):
        for var in ('DEEK_API_KEY', 'CAIRN_API_KEY', 'CLAW_API_KEY'):
            monkeypatch.delenv(var, raising=False)
        out = _patch_crm_project_folder('pid', 'D:\\some\\path')
        assert out['applied'] is False
        assert 'no auth token' in out['note']

    def test_endpoint_not_available_404(self, monkeypatch):
        """The current production state — CRM brief not yet merged.
        Deek must gracefully report endpoint_available=False so the
        caller falls back to note-body."""
        monkeypatch.setenv('DEEK_API_KEY', 'test-key')
        with patch('httpx.Client', _fake_client(404)):
            out = _patch_crm_project_folder('pid', 'D:\\x')
        assert out['applied'] is False
        assert out['endpoint_available'] is False
        assert '404' in out['note']

    def test_endpoint_not_available_405(self, monkeypatch):
        """Method-not-allowed also treated as endpoint missing."""
        monkeypatch.setenv('DEEK_API_KEY', 'test-key')
        with patch('httpx.Client', _fake_client(405)):
            out = _patch_crm_project_folder('pid', 'D:\\x')
        assert out['applied'] is False
        assert out['endpoint_available'] is False

    def test_success_200(self, monkeypatch):
        """What happens once the CRM brief lands."""
        monkeypatch.setenv('DEEK_API_KEY', 'test-key')
        with patch('httpx.Client', _fake_client(200, {'id': 'pid', 'localFolderPath': 'D:\\x'})):
            out = _patch_crm_project_folder('pid', 'D:\\x')
        assert out['applied'] is True
        assert out['endpoint_available'] is True
        assert 'set on CRM project' in out['note']

    def test_success_204(self, monkeypatch):
        """No-content also counts as applied."""
        monkeypatch.setenv('DEEK_API_KEY', 'test-key')
        with patch('httpx.Client', _fake_client(204)):
            out = _patch_crm_project_folder('pid', 'D:\\x')
        assert out['applied'] is True
        assert out['endpoint_available'] is True

    def test_auth_failure(self, monkeypatch):
        """401 means the endpoint exists but we can't authenticate.
        endpoint_available=True (we reached it), applied=False."""
        monkeypatch.setenv('DEEK_API_KEY', 'bad-key')
        with patch('httpx.Client', _fake_client(401)):
            out = _patch_crm_project_folder('pid', 'D:\\x')
        assert out['applied'] is False
        assert out['endpoint_available'] is True

    def test_server_error(self, monkeypatch):
        """500 — server is up but something is broken CRM-side."""
        monkeypatch.setenv('DEEK_API_KEY', 'test-key')
        with patch('httpx.Client', _fake_client(500)):
            out = _patch_crm_project_folder('pid', 'D:\\x')
        assert out['applied'] is False
        assert out['endpoint_available'] is True
        assert 'HTTP 500' in out['note']

    def test_path_trimmed_and_truncated(self, monkeypatch):
        """Paths with whitespace or over 500 chars should be normalised
        before being sent. We can't easily inspect the sent body in
        these mocks, so this is a smoke check that the call doesn't
        error on absurd input."""
        monkeypatch.setenv('DEEK_API_KEY', 'test-key')
        long_path = '   D:\\' + ('x' * 1000) + '   '
        with patch('httpx.Client', _fake_client(200, {})):
            out = _patch_crm_project_folder('pid', long_path)
        assert out['applied'] is True

    def test_network_error_returns_clean_none(self, monkeypatch):
        """An httpx exception (DNS fail, timeout) must not propagate."""
        monkeypatch.setenv('DEEK_API_KEY', 'test-key')
        class _ExplodingClient:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def patch(self, *a, **k):
                raise RuntimeError('synthetic network failure')
        with patch('httpx.Client', _ExplodingClient):
            out = _patch_crm_project_folder('pid', 'D:\\x')
        assert out['applied'] is False
        assert 'RuntimeError' in out['note']
