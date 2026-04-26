"""Tests for core.brief.user_profile — Memory Brief Tier 2.

Pin:
  * loading the real YAML (smoke)
  * overrides via DEEK_BRIEF_PROFILES_PATH
  * graceful default when config missing
  * role-scoped open_ended prompt wiring
"""
from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from core.brief.user_profile import (
    UserProfile,
    active_users,
    get_profile,
    reload_profiles,
)


class TestGetProfile:
    def test_real_yaml_loads(self, monkeypatch):
        """Smoke: the shipped user_profiles.yaml actually parses."""
        monkeypatch.delenv('DEEK_BRIEF_PROFILES_PATH', raising=False)
        reload_profiles()
        p = get_profile('toby@nbnesigns.com')
        assert p.role == 'director'
        assert p.display_name == 'Toby'

    def test_tier2_jo(self, monkeypatch):
        monkeypatch.delenv('DEEK_BRIEF_PROFILES_PATH', raising=False)
        reload_profiles()
        p = get_profile('jo@nbnesigns.com')
        # Role broadened 2026-04-26 to reflect actual remit
        assert p.role == 'operations_hr_finance'
        assert p.display_name == 'Jo'
        assert p.open_ended_prompt is not None
        assert 'office' in p.open_ended_prompt.lower() or 'shop floor' in p.open_ended_prompt.lower()

    def test_tier2_ivan(self, monkeypatch):
        monkeypatch.delenv('DEEK_BRIEF_PROFILES_PATH', raising=False)
        reload_profiles()
        p = get_profile('ivan@nbnesigns.com')
        assert p.role == 'production_tech'
        assert p.display_name == 'Ivan'
        assert p.open_ended_prompt is not None

    def test_unknown_user_falls_back_to_director(self, monkeypatch):
        monkeypatch.delenv('DEEK_BRIEF_PROFILES_PATH', raising=False)
        reload_profiles()
        p = get_profile('nobody@example.com')
        assert p.role == 'director'
        assert p.display_name == ''
        assert p.open_ended_prompt is None

    def test_case_insensitive_email_lookup(self, monkeypatch):
        monkeypatch.delenv('DEEK_BRIEF_PROFILES_PATH', raising=False)
        reload_profiles()
        p = get_profile('JO@NBNESIGNS.COM')
        assert p.role == 'operations_hr_finance'


class TestConfigFallback:
    def test_missing_config_returns_defaults(self, monkeypatch, tmp_path):
        missing = tmp_path / 'nonexistent.yaml'
        monkeypatch.setenv('DEEK_BRIEF_PROFILES_PATH', str(missing))
        reload_profiles()
        # Anyone falls through to director-tier default
        p = get_profile('jo@nbnesigns.com')
        assert p.role == 'director'
        assert active_users() == []

    def test_broken_yaml_graceful(self, monkeypatch, tmp_path):
        bad = tmp_path / 'broken.yaml'
        bad.write_text(': :::: not valid yaml')
        monkeypatch.setenv('DEEK_BRIEF_PROFILES_PATH', str(bad))
        reload_profiles()
        assert active_users() == []
        # Lookup still returns default — no exception
        assert get_profile('toby@nbnesigns.com').role == 'director'


class TestActiveUsers:
    def test_all_tier1_and_tier2_active(self, monkeypatch):
        monkeypatch.delenv('DEEK_BRIEF_PROFILES_PATH', raising=False)
        reload_profiles()
        emails = {u.email for u in active_users()}
        assert 'toby@nbnesigns.com' in emails
        assert 'jo@nbnesigns.com' in emails
        assert 'ivan@nbnesigns.com' in emails


class TestOpenEndedIntegration:
    """The question builder is supposed to honour the override prompt."""

    def test_override_returned_when_profile_has_one(self, monkeypatch):
        monkeypatch.delenv('DEEK_BRIEF_PROFILES_PATH', raising=False)
        reload_profiles()
        from core.brief.questions import _build_open_ended, _open_ended_override
        override = _open_ended_override('jo@nbnesigns.com')
        assert override is not None
        q = _build_open_ended({}, override)
        assert 'shop floor' in q.prompt.lower()
        assert q.category == 'open_ended'
        assert q.provenance == {'source': 'user_profile_override'}

    def test_no_override_for_toby(self, monkeypatch):
        monkeypatch.delenv('DEEK_BRIEF_PROFILES_PATH', raising=False)
        reload_profiles()
        from core.brief.questions import _open_ended_override
        assert _open_ended_override('toby@nbnesigns.com') is None

    def test_no_override_for_unknown(self, monkeypatch):
        monkeypatch.delenv('DEEK_BRIEF_PROFILES_PATH', raising=False)
        reload_profiles()
        from core.brief.questions import _open_ended_override
        assert _open_ended_override('stranger@x.com') is None
