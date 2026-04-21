"""Unit tests for core.memory.response_audit (Brief 1a.2 Phase B Task 5).

The DB insert path is exercised by the live deploy; these tests cover
the pure logic — non-answer detection + identity-prefix detection.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reload_real_identity(monkeypatch):
    """Reset identity assembler to the canonical repo-root files.
    See the same-named fixture in test_self_knowledge.py for
    rationale."""
    monkeypatch.setenv(
        'DEEK_IDENTITY_PATH', str(_REPO_ROOT / 'DEEK_IDENTITY.md'),
    )
    monkeypatch.setenv(
        'DEEK_MODULES_PATH', str(_REPO_ROOT / 'DEEK_MODULES.yaml'),
    )
    import core.identity.assembler as _a
    importlib.reload(_a)
    yield

from core.memory.response_audit import (
    _detect_non_answer,
    _identity_prefix_in,
    _NON_ANSWER_PATTERNS,
)


class TestNonAnswerDetection:
    def test_empty_returns_false(self):
        assert _detect_non_answer('') == (False, None)
        assert _detect_non_answer(None or '') == (False, None)

    def test_clean_response(self):
        is_na, pat = _detect_non_answer(
            'Yes, I can send emails via the shared SMTP path.'
        )
        assert is_na is False
        assert pat is None

    def test_catches_classic_non_answer(self):
        is_na, pat = _detect_non_answer("I don't have that information.")
        assert is_na is True
        assert pat == "i don't have that information"

    def test_catches_uppercase(self):
        is_na, _ = _detect_non_answer(
            "I DON'T HAVE THAT INFORMATION AVAILABLE."
        )
        assert is_na is True

    def test_catches_embedded(self):
        is_na, pat = _detect_non_answer(
            "Unfortunately, I don't have access to that data yet."
        )
        assert is_na is True
        assert 'access' in pat

    def test_multiple_patterns_first_wins(self):
        text = "I don't know. Also, I cannot provide that."
        is_na, pat = _detect_non_answer(text)
        assert is_na is True
        # "i don't know" appears in the list before "i cannot provide"
        # — first-match-wins semantics.
        assert pat in _NON_ANSWER_PATTERNS

    def test_as_an_ai_is_caught(self):
        # AI-disclaimer preamble — classic non-answer opener
        is_na, _ = _detect_non_answer(
            "As an AI, I cannot predict the future."
        )
        assert is_na is True

    def test_polite_capable_response_passes(self):
        # Containing "I" doesn't trigger — we need a specific pattern
        is_na, _ = _detect_non_answer(
            "I'll get that for you. The CRM pipeline is £59k."
        )
        assert is_na is False


class TestIdentityPrefixDetection:
    def test_empty_prompt(self):
        assert _identity_prefix_in('', 'sha256:abc') is False

    def test_real_prefix_detected(self):
        # Minimal prompt shape matching the assembler's output
        prompt = (
            "# DEEK_IDENTITY.md\n\n"
            "Some content.\n\n"
            "## Modules available right now\n\n"
            "- foo — REACHABLE\n"
        )
        assert _identity_prefix_in(prompt, 'sha256:xxx') is True

    def test_only_one_marker_insufficient(self):
        # Just "# DEEK_IDENTITY.md" without modules block → not
        # identified as the full prefix
        prompt = "# DEEK_IDENTITY.md\n\nSome content but no modules."
        assert _identity_prefix_in(prompt, 'sha256:xxx') is False

        # And vice versa
        prompt2 = "## Modules available right now\n\n- something"
        assert _identity_prefix_in(prompt2, 'sha256:xxx') is False

    def test_long_prompt_head_only(self):
        # The check looks at the first 20000 chars. Markers pushed
        # past that don't count.
        prompt = 'x' * 22000 + '\n# DEEK_IDENTITY.md\n## Modules available right now'
        assert _identity_prefix_in(prompt, 'sha256:xxx') is False

    def test_identity_assembler_output_recognised(self):
        """Live check: the real assembler output must be recognised
        by _identity_prefix_in. This is the contract that ties the
        two modules together."""
        from core.identity import assembler, probe
        prefix = assembler.get_system_prompt_prefix(
            reachable=probe.get_reachable_modules(),
            errors=probe.get_errors(),
        )
        assert _identity_prefix_in(prefix, assembler.get_identity_hash()) is True
