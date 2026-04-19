"""Integration-ish test: assembled prefix contains declared modules verbatim."""
from __future__ import annotations

import importlib
import os
import textwrap
from pathlib import Path


IDENTITY = textwrap.dedent("""\
    # DEEK_IDENTITY.md

    Deek is NBNE's sovereign AI brain. The code stays in Northumberland.
""")

MODULES = textwrap.dedent("""\
    modules:
      - name: phloe
        display_name: Phloe
        purpose: Multi-tenant booking SaaS.
        when_to_consult: For booking questions.
        base_url: https://phloe.co.uk
        health_endpoint: /api/health
        context_endpoint: /api/deek/context
        auth_mode: service_token
        owner: Toby
        status: production
      - name: ami
        display_name: Amazon Intelligence
        purpose: SP-API Amazon data across marketplaces.
        when_to_consult: For Amazon questions.
        base_url: https://ami.nbnesigns.co.uk
        health_endpoint: /api/health
        context_endpoint: /api/deek/context
        auth_mode: service_token
        owner: Toby
        status: production
""")


def test_prefix_contains_identity_and_all_modules(tmp_path):
    idp = tmp_path / 'id.md'
    mp = tmp_path / 'mods.yaml'
    idp.write_text(IDENTITY, encoding='utf-8')
    mp.write_text(MODULES, encoding='utf-8')
    os.environ['DEEK_IDENTITY_PATH'] = str(idp)
    os.environ['DEEK_MODULES_PATH'] = str(mp)
    import core.identity.assembler as a
    importlib.reload(a)

    prefix = a.get_system_prompt_prefix(reachable={'phloe'}, errors={'ami': 'timeout'})

    # Identity text present.
    assert 'sovereign AI brain' in prefix
    assert 'Northumberland' in prefix
    # Both modules declared.
    assert 'Phloe' in prefix and 'Amazon Intelligence' in prefix
    # Reachable module not marked unreachable.
    lines = prefix.split('\n')
    phloe_line = next(l for l in lines if 'Phloe' in l and 'phloe' in l)
    assert 'REACHABLE' in phloe_line and 'UNREACHABLE' not in phloe_line
    # Unreachable declared with reason.
    ami_line = next(l for l in lines if 'Amazon Intelligence' in l)
    assert 'UNREACHABLE' in ami_line and 'timeout' in ami_line


def test_identity_hash_is_deterministic(tmp_path):
    idp = tmp_path / 'id.md'
    mp = tmp_path / 'mods.yaml'
    idp.write_text(IDENTITY, encoding='utf-8')
    mp.write_text(MODULES, encoding='utf-8')
    os.environ['DEEK_IDENTITY_PATH'] = str(idp)
    os.environ['DEEK_MODULES_PATH'] = str(mp)
    import core.identity.assembler as a
    importlib.reload(a)
    h1 = a.get_identity_hash()
    importlib.reload(a)
    assert h1 == a.get_identity_hash()
