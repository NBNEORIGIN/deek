"""Deek identity assembler.

Loads DEEK_IDENTITY.md + DEEK_MODULES.yaml from the repo root at import
time. Validates the YAML schema. Exposes the identity prefix used by the
system prompt builder.

Principles:
- Identity is code. This module is the *only* path that builds the
  identity prefix.
- No fallback to DB, no runtime mutation. If files are missing or
  malformed the process fails loudly at startup.
- Unreachable modules are *declared as unreachable*, not silently
  omitted — the system prompt must reflect reality.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

logger = logging.getLogger(__name__)

# Repo root: this file lives at D:\claw\core\identity\assembler.py, so the
# repo root is three parents up.
_REPO_ROOT = Path(__file__).resolve().parents[2]

IDENTITY_PATH = Path(
    os.getenv('DEEK_IDENTITY_PATH', str(_REPO_ROOT / 'DEEK_IDENTITY.md'))
)
MODULES_PATH = Path(
    os.getenv('DEEK_MODULES_PATH', str(_REPO_ROOT / 'DEEK_MODULES.yaml'))
)

_VALID_AUTH_MODES = {'service_token', 'session', 'none'}
_VALID_STATUSES = {'production', 'development', 'spec', 'greenfield'}
_REQUIRED_FIELDS = (
    'name', 'display_name', 'purpose', 'when_to_consult',
    'base_url', 'health_endpoint', 'context_endpoint',
    'auth_mode', 'owner', 'status',
)


class IdentityValidationError(Exception):
    """Raised when DEEK_IDENTITY.md or DEEK_MODULES.yaml is malformed."""


@dataclass(frozen=True)
class ModuleSpec:
    name: str
    display_name: str
    purpose: str
    when_to_consult: str
    base_url: str
    health_endpoint: str
    context_endpoint: str
    auth_mode: str
    owner: str
    status: str

    @property
    def health_url(self) -> str:
        return self.base_url.rstrip('/') + '/' + self.health_endpoint.lstrip('/')

    @property
    def context_url(self) -> str:
        return self.base_url.rstrip('/') + '/' + self.context_endpoint.lstrip('/')


# ── Loaders ────────────────────────────────────────────────────────────

def _read_identity_text(path: Path) -> str:
    if not path.exists():
        raise IdentityValidationError(
            f"DEEK_IDENTITY.md not found at {path}. "
            f"Identity-broken Deek is worse than offline — refusing to start."
        )
    text = path.read_text(encoding='utf-8').strip()
    if not text:
        raise IdentityValidationError(f"{path} is empty.")
    return text


def _read_modules(path: Path) -> list[ModuleSpec]:
    if not path.exists():
        raise IdentityValidationError(
            f"DEEK_MODULES.yaml not found at {path}. "
            f"Identity-broken Deek is worse than offline — refusing to start."
        )
    raw = path.read_text(encoding='utf-8')
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise IdentityValidationError(f"{path}: invalid YAML — {exc}") from exc

    if not isinstance(data, dict) or 'modules' not in data:
        raise IdentityValidationError(
            f"{path}: top-level must be a mapping with a 'modules' key."
        )
    entries = data['modules']
    if not isinstance(entries, list) or not entries:
        raise IdentityValidationError(
            f"{path}: 'modules' must be a non-empty list."
        )

    specs: list[ModuleSpec] = []
    seen_names: set[str] = set()
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise IdentityValidationError(
                f"{path}: module entry #{idx} is not a mapping."
            )
        missing = [f for f in _REQUIRED_FIELDS if f not in entry]
        if missing:
            raise IdentityValidationError(
                f"{path}: module entry #{idx} missing fields: {missing}"
            )
        if entry['auth_mode'] not in _VALID_AUTH_MODES:
            raise IdentityValidationError(
                f"{path}: module '{entry['name']}' has unknown auth_mode "
                f"'{entry['auth_mode']}'. Valid: {sorted(_VALID_AUTH_MODES)}"
            )
        if entry['status'] not in _VALID_STATUSES:
            raise IdentityValidationError(
                f"{path}: module '{entry['name']}' has unknown status "
                f"'{entry['status']}'. Valid: {sorted(_VALID_STATUSES)}"
            )
        if entry['name'] in seen_names:
            raise IdentityValidationError(
                f"{path}: duplicate module name '{entry['name']}'."
            )
        seen_names.add(entry['name'])
        specs.append(ModuleSpec(
            name=str(entry['name']).strip(),
            display_name=str(entry['display_name']).strip(),
            purpose=str(entry['purpose']).strip(),
            when_to_consult=str(entry['when_to_consult']).strip(),
            base_url=str(entry['base_url']).strip(),
            health_endpoint=str(entry['health_endpoint']).strip(),
            context_endpoint=str(entry['context_endpoint']).strip(),
            auth_mode=str(entry['auth_mode']).strip(),
            owner=str(entry['owner']).strip(),
            status=str(entry['status']).strip(),
        ))
    return specs


# ── Module-level cache ────────────────────────────────────────────────

# Loaded eagerly at import so misconfigured deploys fail fast.
_IDENTITY_TEXT: str = _read_identity_text(IDENTITY_PATH)
_MODULES: list[ModuleSpec] = _read_modules(MODULES_PATH)


def _compute_hash(identity_text: str, modules_raw: str) -> str:
    h = hashlib.sha256()
    h.update(identity_text.encode('utf-8'))
    h.update(b'\0')
    h.update(modules_raw.encode('utf-8'))
    return 'sha256:' + h.hexdigest()


_IDENTITY_HASH: str = _compute_hash(
    _IDENTITY_TEXT,
    MODULES_PATH.read_text(encoding='utf-8'),
)

logger.info('[IDENTITY] loaded: %s modules, hash=%s',
            len(_MODULES), _IDENTITY_HASH[:19] + '...')


# ── Public surface ────────────────────────────────────────────────────

def get_identity_text() -> str:
    """Raw markdown from DEEK_IDENTITY.md."""
    return _IDENTITY_TEXT


def get_modules() -> list[ModuleSpec]:
    """Parsed module list (frozen dataclasses)."""
    return list(_MODULES)


def get_identity_hash() -> str:
    """SHA256 over the two loaded files, prefixed sha256:."""
    return _IDENTITY_HASH


def get_module_by_name(name: str) -> ModuleSpec | None:
    for m in _MODULES:
        if m.name == name:
            return m
    return None


def _format_module_line(m: ModuleSpec, reachable: bool, error: str | None = None) -> str:
    """One-line module descriptor for the system prompt."""
    # Strip newlines from purpose so each module is one line; keep it short.
    purpose = ' '.join(m.purpose.split())
    if reachable:
        return f"- **{m.display_name}** ({m.name}) — REACHABLE. {purpose}"
    reason = f" [{error}]" if error else ""
    return (
        f"- **{m.display_name}** ({m.name}) — UNREACHABLE{reason}. "
        f"Do not claim live data from this module until it is back. {purpose}"
    )


def get_system_prompt_prefix(
    reachable: Iterable[str],
    errors: dict[str, str] | None = None,
) -> str:
    """Assemble the identity prefix for the system prompt.

    Args:
        reachable: set of module names currently reachable.
        errors: optional map of module name → probe error string.

    Returns:
        Identity markdown + a dynamically-generated 'Modules available
        right now' section reflecting live reachability.
    """
    reachable_set = set(reachable)
    errors = errors or {}

    lines: list[str] = []
    lines.append(_IDENTITY_TEXT)
    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('## Modules available right now')
    lines.append('')
    lines.append(
        'The following modules are declared in DEEK_MODULES.yaml. Reachability '
        'is probed on startup and every 60 seconds. If a module is UNREACHABLE, '
        'do not invent data from it — say so and stop.'
    )
    lines.append('')
    for m in _MODULES:
        is_reachable = m.name in reachable_set
        err = errors.get(m.name) if not is_reachable else None
        lines.append(_format_module_line(m, is_reachable, err))
    lines.append('')
    lines.append('---')
    lines.append('')
    return '\n'.join(lines)
