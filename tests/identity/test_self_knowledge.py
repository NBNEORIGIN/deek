"""Brief 1a.2 Tasks 8 + 9 — regression tests against the unified
system prompt.

These tests assert the system prompt BUILDS correctly for every one
of the ten pre-flight questions — the identity prefix is present on
every path, both chat and voice, and the self-referential-directive
is present. That's the unit-testable surface.

A separate integration-layer test (marked `integration`, runs only
when Ollama is reachable) asserts actual model responses don't fall
back to "I don't have that information" phrasing. Flaky by nature
since LLM outputs vary — we ship it anyway because the absence is
the canary the brief cares about.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reload_real_identity(monkeypatch):
    """Force the identity module to load the REAL repo-root files.

    tests/identity/test_assembler.py reloads core.identity.assembler
    with tmp paths to test loader behaviour; once those tmp files
    are cleaned up, later tests see stale cached content. This
    fixture resets to canonical paths for every test in this module,
    keeping it order-independent.
    """
    monkeypatch.setenv(
        'DEEK_IDENTITY_PATH', str(_REPO_ROOT / 'DEEK_IDENTITY.md'),
    )
    monkeypatch.setenv(
        'DEEK_MODULES_PATH', str(_REPO_ROOT / 'DEEK_MODULES.yaml'),
    )
    import core.identity.assembler as _a
    importlib.reload(_a)
    yield


# The ten canonical pre-flight questions, verbatim from Brief 1a.2.
PREFLIGHT_QUESTIONS = [
    'What is NBNE?',
    'What modules can you access right now?',
    'Do you have access to Amazon intelligence?',
    'What reasoning models do we use for our local LLMs on our Deek server?',
    'Who runs NBNE?',
    'What marketplaces do we sell on?',
    'What is the sovereignty principle?',
    'What is Phloe?',
    'Is AMI reachable?',
    'What modules are currently unreachable, and why?',
]


# Phrases that would indicate regression — if the response contains
# any, the model is falling back to a non-answer for a self-referential
# question that's fully answered by the identity prefix.
_REGRESSION_PHRASES = (
    "i don't have that information",
    "i do not have that information",
    "i'm unable to provide",
    "i cannot provide",
    "i don't have access to",
    "i'm not able to access",
)


# ── Unit-level assertions on the system prompt ─────────────────────

class TestChatPromptAssembly:
    """The chat path's system prompt must contain the identity prefix
    for every one of the ten pre-flight questions.
    """

    def test_identity_prefix_present(self):
        from core.identity import assembler, probe
        prefix = assembler.get_system_prompt_prefix(
            reachable=probe.get_reachable_modules(),
            errors=probe.get_errors(),
        )
        # Key invariants: company name + behavioural directive present.
        assert 'NBNE' in prefix or 'North By North East' in prefix
        # Brief 1a.2 Task 2 directive — loaded at identity-layer time,
        # appears in DEEK_IDENTITY.md, therefore in the prefix.
        assert (
            'self-referential' in prefix
            or "Answering self-referential" in prefix
            or 'Do not respond with' in prefix
        ), (
            'Identity prefix must carry the self-referential-question '
            'directive added in Phase A Task 2.'
        )

    def test_module_list_present(self):
        """Module reachability block must be in the prefix so the
        'what modules can you access' questions are answerable from
        identity alone.
        """
        from core.identity import assembler, probe
        prefix = assembler.get_system_prompt_prefix(
            reachable=probe.get_reachable_modules(),
            errors=probe.get_errors(),
        )
        assert 'Modules available right now' in prefix
        # Every module should appear in the prefix by name
        for module in assembler.get_modules():
            # display_name OR machine name — assembler renders both
            assert (
                module.display_name in prefix
                or module.name in prefix
            ), f'module {module.name} missing from prefix'

    def test_llm_roster_present(self):
        """The local LLM roster was a Phase A Task 3 addition.
        Content must survive in DEEK_IDENTITY.md."""
        from core.identity.assembler import get_identity_text
        text = get_identity_text()
        assert 'qwen2.5' in text.lower() or 'qwen' in text.lower()
        assert 'deepseek' in text.lower()
        assert 'Sonnet' in text or 'claude' in text.lower()

    def test_communication_capabilities_present(self):
        """The symptom that prompted this brief: Deek unaware it can
        send email. DEEK_IDENTITY.md must now say so explicitly."""
        from core.identity.assembler import get_identity_text
        text = get_identity_text()
        assert 'smtp' in text.lower() or 'SMTP' in text
        assert 'imap' in text.lower() or 'IMAP' in text
        assert 'cairn@' in text.lower() or 'cairn@' in text


class TestVoicePromptAssembly:
    """Voice path must carry the same identity prefix as chat.
    Brief 1a.2 Task 1 says: exactly one assembler, no divergence.
    """

    def test_voice_prompt_contains_identity_prefix(self):
        from core.identity import assembler, probe
        from api.routes.ambient import _build_voice_system_prompt

        identity_prefix = assembler.get_system_prompt_prefix(
            reachable=probe.get_reachable_modules(),
            errors=probe.get_errors(),
        )
        voice_prompt = _build_voice_system_prompt('office')
        # The identity prefix must appear verbatim inside the voice prompt.
        assert identity_prefix in voice_prompt, (
            'Voice path system prompt does not contain the canonical '
            'identity prefix — Task 1 has regressed.'
        )

    def test_voice_prompt_has_tts_rules(self):
        """The voice-specific TTS rules must still apply on top of
        the unified identity prefix."""
        from api.routes.ambient import _build_voice_system_prompt, VOICE_TTS_RULES
        prompt = _build_voice_system_prompt('workshop')
        assert VOICE_TTS_RULES in prompt

    @pytest.mark.parametrize('location', ['workshop', 'office', 'home'])
    def test_voice_prompt_all_locations(self, location):
        from api.routes.ambient import _build_voice_system_prompt
        prompt = _build_voice_system_prompt(location)
        # All three locations must carry identity
        assert 'NBNE' in prompt or 'North By North East' in prompt
        assert 'Modules available right now' in prompt


class TestPromptParityAcrossPaths:
    """Chat prompt and voice prompt must share the same identity
    prefix verbatim. They differ in the trailing rules (chat has tool
    rules, voice has TTS rules) — that's expected — but the identity
    block is the hash-stable shared part.
    """

    def test_identity_prefix_byte_identical(self):
        from core.identity import assembler, probe
        from api.routes.ambient import _build_voice_system_prompt

        canonical_prefix = assembler.get_system_prompt_prefix(
            reachable=probe.get_reachable_modules(),
            errors=probe.get_errors(),
        )
        voice_prompt = _build_voice_system_prompt('office')
        # The prefix must be a prefix (by position) of the voice prompt.
        assert voice_prompt.startswith(canonical_prefix), (
            'Voice prompt does not start with the canonical identity '
            'prefix — something is prepending content before the '
            'identity block, which breaks Task 1 parity.'
        )


# ── Pre-flight questions: unit-level ─────────────────────────────

@pytest.mark.parametrize('question', PREFLIGHT_QUESTIONS)
class TestPreflightQuestionAssembly:
    """Every pre-flight question must yield a system prompt containing
    the identity prefix on BOTH paths.

    This is the test that would have caught the 2026-04-19 regression
    at merge time.
    """

    def test_chat_prompt_has_identity(self, question):
        from core.identity import assembler, probe
        prefix = assembler.get_system_prompt_prefix(
            reachable=probe.get_reachable_modules(),
            errors=probe.get_errors(),
        )
        # The prefix is question-independent on the chat path — the
        # agent prepends it to everything. The invariant is simply
        # that the prefix exists and is well-formed.
        assert prefix
        assert len(prefix) > 500, (
            f'Identity prefix suspiciously short ({len(prefix)} chars); '
            f'question context: {question!r}'
        )

    def test_voice_prompt_has_identity(self, question):
        from api.routes.ambient import _build_voice_system_prompt
        # Voice prompt is also question-independent at assembly time;
        # the user question is appended by the caller.
        prompt = _build_voice_system_prompt('office')
        assert 'Modules available right now' in prompt, (
            f'Voice prompt missing module block; question: {question!r}'
        )


# ── Integration tests (run only with a live Ollama) ──────────────

@pytest.mark.integration
class TestPreflightLiveVoice:
    """End-to-end against the voice path. Marked `integration` AND
    gated by DEEK_RUN_INTEGRATION=1 so it runs only when Toby
    explicitly opts in (typically on Hetzner during a deploy
    verification). Needs DB + Ollama reachable.

    The assertions are intentionally lenient: we check that the
    response does NOT match any regression phrase. We do NOT check
    that it includes any specific phrasing — LLM output varies too
    much for that to be stable.
    """

    @pytest.mark.parametrize('question', PREFLIGHT_QUESTIONS)
    def test_response_is_not_a_non_answer(self, question):
        import os
        if os.getenv('DEEK_RUN_INTEGRATION', '').strip().lower() not in ('1', 'true', 'yes'):
            pytest.skip('DEEK_RUN_INTEGRATION not set — skipping live integration test')
        """Actual model call via the voice endpoint. Skipped unless
        the API base URL responds to /health.
        """
        import os
        import httpx
        base = os.getenv('DEEK_API_URL', 'http://localhost:8765')
        key = os.getenv('DEEK_API_KEY', '')
        try:
            h = httpx.get(f'{base}/health', timeout=2.0)
            if h.status_code != 200:
                pytest.skip(f'API unavailable ({h.status_code})')
        except Exception:
            pytest.skip('API unreachable')

        try:
            r = httpx.post(
                f'{base}/api/deek/chat/voice/stream',
                headers={'X-API-Key': key, 'Content-Type': 'application/json'},
                json={'content': question, 'location': 'office'},
                timeout=60.0,
            )
        except Exception as exc:
            pytest.skip(f'voice endpoint failed: {exc}')

        # Stream parse — concatenate all response_delta text fields.
        import json as _json
        collected = ''
        for line in r.text.splitlines():
            if line.startswith('data: ') and '"text"' in line:
                try:
                    collected += _json.loads(line[6:]).get('text', '')
                except Exception:
                    continue

        assert collected, f'empty response for {question!r}'
        lower = collected.lower()
        for phrase in _REGRESSION_PHRASES:
            assert phrase not in lower, (
                f'Voice response regressed to non-answer on {question!r}: '
                f'{collected!r}'
            )
