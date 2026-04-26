"""Tests for role-specific brief expansion (Jo HR/finance/D2C,
Ivan production/equipment/tech)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from core.brief.questions import _build_self_prompt
from core.brief.replies import (
    _SELF_PROMPT_CATEGORIES, _is_nothing_answer,
)
from core.brief.user_profile import (
    get_profile, reload_profiles,
)


# ── Profile loading ────────────────────────────────────────────────

class TestProfileExtensions:
    def test_jo_has_question_categories(self, monkeypatch):
        monkeypatch.delenv('DEEK_BRIEF_PROFILES_PATH', raising=False)
        reload_profiles()
        p = get_profile('jo@nbnesigns.com')
        assert p.question_categories is not None
        assert 'hr_pulse' in p.question_categories
        assert 'finance_check' in p.question_categories
        assert 'd2c_observation' in p.question_categories
        assert 'open_ended' in p.question_categories

    def test_ivan_has_question_categories(self, monkeypatch):
        monkeypatch.delenv('DEEK_BRIEF_PROFILES_PATH', raising=False)
        reload_profiles()
        p = get_profile('ivan@nbnesigns.com')
        assert p.question_categories is not None
        assert 'production_quality' in p.question_categories
        assert 'equipment_health' in p.question_categories
        assert 'technical_solve' in p.question_categories
        assert 'open_ended' in p.question_categories

    def test_toby_has_no_override(self, monkeypatch):
        """Tier-1 director keeps the default tier-1 mix."""
        monkeypatch.delenv('DEEK_BRIEF_PROFILES_PATH', raising=False)
        reload_profiles()
        p = get_profile('toby@nbnesigns.com')
        assert p.question_categories is None

    def test_unknown_user_no_override(self, monkeypatch):
        monkeypatch.delenv('DEEK_BRIEF_PROFILES_PATH', raising=False)
        reload_profiles()
        p = get_profile('stranger@x.com')
        assert p.question_categories is None


# ── Self-prompt builder ────────────────────────────────────────────

class TestBuildSelfPrompt:
    def _templates(self):
        return {
            'hr_pulse': {
                'prompt': 'HR / STAFF PULSE — anything from yesterday?',
                'reply_format': "Free text or 'nothing'",
                'role_tag': 'hr',
            },
            'unknown_cat': {},  # no prompt key
        }

    def test_happy_path(self):
        q = _build_self_prompt(self._templates(), 'hr_pulse', 'jo@x')
        assert q is not None
        assert q.category == 'hr_pulse'
        assert 'PULSE' in q.prompt
        assert q.provenance.get('role_tag') == 'hr'
        assert q.provenance.get('source') == 'self_prompt'

    def test_missing_template_returns_none(self):
        q = _build_self_prompt(self._templates(), 'no_such_cat', 'jo@x')
        assert q is None

    def test_missing_prompt_field_returns_none(self):
        q = _build_self_prompt(self._templates(), 'unknown_cat', 'jo@x')
        assert q is None

    def test_role_tag_falls_back_to_category_name(self):
        templates = {
            'finance_check': {
                'prompt': 'FINANCE — anything?',
                'reply_format': 'Free text',
                # no role_tag set
            }
        }
        q = _build_self_prompt(templates, 'finance_check', 'jo@x')
        assert q.provenance['role_tag'] == 'finance_check'


# ── 'nothing' answer detection ──────────────────────────────────────

class TestIsNothingAnswer:
    @pytest.mark.parametrize('text', [
        'nothing', 'Nothing', 'NOTHING', 'nothing.', 'nothing!',
        'none', 'n/a', 'na', '-', 'all good', 'all clean',
        'clear', 'nope', 'nada', '', '   ',
    ])
    def test_recognises(self, text):
        assert _is_nothing_answer(text)

    @pytest.mark.parametrize('text', [
        'staff issue with timekeeping',
        'late invoice from supplier',
        'no, but here is one thing...',
        '5 returns yesterday',
    ])
    def test_does_not_match_real_content(self, text):
        assert not _is_nothing_answer(text)


class TestSelfPromptCategoriesSet:
    def test_includes_jo_categories(self):
        for c in ('hr_pulse', 'finance_check', 'd2c_observation'):
            assert c in _SELF_PROMPT_CATEGORIES

    def test_includes_ivan_categories(self):
        for c in ('production_quality', 'equipment_health',
                  'technical_solve'):
            assert c in _SELF_PROMPT_CATEGORIES


# ── End-to-end question generation for Jo + Ivan ────────────────────

class TestGenerateQuestionsForRoles:
    def test_jo_brief_built_from_her_categories(self, monkeypatch):
        """generate_questions(jo) returns 4 self-prompt questions
        (hr / finance / d2c / open_ended), no DB queries."""
        monkeypatch.delenv('DEEK_BRIEF_PROFILES_PATH', raising=False)
        reload_profiles()
        # Stub the DB connection so even if the override path
        # somehow falls through, the test won't depend on it
        from core.brief import questions as Q

        def _no_conn():
            raise RuntimeError('test: db should not be needed')

        monkeypatch.setattr(Q, '_connect', _no_conn)
        qs = Q.generate_questions('jo@nbnesigns.com')
        cats = [q.category for q in qs.questions]
        assert cats == ['hr_pulse', 'finance_check',
                        'd2c_observation', 'open_ended']
        # role_tag propagates into provenance
        assert qs.questions[0].provenance.get('role_tag') == 'hr'

    def test_ivan_brief_built_from_his_categories(self, monkeypatch):
        monkeypatch.delenv('DEEK_BRIEF_PROFILES_PATH', raising=False)
        reload_profiles()
        from core.brief import questions as Q

        def _no_conn():
            raise RuntimeError('test: db should not be needed')

        monkeypatch.setattr(Q, '_connect', _no_conn)
        qs = Q.generate_questions('ivan@nbnesigns.com')
        cats = [q.category for q in qs.questions]
        assert cats == ['production_quality', 'equipment_health',
                        'technical_solve', 'open_ended']
        assert qs.questions[0].provenance.get('role_tag') == 'production'
