"""
Shared fixtures for the CLAW test suite.
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure the project root is on sys.path regardless of how pytest is invoked
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Load test env vars before anything imports from core/api
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("OPENAI_API_KEY", "")
os.environ.setdefault("API_PROVIDER", "claude")
os.environ.setdefault("CLAW_API_KEY", "claw-dev-key-change-in-production")
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres123@localhost:5432/claw")
os.environ.setdefault("CLAW_DATA_DIR", tempfile.mkdtemp(prefix="claw-pytest-data-"))
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
os.environ.setdefault("OLLAMA_MODEL", "qwen2.5-coder:7b")
os.environ.setdefault("CLAW_FORCE_API", "true")
os.environ.setdefault("CLAUDE_MODEL", "claude-sonnet-4-6")
os.environ.setdefault("CLAUDE_OPUS_MODEL", "claude-opus-4-6")

# ── Fake embedding vector ─────────────────────────────────────────────────────
_FAKE_EMBEDDING = [0.1] * 768


@pytest.fixture(scope="session")
def api_key():
    return os.environ["CLAW_API_KEY"]


@pytest.fixture(scope="session")
def auth_headers(api_key):
    return {"X-API-Key": api_key}


@pytest.fixture(autouse=True)
def mock_ollama_in_tests(monkeypatch):
    """
    Mock all Ollama HTTP calls in tests so the suite never hangs waiting
    for a real Ollama response. Tests run fast regardless of Ollama state.

    Patches at two levels:
      1. Class methods — ClawAgent._embed, CodeIndexer.embed/check_embedding_model
      2. httpx.post catch-all — intercepts any Ollama URL before it hits the network
    """
    # Patch agent embedding at class level
    monkeypatch.setattr(
        'core.agent.ClawAgent._embed',
        lambda self, text: _FAKE_EMBEDDING,
    )

    # Patch indexer embedding at class level
    monkeypatch.setattr(
        'core.context.indexer.CodeIndexer.embed',
        lambda self, text: _FAKE_EMBEDDING,
    )

    # Patch indexer model check at class level
    monkeypatch.setattr(
        'core.context.indexer.CodeIndexer.check_embedding_model',
        lambda self: True,
    )

    # Catch-all: patch httpx.post to intercept Ollama URLs
    import httpx
    _original_post = httpx.post

    def _patched_post(url, *args, **kwargs):
        url_str = str(url)
        if '/api/embeddings' in url_str or '/api/embed' in url_str:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {'embedding': _FAKE_EMBEDDING}
            mock_resp.raise_for_status = MagicMock()
            return mock_resp
        if '11434' in url_str:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {}
            mock_resp.raise_for_status = MagicMock()
            return mock_resp
        return _original_post(url, *args, **kwargs)

    monkeypatch.setattr('httpx.post', _patched_post)

    # Also patch httpx.AsyncClient to intercept async Ollama calls
    _OrigAsyncClient = httpx.AsyncClient

    class _MockAsyncClient(_OrigAsyncClient):
        async def get(self, url, *args, **kwargs):
            url_str = str(url)
            if '11434' in url_str:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {'models': []}
                mock_resp.raise_for_status = MagicMock()
                return mock_resp
            return await super().get(url, *args, **kwargs)

        async def post(self, url, *args, **kwargs):
            url_str = str(url)
            if '/api/embeddings' in url_str or '/api/embed' in url_str:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {'embedding': _FAKE_EMBEDDING}
                mock_resp.raise_for_status = MagicMock()
                return mock_resp
            if '11434' in url_str:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {}
                mock_resp.raise_for_status = MagicMock()
                return mock_resp
            return await super().post(url, *args, **kwargs)

    monkeypatch.setattr('httpx.AsyncClient', _MockAsyncClient)
