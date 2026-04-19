"""Deek identity layer.

Identity is code, episodic memory is data. This package loads Deek's
self-description from version-controlled markdown + YAML at repo root
and exposes it to the system prompt builder.

Public surface:
    from core.identity import assembler, probe

See DEEK_IDENTITY.md + DEEK_MODULES.yaml at the repo root.
"""
from . import assembler  # noqa: F401
from . import probe  # noqa: F401
