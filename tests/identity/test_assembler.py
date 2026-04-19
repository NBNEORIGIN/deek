"""Unit tests for core.identity.assembler."""
from __future__ import annotations

import importlib
import os
import textwrap
from pathlib import Path

import pytest


GOOD_IDENTITY = "# DEEK_IDENTITY.md\n\nDeek is NBNE's sovereign AI brain.\n"

GOOD_MODULES = textwrap.dedent("""\
    modules:
      - name: alpha
        display_name: Alpha
        purpose: Alpha module purpose.
        when_to_consult: When you need alpha.
        base_url: https://alpha.example
        health_endpoint: /api/health
        context_endpoint: /api/deek/context
        auth_mode: service_token
        owner: Toby
        status: production
      - name: beta
        display_name: Beta
        purpose: Beta module purpose.
        when_to_consult: When you need beta.
        base_url: https://beta.example
        health_endpoint: /api/health
        context_endpoint: /api/deek/context
        auth_mode: none
        owner: Toby
        status: development
""")


def _load_assembler(tmp_path: Path, identity: str, modules_yaml: str):
    """Load a fresh copy of assembler pointed at tmp files."""
    idp = tmp_path / 'DEEK_IDENTITY.md'
    mp = tmp_path / 'DEEK_MODULES.yaml'
    idp.write_text(identity, encoding='utf-8')
    mp.write_text(modules_yaml, encoding='utf-8')
    os.environ['DEEK_IDENTITY_PATH'] = str(idp)
    os.environ['DEEK_MODULES_PATH'] = str(mp)
    # Force reload so the module-level loader runs against our files.
    import core.identity.assembler as a
    return importlib.reload(a)


def test_happy_path(tmp_path):
    a = _load_assembler(tmp_path, GOOD_IDENTITY, GOOD_MODULES)
    assert 'sovereign AI brain' in a.get_identity_text()
    modules = a.get_modules()
    assert [m.name for m in modules] == ['alpha', 'beta']
    assert modules[0].health_url == 'https://alpha.example/api/health'


def test_hash_stable_across_loads(tmp_path):
    a = _load_assembler(tmp_path, GOOD_IDENTITY, GOOD_MODULES)
    h1 = a.get_identity_hash()
    a2 = _load_assembler(tmp_path, GOOD_IDENTITY, GOOD_MODULES)
    assert h1 == a2.get_identity_hash()
    assert h1.startswith('sha256:')


def test_hash_changes_with_content(tmp_path):
    a = _load_assembler(tmp_path, GOOD_IDENTITY, GOOD_MODULES)
    h1 = a.get_identity_hash()
    a2 = _load_assembler(tmp_path, GOOD_IDENTITY + '\nextra line', GOOD_MODULES)
    assert h1 != a2.get_identity_hash()


def test_missing_identity_raises(tmp_path):
    os.environ['DEEK_IDENTITY_PATH'] = str(tmp_path / 'missing.md')
    os.environ['DEEK_MODULES_PATH'] = str(tmp_path / 'missing.yaml')
    import core.identity.assembler as a
    with pytest.raises(Exception):
        importlib.reload(a)


def test_malformed_yaml(tmp_path):
    with pytest.raises(Exception):
        _load_assembler(tmp_path, GOOD_IDENTITY, "modules:\n  - this is: not: valid: yaml::")


def test_unknown_auth_mode(tmp_path):
    bad = GOOD_MODULES.replace('auth_mode: service_token', 'auth_mode: bogus')
    with pytest.raises(Exception) as exc:
        _load_assembler(tmp_path, GOOD_IDENTITY, bad)
    assert 'auth_mode' in str(exc.value)


def test_missing_required_field(tmp_path):
    # Strip purpose from alpha
    bad = GOOD_MODULES.replace('    purpose: Alpha module purpose.\n', '', 1)
    assert 'Alpha module purpose' not in bad  # sanity: we actually removed it
    with pytest.raises(Exception) as exc:
        _load_assembler(tmp_path, GOOD_IDENTITY, bad)
    assert 'purpose' in str(exc.value)


def test_duplicate_names(tmp_path):
    bad = GOOD_MODULES.replace('name: beta', 'name: alpha')
    with pytest.raises(Exception) as exc:
        _load_assembler(tmp_path, GOOD_IDENTITY, bad)
    assert 'duplicate' in str(exc.value).lower()


def test_empty_modules_list(tmp_path):
    with pytest.raises(Exception):
        _load_assembler(tmp_path, GOOD_IDENTITY, "modules: []\n")


def test_prefix_includes_reachable_and_unreachable(tmp_path):
    a = _load_assembler(tmp_path, GOOD_IDENTITY, GOOD_MODULES)
    prefix = a.get_system_prompt_prefix(
        reachable={'alpha'},
        errors={'beta': 'timeout'},
    )
    assert 'sovereign AI brain' in prefix
    assert 'Alpha' in prefix and 'REACHABLE' in prefix
    assert 'Beta' in prefix and 'UNREACHABLE' in prefix
    assert 'timeout' in prefix


def test_prefix_declares_all_modules_even_when_unreachable(tmp_path):
    """Unreachable modules must be explicitly declared, not omitted."""
    a = _load_assembler(tmp_path, GOOD_IDENTITY, GOOD_MODULES)
    prefix = a.get_system_prompt_prefix(reachable=set(), errors={})
    # Both modules appear in the prefix even with no reachability.
    assert 'alpha' in prefix and 'beta' in prefix
    # Each module line is marked UNREACHABLE (header blurb also mentions
    # the word, so we count only list-item lines).
    module_lines = [l for l in prefix.split('\n') if l.startswith('- **')]
    assert len(module_lines) == 2
    assert all('UNREACHABLE' in l for l in module_lines)


def test_get_module_by_name(tmp_path):
    a = _load_assembler(tmp_path, GOOD_IDENTITY, GOOD_MODULES)
    assert a.get_module_by_name('alpha').display_name == 'Alpha'
    assert a.get_module_by_name('nope') is None
