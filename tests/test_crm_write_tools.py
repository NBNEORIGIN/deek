"""Unit tests for Deek's CRM write tools.

HTTP is mocked via patched httpx.Client. Tests pin:
  - validation of required fields + enums
  - auth missing path
  - success response parsing
  - 404/405 graceful handling (Phase C endpoint not yet deployed)
  - no unauthorised HTTP shape leaks in the return string
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core.tools.crm_tools import (
    _mark_crm_actioned,
    _set_crm_project_folder,
    _write_crm_memory,
)


def _fake_client(status: int, body: dict | None = None, *, raw_text: str = ''):
    class _R:
        status_code = status
        text = raw_text or (json.dumps(body) if body else '')
        def json(self):
            return body or {}

    class _C:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k):
            return _R()
        def patch(self, *a, **k):
            return _R()

    return _C


# ── write_crm_memory ─────────────────────────────────────────────────

class TestWriteCrmMemory:
    def test_missing_message(self):
        assert 'message' in _write_crm_memory('.', message='').lower()

    def test_bad_type(self, monkeypatch):
        monkeypatch.setenv('DEEK_API_KEY', 'tok')
        out = _write_crm_memory('.', message='hi', type='rumination')
        assert 'type' in out.lower()

    def test_bad_priority(self, monkeypatch):
        monkeypatch.setenv('DEEK_API_KEY', 'tok')
        out = _write_crm_memory('.', message='hi', priority='urgent')
        assert 'priority' in out.lower()

    def test_no_token(self, monkeypatch):
        for var in ('DEEK_API_KEY', 'CAIRN_API_KEY', 'CLAW_API_KEY'):
            monkeypatch.delenv(var, raising=False)
        out = _write_crm_memory('.', message='hi')
        assert 'DEEK_API_KEY' in out

    def test_happy_path_201(self, monkeypatch):
        monkeypatch.setenv('DEEK_API_KEY', 'tok')
        with patch(
            'httpx.Client',
            _fake_client(201, {'id': 'abc-123'}),
        ):
            out = _write_crm_memory(
                '.', message='Julie prefers callbacks after 3pm',
                type='observation',
            )
        assert 'abc-123' in out
        assert 'observation' in out

    def test_happy_path_with_project_id(self, monkeypatch):
        monkeypatch.setenv('DEEK_API_KEY', 'tok')
        with patch(
            'httpx.Client',
            _fake_client(201, {'id': 'abc'}),
        ):
            out = _write_crm_memory(
                '.', message='x', project_id='proj-1',
            )
        assert 'proj-1' in out

    def test_server_error(self, monkeypatch):
        monkeypatch.setenv('DEEK_API_KEY', 'tok')
        with patch('httpx.Client', _fake_client(500, raw_text='boom')):
            out = _write_crm_memory('.', message='x')
        assert '500' in out
        assert 'boom' in out

    def test_network_error(self, monkeypatch):
        monkeypatch.setenv('DEEK_API_KEY', 'tok')
        class _Explode:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, *a, **k):
                raise RuntimeError('dns fail')
        with patch('httpx.Client', _Explode):
            out = _write_crm_memory('.', message='x')
        assert 'RuntimeError' in out


# ── mark_crm_actioned ────────────────────────────────────────────────

class TestMarkCrmActioned:
    def test_missing_id(self):
        assert 'recommendation_id' in _mark_crm_actioned('.', recommendation_id='').lower()

    def test_no_token(self, monkeypatch):
        for var in ('DEEK_API_KEY', 'CAIRN_API_KEY', 'CLAW_API_KEY'):
            monkeypatch.delenv(var, raising=False)
        assert 'DEEK_API_KEY' in _mark_crm_actioned('.', recommendation_id='x')

    def test_happy(self, monkeypatch):
        monkeypatch.setenv('DEEK_API_KEY', 'tok')
        with patch('httpx.Client', _fake_client(200, {'id': 'x', 'is_actioned': True})):
            out = _mark_crm_actioned('.', recommendation_id='x')
        assert 'actioned' in out.lower()

    def test_404(self, monkeypatch):
        monkeypatch.setenv('DEEK_API_KEY', 'tok')
        with patch('httpx.Client', _fake_client(404)):
            out = _mark_crm_actioned('.', recommendation_id='missing')
        assert 'not found' in out.lower()


# ── set_crm_project_folder ───────────────────────────────────────────

class TestSetCrmProjectFolder:
    def test_missing_args(self):
        assert 'project_id' in _set_crm_project_folder(
            '.', project_id='', folder_path='x',
        ).lower()
        assert 'folder_path' in _set_crm_project_folder(
            '.', project_id='x', folder_path='',
        ).lower()

    def test_no_token(self, monkeypatch):
        for var in ('DEEK_API_KEY', 'CAIRN_API_KEY', 'CLAW_API_KEY'):
            monkeypatch.delenv(var, raising=False)
        out = _set_crm_project_folder(
            '.', project_id='x', folder_path='y',
        )
        assert 'DEEK_API_KEY' in out

    def test_phase_c_not_deployed_404(self, monkeypatch):
        """Graceful degradation: CRM PR not merged yet."""
        monkeypatch.setenv('DEEK_API_KEY', 'tok')
        with patch('httpx.Client', _fake_client(404)):
            out = _set_crm_project_folder(
                '.', project_id='p1', folder_path='D:\\x',
            )
        assert 'not available' in out.lower()
        assert 'Phase C' in out

    def test_405_same_graceful_path(self, monkeypatch):
        monkeypatch.setenv('DEEK_API_KEY', 'tok')
        with patch('httpx.Client', _fake_client(405)):
            out = _set_crm_project_folder(
                '.', project_id='p1', folder_path='D:\\x',
            )
        assert 'not available' in out.lower()

    def test_happy(self, monkeypatch):
        monkeypatch.setenv('DEEK_API_KEY', 'tok')
        with patch(
            'httpx.Client',
            _fake_client(200, {'id': 'p1', 'localFolderPath': 'D:\\x'}),
        ):
            out = _set_crm_project_folder(
                '.', project_id='p1', folder_path='D:\\x',
            )
        assert 'p1' in out
        assert 'D:\\x' in out
