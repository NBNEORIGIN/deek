"""Structured logging of every model response — Brief 1a.2 Phase B Task 5.

One row per response, written to `model_response_audit`. The row
records whether the system prompt actually contained the identity
prefix (the most useful signal for catching the regression class Brief
1a.2 closes), whether the response matched a non-answer heuristic, and
basic response shape.

All writes are best-effort. Logging failures never propagate — the
audit log is a diagnostic aid, not a correctness gate, so a broken log
must not break production traffic.

30-day retention enforced opportunistically on ~1% of writes (cheap
amortised cleanup without a separate cron).
"""
from __future__ import annotations

import hashlib
import logging
import os
import random
import threading
from dataclasses import dataclass

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30
# Chance of opportunistic cleanup per write. 1% ≈ one sweep per 100
# responses, plenty at current traffic.
_CLEANUP_PROBABILITY = 0.01

# Heuristic list of "non-answer" substrings. Case-insensitive. Order
# matters only for which one is recorded as the matching pattern.
_NON_ANSWER_PATTERNS: tuple[str, ...] = (
    "i don't have that information",
    "i do not have that information",
    "i'm unable to",
    "i am unable to",
    "i cannot provide",
    "i can't provide",
    "i'm not able to",
    "i am not able to",
    "i don't have access",
    "i do not have access",
    "i don't know",
    "i do not know",
    "i'm sorry, i can't",
    "i'm sorry, i cannot",
    "i apologize, but i",
    "i apologise, but i",
    "as an ai",
    "i'm just an",
)


@dataclass
class ResponseAuditRow:
    path: str
    system_prompt: str
    response_text: str
    session_id: str | None = None
    model: str | None = None
    user_question: str | None = None
    latency_ms: int | None = None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8', errors='replace')).hexdigest()


def _detect_non_answer(response_text: str) -> tuple[bool, str | None]:
    """Return (is_non_answer, matched_pattern).

    First-match wins. Search is case-insensitive against substrings.
    """
    if not response_text:
        return False, None
    lower = response_text.lower()
    for pat in _NON_ANSWER_PATTERNS:
        if pat in lower:
            return True, pat
    return False, None


def _current_identity_hash() -> str:
    """Best-effort fetch of the identity assembler's hash. Empty on
    failure — the assembler may not be loaded in some test contexts.
    """
    try:
        from core.identity import assembler
        return assembler.get_identity_hash()
    except Exception:
        return ''


def _identity_prefix_in(prompt: str, identity_hash: str) -> bool:
    """Heuristic: does the prompt start with the identity prefix?

    We can't include the hash in the prompt text itself (that would
    leak it to the model), so we use structural signals: the prefix
    always starts with '# DEEK_IDENTITY.md' and contains the
    'Modules available right now' subsection added by the assembler.
    Both are hard-coded in assembler.get_system_prompt_prefix.

    The head window needs to be generous — the full identity file is
    ~7KB of prose, plus the module list section. 20KB covers real
    identity output with comfortable slack for additions without
    letting the scan become a linear walk of massive prompts.
    """
    if not prompt:
        return False
    head = prompt[:20000]
    return (
        '# DEEK_IDENTITY.md' in head
        and 'Modules available right now' in head
    )


def _cleanup_old_rows(conn) -> None:
    """Delete rows older than RETENTION_DAYS. Cheap op when there's
    nothing to delete; runs on a fraction of writes."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM model_response_audit "
                "WHERE created_at < NOW() - (INTERVAL '1 day' * %s)",
                (RETENTION_DAYS,),
            )
    except Exception as exc:
        logger.debug('[audit] cleanup failed (non-fatal): %s', exc)


def _log_sync(row: ResponseAuditRow) -> None:
    """Synchronous write. Called by log_async inside a daemon thread."""
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        return
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=3)
    except Exception as exc:
        logger.debug('[audit] connect failed: %s', exc)
        return
    try:
        is_non_answer, matched = _detect_non_answer(row.response_text)
        identity_hash = _current_identity_hash()
        system_hash = _sha256(row.system_prompt)
        question_hash = (
            _sha256(row.user_question) if row.user_question else None
        )
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO model_response_audit
                    (path, session_id, model, system_prompt_hash,
                     identity_hash, identity_prefix_present,
                     response_length_chars,
                     response_contains_non_answer, non_answer_pattern,
                     user_question_sha, latency_ms)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    row.path, row.session_id, row.model,
                    system_hash,
                    identity_hash,
                    _identity_prefix_in(row.system_prompt, identity_hash),
                    len(row.response_text or ''),
                    is_non_answer, matched,
                    question_hash, row.latency_ms,
                ),
            )
        conn.commit()

        if random.random() < _CLEANUP_PROBABILITY:
            _cleanup_old_rows(conn)
            conn.commit()
    except Exception as exc:
        logger.debug('[audit] write failed (non-fatal): %s', exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def log_async(row: ResponseAuditRow) -> None:
    """Fire-and-forget write. Runs on a daemon thread so the hot
    response path is never blocked on the audit insert.

    Failures are logged at DEBUG and swallowed — this function never
    raises.
    """
    try:
        t = threading.Thread(
            target=_log_sync, args=(row,),
            name='deek-audit-write', daemon=True,
        )
        t.start()
    except Exception as exc:
        logger.debug('[audit] thread spawn failed: %s', exc)


__all__ = [
    'ResponseAuditRow',
    'log_async',
    '_detect_non_answer',      # exported for tests
    '_identity_prefix_in',     # exported for tests
    '_NON_ANSWER_PATTERNS',    # exported for tests
    'RETENTION_DAYS',
]
