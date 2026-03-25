"""
Shared fixtures for the CLAW test suite.
"""
import os
import sys
from pathlib import Path

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
os.environ.setdefault("CLAW_DATA_DIR", str(ROOT / "data"))
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
os.environ.setdefault("OLLAMA_MODEL", "qwen2.5-coder:7b")
os.environ.setdefault("CLAW_FORCE_API", "true")
os.environ.setdefault("CLAUDE_MODEL", "claude-sonnet-4-6")
os.environ.setdefault("CLAUDE_OPUS_MODEL", "claude-opus-4-6")


@pytest.fixture(scope="session")
def api_key():
    return os.environ["CLAW_API_KEY"]


@pytest.fixture(scope="session")
def auth_headers(api_key):
    return {"X-API-Key": api_key}
