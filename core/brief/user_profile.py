"""User profile loader for Memory Brief Tier 2.

Reads ``config/brief/user_profiles.yaml`` to map an email address to
a role, display name, and optional open-ended prompt override. Used
by the send script and the question composer to scope the brief to
each recipient.

Missing users fall back to sensible director-tier defaults so the
tier-1 path (Toby) keeps working unchanged.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml


logger = logging.getLogger(__name__)


@dataclass
class UserProfile:
    email: str
    role: str
    display_name: str
    open_ended_prompt: str | None = None
    active: bool = True


_DEFAULT = UserProfile(
    email='',
    role='director',
    display_name='',
    open_ended_prompt=None,
    active=True,
)


def _config_path() -> Path:
    override = os.getenv('DEEK_BRIEF_PROFILES_PATH')
    if override:
        return Path(override)
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / 'config' / 'brief' / 'user_profiles.yaml'


_cache: dict[str, UserProfile] | None = None


def _load() -> dict[str, UserProfile]:
    global _cache
    if _cache is not None:
        return _cache
    path = _config_path()
    out: dict[str, UserProfile] = {}
    if not path.exists():
        logger.warning(
            'user_profile: config not found at %s — tier-2 disabled',
            path,
        )
        _cache = out
        return out
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except Exception as exc:
        logger.warning('user_profile: yaml load failed: %s', exc)
        _cache = out
        return out
    for email, cfg in (data.get('users') or {}).items():
        if not isinstance(cfg, dict):
            continue
        out[email.strip().lower()] = UserProfile(
            email=email.strip().lower(),
            role=str(cfg.get('role') or 'director'),
            display_name=str(cfg.get('display_name') or ''),
            open_ended_prompt=(cfg.get('open_ended_prompt') or None),
            active=bool(cfg.get('active', True)),
        )
    _cache = out
    return out


def reload_profiles() -> None:
    """Clear the cache. Called by tests + by ops when rolling out a
    config change without restarting the API container."""
    global _cache
    _cache = None


def get_profile(email: str) -> UserProfile:
    """Return the profile for an email, or a director-tier default
    when not configured. Never raises."""
    key = (email or '').strip().lower()
    profile = _load().get(key)
    if profile is None:
        return UserProfile(
            email=key, role='director', display_name='', active=True,
        )
    return profile


def active_users() -> list[UserProfile]:
    """Return every profile with active=True. Cron uses this to
    iterate recipients for the daily send."""
    return [p for p in _load().values() if p.active]


__all__ = [
    'UserProfile',
    'get_profile',
    'active_users',
    'reload_profiles',
]
