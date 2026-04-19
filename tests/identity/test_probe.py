"""Unit tests for core.identity.probe."""
from __future__ import annotations

import asyncio
import importlib
import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest


MODULES_YAML = textwrap.dedent("""\
    modules:
      - name: alpha
        display_name: Alpha
        purpose: Alpha purpose.
        when_to_consult: When alpha.
        base_url: https://alpha.test
        health_endpoint: /api/health
        context_endpoint: /api/deek/context
        auth_mode: none
        owner: Toby
        status: production
      - name: beta
        display_name: Beta
        purpose: Beta purpose.
        when_to_consult: When beta.
        base_url: https://beta.test
        health_endpoint: /api/health
        context_endpoint: /api/deek/context
        auth_mode: none
        owner: Toby
        status: production
""")


def _prep(tmp_path: Path):
    idp = tmp_path / 'DEEK_IDENTITY.md'
    mp = tmp_path / 'DEEK_MODULES.yaml'
    idp.write_text("# DEEK\nDeek is NBNE's brain.", encoding='utf-8')
    mp.write_text(MODULES_YAML, encoding='utf-8')
    os.environ['DEEK_IDENTITY_PATH'] = str(idp)
    os.environ['DEEK_MODULES_PATH'] = str(mp)
    import core.identity.assembler as a
    importlib.reload(a)
    import core.identity.probe as p
    importlib.reload(p)
    return a, p


def _mock_transport(responses: dict[str, int | Exception]):
    """Build an httpx MockTransport responding per URL prefix."""
    def handler(request: httpx.Request) -> httpx.Response:
        for prefix, outcome in responses.items():
            if str(request.url).startswith(prefix):
                if isinstance(outcome, Exception):
                    raise outcome
                return httpx.Response(outcome, text='ok')
        return httpx.Response(500, text='no route')
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_all_reachable(tmp_path):
    _, p = _prep(tmp_path)
    transport = _mock_transport({
        'https://alpha.test': 200,
        'https://beta.test': 200,
    })
    _real = httpx.AsyncClient
    with patch('core.identity.probe.httpx.AsyncClient', lambda **kw: _real(transport=transport)):
        await p.probe_once()
    assert p.get_reachable_modules() == {'alpha', 'beta'}
    assert p.get_errors() == {}


@pytest.mark.asyncio
async def test_all_unreachable(tmp_path):
    _, p = _prep(tmp_path)
    transport = _mock_transport({
        'https://alpha.test': httpx.ConnectError('refused'),
        'https://beta.test': httpx.ConnectError('refused'),
    })
    _real = httpx.AsyncClient
    with patch('core.identity.probe.httpx.AsyncClient', lambda **kw: _real(transport=transport)):
        await p.probe_once()
    assert p.get_reachable_modules() == set()
    errs = p.get_errors()
    assert set(errs.keys()) == {'alpha', 'beta'}


@pytest.mark.asyncio
async def test_mixed(tmp_path):
    _, p = _prep(tmp_path)
    transport = _mock_transport({
        'https://alpha.test': 200,
        'https://beta.test': httpx.ConnectError('refused'),
    })
    _real = httpx.AsyncClient
    with patch('core.identity.probe.httpx.AsyncClient', lambda **kw: _real(transport=transport)):
        await p.probe_once()
    assert p.get_reachable_modules() == {'alpha'}
    assert 'beta' in p.get_errors()


@pytest.mark.asyncio
async def test_5xx_counts_as_unreachable(tmp_path):
    _, p = _prep(tmp_path)
    transport = _mock_transport({
        'https://alpha.test': 503,
        'https://beta.test': 200,
    })
    _real = httpx.AsyncClient
    with patch('core.identity.probe.httpx.AsyncClient', lambda **kw: _real(transport=transport)):
        await p.probe_once()
    assert 'alpha' not in p.get_reachable_modules()
    assert 'HTTP 503' in p.get_errors()['alpha']


@pytest.mark.asyncio
async def test_probe_does_not_raise(tmp_path):
    """Probe must never raise — network errors become dict entries."""
    _, p = _prep(tmp_path)
    transport = _mock_transport({
        'https://alpha.test': Exception('weird'),
    })
    _real = httpx.AsyncClient
    with patch('core.identity.probe.httpx.AsyncClient', lambda **kw: _real(transport=transport)):
        # Should not raise.
        await p.probe_once()
    status = p.get_probe_status()
    assert status['last_probe'] is not None


@pytest.mark.asyncio
async def test_status_shape(tmp_path):
    _, p = _prep(tmp_path)
    transport = _mock_transport({
        'https://alpha.test': 200,
        'https://beta.test': 200,
    })
    _real = httpx.AsyncClient
    with patch('core.identity.probe.httpx.AsyncClient', lambda **kw: _real(transport=transport)):
        await p.probe_once()
    st = p.get_probe_status()
    assert 'last_probe' in st and 'modules' in st
    assert set(st['modules'].keys()) == {'alpha', 'beta'}
    for name, entry in st['modules'].items():
        assert 'reachable' in entry and 'last_checked' in entry
