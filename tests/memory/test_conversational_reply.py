"""Unit tests for core.brief.conversational — the conversational
reply normaliser.

LLM call is stubbed via httpx.Client mock; tests pin the JSON
parsing, validation, verdict routing, and shadow-mode gating.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core.brief.conversational import (
    ConversationalQuestion,
    NormalisedAnswer,
    _parse_model_json,
    _validate_answer,
    is_conversational_shadow,
    normalise_conversational_reply,
)


# ── Shadow gate ──────────────────────────────────────────────────────

class TestShadowGate:
    def test_default_on(self, monkeypatch):
        monkeypatch.delenv('DEEK_CONVERSATIONAL_REPLY_SHADOW', raising=False)
        assert is_conversational_shadow() is True

    def test_explicit_false(self, monkeypatch):
        monkeypatch.setenv('DEEK_CONVERSATIONAL_REPLY_SHADOW', 'false')
        assert is_conversational_shadow() is False


# ── JSON parsing ─────────────────────────────────────────────────────

class TestParseModelJson:
    def test_plain_json(self):
        out = _parse_model_json('{"answers": []}')
        assert out == {'answers': []}

    def test_with_markdown_fences(self):
        out = _parse_model_json('```json\n{"answers": [{"q": 1}]}\n```')
        assert out == {'answers': [{'q': 1}]}

    def test_recovers_from_prefix_prose(self):
        out = _parse_model_json(
            'Here is the JSON:\n{"answers": [{"q": 1}]}\nLet me know if...'
        )
        assert out == {'answers': [{'q': 1}]}

    def test_none_on_garbage(self):
        assert _parse_model_json('not json at all') is None
        assert _parse_model_json('') is None


# ── Answer validation ────────────────────────────────────────────────

_BRIEF = frozenset({'affirm', 'deny', 'correct', 'empty'})
_TRIAGE = frozenset({
    'affirm', 'deny', 'select_candidate', 'edit', 'text', 'empty',
})


class TestValidateAnswer:
    def test_happy_brief(self):
        a = _validate_answer(
            {'q_number': 1, 'category': 'belief_audit',
             'verdict': 'affirm'}, _BRIEF,
        )
        assert a is not None
        assert a.q_number == 1
        assert a.verdict == 'affirm'

    def test_rejects_unknown_verdict(self):
        a = _validate_answer(
            {'q_number': 1, 'category': 'x', 'verdict': 'maybe'}, _BRIEF,
        )
        assert a is None

    def test_rejects_bad_q_number(self):
        a = _validate_answer(
            {'q_number': 'foo', 'category': 'x', 'verdict': 'affirm'}, _BRIEF,
        )
        assert a is None

    def test_triage_verdicts_allowed(self):
        a = _validate_answer(
            {'q_number': 1, 'category': 'match_confirm',
             'verdict': 'select_candidate',
             'selected_candidate_index': 2}, _TRIAGE,
        )
        assert a.selected_candidate_index == 2

    def test_out_of_range_candidate_clipped(self):
        a = _validate_answer(
            {'q_number': 1, 'category': 'match_confirm',
             'verdict': 'select_candidate',
             'selected_candidate_index': 7}, _TRIAGE,
        )
        assert a.selected_candidate_index is None

    def test_confidence_clamped(self):
        a = _validate_answer(
            {'q_number': 1, 'category': 'x', 'verdict': 'affirm',
             'confidence': 2.5}, _BRIEF,
        )
        assert a.confidence == 1.0


# ── End-to-end with stubbed Ollama ───────────────────────────────────

def _fake_ollama_response(payload: dict):
    """Return a fake httpx.Client whose .post() returns a mocked
    response with .json() == {'message': {'content': <payload>}}."""
    class _R:
        status_code = 200
        text = ''
        def raise_for_status(self):
            pass
        def json(self):
            return {'message': {'content': json.dumps(payload)}}

    class _C:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k):
            return _R()

    return _C


class TestNormaliseConversationalReply:
    def test_empty_body_returns_none(self):
        qs = [ConversationalQuestion(1, 'x', 'What?')]
        assert normalise_conversational_reply('', qs) is None
        assert normalise_conversational_reply('   ', qs) is None

    def test_no_questions_returns_none(self):
        assert normalise_conversational_reply('body', []) is None

    def test_happy_brief_reply(self):
        payload = {
            'answers': [
                {'q_number': 1, 'category': 'belief_audit',
                 'verdict': 'affirm', 'correction_text': '',
                 'confidence': 0.9},
                {'q_number': 2, 'category': 'salience_calibration',
                 'verdict': 'deny',
                 'correction_text': 'outdated domain',
                 'confidence': 0.85},
                {'q_number': 3, 'category': 'open_ended',
                 'verdict': 'correct',
                 'correction_text': 'QA is important',
                 'confidence': 0.8},
            ]
        }
        qs = [
            ConversationalQuestion(1, 'belief_audit', 'Still true?'),
            ConversationalQuestion(2, 'salience_calibration', 'Important?'),
            ConversationalQuestion(3, 'open_ended', 'Anything?'),
        ]
        with patch('httpx.Client', _fake_ollama_response(payload)):
            out = normalise_conversational_reply(
                'yes to Q1, no to Q2, and QA matters', qs, kind='brief',
            )
        assert out is not None
        assert len(out) == 3
        assert out[0].verdict == 'affirm'
        assert out[1].verdict == 'deny'
        assert 'outdated' in out[1].correction_text
        assert out[2].verdict == 'correct'

    def test_triage_select_candidate(self):
        payload = {
            'answers': [
                {'q_number': 1, 'category': 'match_confirm',
                 'verdict': 'select_candidate',
                 'selected_candidate_index': 2,
                 'correction_text': '', 'confidence': 0.9},
            ]
        }
        qs = [ConversationalQuestion(1, 'match_confirm', 'Which?')]
        with patch('httpx.Client', _fake_ollama_response(payload)):
            out = normalise_conversational_reply(
                "actually it's the second one", qs, kind='triage',
            )
        assert out[0].verdict == 'select_candidate'
        assert out[0].selected_candidate_index == 2

    def test_ollama_unreachable_returns_none(self):
        class _ExplodingClient:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, *a, **k):
                raise RuntimeError('network down')
        qs = [ConversationalQuestion(1, 'x', 'What?')]
        with patch('httpx.Client', _ExplodingClient):
            out = normalise_conversational_reply('something', qs)
        assert out is None

    def test_malformed_model_output_returns_none(self):
        class _R:
            status_code = 200
            text = ''
            def raise_for_status(self):
                pass
            def json(self):
                return {'message': {'content': 'not json at all'}}

        class _C:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, *a, **k):
                return _R()

        qs = [ConversationalQuestion(1, 'x', 'What?')]
        with patch('httpx.Client', _C):
            out = normalise_conversational_reply('something', qs)
        assert out is None

    def test_invalid_verdict_filtered(self):
        """Model returns 3 answers, 1 with invalid verdict — filter drops
        the bad one, keeps the good ones."""
        payload = {
            'answers': [
                {'q_number': 1, 'category': 'x', 'verdict': 'affirm'},
                {'q_number': 2, 'category': 'x', 'verdict': 'INVALID'},
                {'q_number': 3, 'category': 'x', 'verdict': 'deny'},
            ]
        }
        qs = [ConversationalQuestion(1, 'x', 'a')]
        with patch('httpx.Client', _fake_ollama_response(payload)):
            out = normalise_conversational_reply('body', qs, kind='brief')
        assert len(out) == 2
        verdicts = [a.verdict for a in out]
        assert 'INVALID' not in verdicts
