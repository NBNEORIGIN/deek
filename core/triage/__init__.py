"""Triage Phase B — reply parsing + apply.

Sibling of core.brief: same reply-back shape (--- Q<n> (<category>) ---
delimiters), different storage and apply logic.

  * core.brief.replies        → Memory Brief replies
  * core.triage.replies       → Triage digest replies

Both share the classify-first-word-as-verdict convention, quote
stripping via the same heuristics, and the idempotency pattern
(SHA over raw_body + row_id). Carried through because having two
slightly-different parsers for two reply flows would be a
maintenance tax I don't want to pay.
"""
from . import replies  # noqa: F401
