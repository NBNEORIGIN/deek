"""Unit tests for the Memory Brief (Phase A).

DB-dependent code paths (the DB pick functions and the run-insert
path) are exercised by the live dry-run on Hetzner. These tests
cover pure logic: template rendering, fallback behaviour, email
composition, SMTP config detection.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.brief.questions import (
    Question, QuestionSet,
    _build_open_ended, _format_signal_block, _render,
)
from core.brief.composer import (
    compose_email, SMTPNotConfigured, _smtp_cfg,
)


class TestRender:
    def test_missing_template_raises(self):
        with pytest.raises(ValueError):
            _render(None)

    def test_missing_prompt_key_raises(self):
        with pytest.raises(ValueError):
            _render({'reply_format': 'whatever'})

    def test_happy_path(self):
        prompt, reply = _render(
            {'prompt': 'hello {name}', 'reply_format': 'FF'},
            name='world',
        )
        assert prompt == 'hello world'
        assert reply == 'FF'

    def test_missing_format_key_raises(self):
        """If a template references a placeholder the caller didn't
        supply, we want a loud KeyError so the mismatch is diagnosed,
        not a silent empty string.
        """
        with pytest.raises(KeyError):
            _render({'prompt': 'hi {nope}', 'reply_format': ''})


class TestSignalBlock:
    def test_empty(self):
        assert _format_signal_block({}) == '(no signal breakdown)'
        assert _format_signal_block(None) == '(no signal breakdown)'

    def test_all_zero(self):
        signals = {'money': 0.0, 'outcome_weight': 0.0}
        assert _format_signal_block(signals) == '(all zero)'

    def test_renames_noisy_keys(self):
        signals = {'customer_pushback': 0.4, 'outcome_weight': 1.0}
        out = _format_signal_block(signals)
        # Long names get shortened for readability
        assert 'pushback 0.40' in out
        assert 'outcome 1.00' in out
        assert 'customer_pushback' not in out

    def test_skips_zero_signals(self):
        signals = {'money': 0.42, 'novelty': 0.0, 'toby_flag': 1.0}
        out = _format_signal_block(signals)
        assert 'money 0.42' in out
        assert 'toby_flag 1.00' in out
        assert 'novelty' not in out


class TestOpenEnded:
    def test_always_returns_question(self):
        q = _build_open_ended({})
        assert isinstance(q, Question)
        assert q.category == 'open_ended'
        assert 'remembering' in q.prompt.lower() or 'one thing' in q.prompt.lower()

    def test_uses_template_when_available(self):
        templates = {'open_ended': {
            'prompt': 'CUSTOM OPEN — tell me something',
            'reply_format': 'free',
        }}
        q = _build_open_ended(templates)
        assert 'CUSTOM OPEN' in q.prompt
        assert q.reply_format == 'free'

    def test_falls_back_if_template_broken(self):
        # Malformed template should not propagate the exception
        templates = {'open_ended': {'reply_format': 'only'}}
        q = _build_open_ended(templates)
        assert q.prompt  # non-empty fallback
        assert q.category == 'open_ended'


class TestCompose:
    def _q(self, i):
        return Question(
            category=f'cat_{i}',
            prompt=f'question {i} text',
            reply_format='TRUE / FALSE',
            provenance={'schema_id': str(i)},
        )

    def test_subject_carries_date(self):
        email = compose_email(
            'toby@x',
            datetime(2026, 4, 20, 7, 30, tzinfo=timezone.utc),
            [self._q(1)],
        )
        assert '2026-04-20' in email.subject
        assert 'Deek morning brief' in email.subject

    def test_body_has_one_block_per_question(self):
        email = compose_email(
            'toby@x',
            datetime.now(timezone.utc),
            [self._q(1), self._q(2), self._q(3)],
        )
        assert email.body.count('--- Q') == 3
        assert 'question 1 text' in email.body
        assert 'question 3 text' in email.body

    def test_body_embeds_reply_format(self):
        email = compose_email(
            'toby@x',
            datetime.now(timezone.utc),
            [self._q(1)],
        )
        assert 'TRUE / FALSE' in email.body

    def test_notes_appear_when_given(self):
        email = compose_email(
            'toby@x', datetime.now(timezone.utc),
            [self._q(1)],
            notes=['belief_audit: no eligible'],
        )
        assert 'Generator notes' in email.body
        assert 'no eligible' in email.body

    def test_notes_absent_when_none(self):
        email = compose_email(
            'toby@x', datetime.now(timezone.utc),
            [self._q(1)],
        )
        assert 'Generator notes' not in email.body

    def test_singular_when_one_question(self):
        email = compose_email(
            'toby@x', datetime.now(timezone.utc),
            [self._q(1)],
        )
        assert '1 question for you today' in email.body

    def test_plural_when_many(self):
        email = compose_email(
            'toby@x', datetime.now(timezone.utc),
            [self._q(i) for i in range(4)],
        )
        assert '4 questions for you today' in email.body

    def test_reply_to_set(self):
        email = compose_email(
            'toby@x', datetime.now(timezone.utc), [self._q(1)],
        )
        assert 'cairn@' in email.reply_to


class TestSMTPConfig:
    def test_missing_raises(self, monkeypatch):
        for var in ('SMTP_HOST', 'SMTP_USER', 'SMTP_PASS'):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(SMTPNotConfigured):
            _smtp_cfg()

    def test_partial_raises(self, monkeypatch):
        monkeypatch.setenv('SMTP_HOST', 'h')
        monkeypatch.setenv('SMTP_USER', 'u')
        monkeypatch.delenv('SMTP_PASS', raising=False)
        with pytest.raises(SMTPNotConfigured):
            _smtp_cfg()

    def test_happy_path(self, monkeypatch):
        monkeypatch.setenv('SMTP_HOST', 'smtp.example')
        monkeypatch.setenv('SMTP_USER', 'u')
        monkeypatch.setenv('SMTP_PASS', 'p')
        monkeypatch.setenv('SMTP_PORT', '587')
        cfg = _smtp_cfg()
        assert cfg['host'] == 'smtp.example'
        assert cfg['port'] == 587


class TestQuestionSet:
    def test_dataclass_defaults(self):
        qs = QuestionSet(
            user_email='x@y',
            generated_at=datetime.now(timezone.utc),
            questions=[],
        )
        assert qs.notes == []

    def test_provenance_serializable(self):
        """Phase B's parser round-trips via JSONB — every provenance
        dict must be JSON-serializable."""
        import json
        q = Question(
            category='belief_audit', prompt='...', reply_format='...',
            provenance={'schema_id': 'uuid-string', 'access_count': 2},
        )
        # Should not raise
        json.dumps(q.provenance)
