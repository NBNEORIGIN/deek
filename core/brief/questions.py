"""Question generator for the Memory Brief — Phase A.

Each question is derived from live memory state, carries provenance
(which schema / memory row it came from), and has a fixed reply
format so Phase B's parser can write answers back deterministically.

Four categories:

    belief_audit         — low-access active schema
    gist_validation      — recent (<=30d, >=7d) consolidated schema
    salience_calibration — yesterday's highest-salience memory
    open_ended           — always included, no DB query

Returns 1-4 questions per call. Always at least the open-ended one;
the other three skip cleanly when there's nothing suitable in the
DB, which is the normal case at low memory volume.

Discipline enforced by design:

    * Every non-open question cites a real DB row. No hallucinated
      references — the generator queries, then formats. Opposite
      order would allow drift.
    * Template is loaded from config/brief/templates.yaml at call
      time, not module load — so a PR to the template is reflected
      on the next run without a deploy.
    * Failures in individual categories log + skip; never raise.
      The brief is better sent with 2 questions than not sent at all.
"""
from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATES_PATH = Path(
    os.getenv('DEEK_BRIEF_TEMPLATES',
              str(_REPO_ROOT / 'config' / 'brief' / 'templates.yaml'))
)


# ── Data types ────────────────────────────────────────────────────────

@dataclass
class Question:
    """One generated question. The `provenance` dict is the link back
    to the DB row that produced it — Phase B's parser uses it to
    write answers back to the right row.
    """
    category: str                  # belief_audit | gist_validation | ...
    prompt: str                    # rendered text to put in the email
    reply_format: str              # instruction to the user
    provenance: dict = field(default_factory=dict)


# ── Template loading ──────────────────────────────────────────────────

def _load_templates() -> dict:
    """Read the templates file. Returns {} on any failure — the
    generator then produces no structured questions, only the
    open-ended fallback that has no template dependency.
    """
    if not _TEMPLATES_PATH.exists():
        logger.warning('[brief] template file missing: %s', _TEMPLATES_PATH)
        return {}
    try:
        import yaml
        data = yaml.safe_load(_TEMPLATES_PATH.read_text(encoding='utf-8')) or {}
        templates = data.get('templates') or {}
        if not isinstance(templates, dict):
            logger.warning('[brief] templates not a mapping')
            return {}
        return templates
    except Exception as exc:
        logger.warning('[brief] template load failed: %s', exc)
        return {}


def _render(template_body: dict | None, **fmt) -> tuple[str, str]:
    """Return (prompt, reply_format) from a template block.

    Missing template → raises ValueError so caller can skip the
    category cleanly. Missing format key in the prompt → also
    raises, because it indicates a template / source-query mismatch
    we want surfaced loudly.
    """
    if not template_body or 'prompt' not in template_body:
        raise ValueError('template missing prompt')
    prompt = str(template_body['prompt']).format(**fmt).strip()
    reply_format = str(template_body.get('reply_format') or '').strip()
    return prompt, reply_format


# ── DB helpers ────────────────────────────────────────────────────────

def _connect():
    import psycopg2
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        raise RuntimeError('DATABASE_URL not set')
    return psycopg2.connect(db_url, connect_timeout=5)


def _pick_belief_audit_schema(conn) -> dict | None:
    """Low-access active schema — EITHER wrong or correctly-ignored,
    both worth confirming. Prefers least-accessed, then oldest (so
    long-untouched schemas get reviewed before recent ones).
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id::text, schema_text, access_count, confidence,
                          derived_at,
                          EXTRACT(EPOCH FROM (NOW() - derived_at))/86400 AS age_days
                     FROM schemas
                    WHERE status = 'active'
                      AND access_count <= 2
                      AND derived_at < NOW() - INTERVAL '2 days'
                    ORDER BY access_count ASC, derived_at ASC
                    LIMIT 5"""
            )
            rows = cur.fetchall()
    except Exception as exc:
        logger.warning('[brief] belief_audit query failed: %s', exc)
        return None
    if not rows:
        return None
    row = random.choice(rows)
    return {
        'schema_id': row[0],
        'schema_text': row[1],
        'access_count': int(row[2] or 0),
        'confidence': float(row[3] or 0.0),
        'derived_at': row[4],
        'schema_age_days': int(row[5] or 0),
    }


def _pick_gist_validation_schema(conn) -> dict | None:
    """Recent consolidation — between 7 and 30 days old, confidence
    in the middle band where the consolidator wasn't very confident
    but wasn't dismissing either. Most interesting slice to audit.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id::text, schema_text,
                          COALESCE(array_length(source_memory_ids, 1), 0),
                          confidence,
                          derived_at,
                          EXTRACT(EPOCH FROM (NOW() - derived_at))/86400 AS age_days
                     FROM schemas
                    WHERE status = 'active'
                      AND confidence BETWEEN 0.70 AND 0.90
                      AND derived_at BETWEEN NOW() - INTERVAL '30 days'
                                         AND NOW() - INTERVAL '7 days'
                    ORDER BY RANDOM()
                    LIMIT 1"""
            )
            row = cur.fetchone()
    except Exception as exc:
        logger.warning('[brief] gist_validation query failed: %s', exc)
        return None
    if not row:
        return None
    return {
        'schema_id': row[0],
        'schema_text': row[1],
        'source_count': int(row[2] or 0),
        'confidence': float(row[3] or 0.0),
        'derived_at': row[4],
        'schema_age_days': int(row[5] or 0),
    }


_MEMORY_CHUNK_TYPES = ('memory', 'email', 'wiki', 'module_snapshot', 'social_post')


def _pick_salience_calibration_memory(conn) -> dict | None:
    """Yesterday's highest-salience memory. 'Yesterday' = the last
    24 hours, rolling — not a calendar boundary, so a run slightly
    after midnight doesn't miss the previous day.
    """
    try:
        types_sql = ','.join(['%s'] * len(_MEMORY_CHUNK_TYPES))
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT id, chunk_content, salience, salience_signals,
                           indexed_at
                      FROM claw_code_chunks
                     WHERE chunk_type IN ({types_sql})
                       AND salience > 3.0
                       AND indexed_at > NOW() - INTERVAL '36 hours'
                     ORDER BY salience DESC, indexed_at DESC
                     LIMIT 1""",
                _MEMORY_CHUNK_TYPES,
            )
            row = cur.fetchone()
    except Exception as exc:
        logger.warning('[brief] salience_calibration query failed: %s', exc)
        return None
    if not row:
        return None
    content = str(row[1] or '').strip()
    snippet = (content[:200] + '…') if len(content) > 200 else content
    return {
        'memory_id': int(row[0]),
        'memory_snippet': snippet,
        'salience': float(row[2] or 0.0),
        'signals': row[3] or {},
        'memory_date': row[4].strftime('%Y-%m-%d') if row[4] else '',
    }


def _format_signal_block(signals: dict) -> str:
    """Render salience signals as compact one-liner like
    `money 0.42 · pushback 0.4 · outcome 1.0`."""
    if not signals:
        return '(no signal breakdown)'
    parts: list[str] = []
    for key, val in signals.items():
        try:
            if float(val) > 0:
                short = key.replace('outcome_weight', 'outcome')
                short = short.replace('customer_pushback', 'pushback')
                parts.append(f'{short} {float(val):.2f}')
        except Exception:
            continue
    return ' · '.join(parts) if parts else '(all zero)'


# ── Category builders ────────────────────────────────────────────────

def _build_belief_audit(conn, templates: dict) -> Question | None:
    picked = _pick_belief_audit_schema(conn)
    if not picked:
        return None
    try:
        prompt, reply = _render(
            templates.get('belief_audit'),
            schema_text=picked['schema_text'],
            schema_age_days=picked['schema_age_days'],
            access_count=picked['access_count'],
            confidence=picked['confidence'],
        )
    except Exception as exc:
        logger.warning('[brief] belief_audit render failed: %s', exc)
        return None
    return Question(
        category='belief_audit',
        prompt=prompt,
        reply_format=reply,
        provenance={
            'schema_id': picked['schema_id'],
            'access_count': picked['access_count'],
        },
    )


def _build_gist_validation(conn, templates: dict) -> Question | None:
    picked = _pick_gist_validation_schema(conn)
    if not picked:
        return None
    try:
        prompt, reply = _render(
            templates.get('gist_validation'),
            schema_text=picked['schema_text'],
            schema_age_days=picked['schema_age_days'],
            source_count=picked['source_count'],
            confidence=picked['confidence'],
        )
    except Exception as exc:
        logger.warning('[brief] gist_validation render failed: %s', exc)
        return None
    return Question(
        category='gist_validation',
        prompt=prompt,
        reply_format=reply,
        provenance={
            'schema_id': picked['schema_id'],
            'confidence': picked['confidence'],
        },
    )


def _build_salience_calibration(conn, templates: dict) -> Question | None:
    picked = _pick_salience_calibration_memory(conn)
    if not picked:
        return None
    try:
        prompt, reply = _render(
            templates.get('salience_calibration'),
            memory_snippet=picked['memory_snippet'],
            salience=picked['salience'],
            memory_date=picked['memory_date'],
            signal_block=_format_signal_block(picked['signals']),
        )
    except Exception as exc:
        logger.warning('[brief] salience_calibration render failed: %s', exc)
        return None
    return Question(
        category='salience_calibration',
        prompt=prompt,
        reply_format=reply,
        provenance={
            'memory_id': picked['memory_id'],
            'salience': picked['salience'],
        },
    )


def _build_research_prompt(conn) -> 'Question | None':
    """arXiv research-loop Stage 2 — surface the top un-surfaced
    candidate as a research_prompt question. Returns None when
    the queue is empty or no candidate clears the threshold.

    Marks the chosen candidate as surfaced_at so it doesn't repeat
    in tomorrow's brief.
    """
    try:
        from core.research.arxiv_loop import pick_next_candidate, mark_surfaced
    except Exception:
        return None
    cand = pick_next_candidate(conn)
    if not cand:
        return None

    title = cand.get('title') or '(untitled)'
    reason = cand.get('applicability_reason') or ''
    score = cand.get('applicability_score') or 0.0
    arxiv_id = cand.get('arxiv_id') or ''
    pdf_url = cand.get('pdf_url') or ''

    prompt = (
        f'RESEARCH — applicability {score:.1f}/10\n\n'
        f'A paper that might be worth a closer look:\n\n'
        f'  {title}\n'
        f'  arXiv: {arxiv_id}\n'
        f'  {pdf_url}\n\n'
        f'Why it came up: {reason}\n\n'
        f'Worth a deeper look?\n'
        f'Reply: YES / NO / LATER'
    )
    q = Question(
        category='research_prompt',
        prompt=prompt,
        reply_format='YES / NO / LATER',
        provenance={
            'arxiv_candidate_id': cand['id'],
            'arxiv_id': arxiv_id,
            'applicability_score': score,
        },
    )
    # Mark surfaced NOW so the SAME paper doesn't appear in two
    # briefs (e.g. if Jo's run fires a moment after Toby's).
    try:
        mark_surfaced(conn, cand['id'])
    except Exception:
        pass
    return q


def _build_self_prompt(
    templates: dict, category: str, user_email: str,
) -> 'Question | None':
    """Build a self-prompt category (HR / finance / D2C / production /
    equipment / tech-solve etc.) — no DB query, just renders the
    template prompt with optional placeholders.

    Templates can include a ``role_tag`` field that gets propagated
    into provenance so reply persistence can scope the resulting
    memory chunks by domain.
    """
    template_body = templates.get(category)
    if not template_body or 'prompt' not in template_body:
        return None
    try:
        prompt = str(template_body['prompt']).strip()
        reply_format = str(template_body.get('reply_format') or 'Free text')
    except Exception:
        return None
    role_tag = template_body.get('role_tag') or category
    return Question(
        category=category,
        prompt=prompt,
        reply_format=reply_format,
        provenance={'role_tag': role_tag, 'source': 'self_prompt'},
    )


def _open_ended_override(user_email: str) -> str | None:
    """Look up the role-scoped open-ended prompt for this user, if
    any. Returns None for default (director-tier) recipients."""
    try:
        from .user_profile import get_profile
        return get_profile(user_email).open_ended_prompt
    except Exception:
        return None


def _build_open_ended(
    templates: dict, override_prompt: str | None = None,
) -> Question:
    """Always returned — no DB query. Fallback-safe in case the
    template file itself is broken: we hard-code a minimal version.

    ``override_prompt`` comes from the recipient's user profile
    (config/brief/user_profiles.yaml) when set — lets tier-2
    users like Jo + Ivan get a role-scoped open prompt without
    touching the templates file.
    """
    if override_prompt:
        return Question(
            category='open_ended',
            prompt=f'OPEN —\n\n{override_prompt.strip()}',
            reply_format='Free text (one or two sentences)',
            provenance={'source': 'user_profile_override'},
        )
    try:
        prompt, reply = _render(templates.get('open_ended'))
    except Exception:
        prompt = (
            'OPEN —\n\n'
            'One thing from yesterday worth remembering long-term.\n\n'
            'Reply: (free text)'
        )
        reply = 'Free text (one or two sentences)'
    return Question(
        category='open_ended', prompt=prompt, reply_format=reply,
        provenance={},
    )


# ── Public entry ──────────────────────────────────────────────────────

@dataclass
class QuestionSet:
    user_email: str
    generated_at: datetime
    questions: list[Question]
    notes: list[str] = field(default_factory=list)   # why categories skipped


def generate_questions(user_email: str) -> QuestionSet:
    """Generate the day's question set for one user.

    Never raises. If DB is unreachable, returns a set containing
    only the open-ended question (which has no DB dependency) plus
    a note explaining why.

    User profiles can override the default tier-1 mix via the
    ``question_categories`` field — used for tier-2 users (Jo,
    Ivan) whose briefs are built around their actual remit
    rather than Deek's memory-state introspection.
    """
    templates = _load_templates()
    questions: list[Question] = []
    notes: list[str] = []

    # Profile-driven category override — tier-2 path
    try:
        from .user_profile import get_profile
        profile_categories = get_profile(user_email).question_categories
    except Exception:
        profile_categories = None

    if profile_categories:
        for cat in profile_categories:
            if cat == 'open_ended':
                questions.append(_build_open_ended(
                    templates, _open_ended_override(user_email),
                ))
            else:
                q = _build_self_prompt(templates, cat, user_email)
                if q:
                    questions.append(q)
                else:
                    notes.append(f'{cat}: template missing')
        return QuestionSet(
            user_email=user_email,
            generated_at=datetime.now(timezone.utc),
            questions=questions,
            notes=notes,
        )

    try:
        conn = _connect()
    except Exception as exc:
        notes.append(f'db unreachable ({type(exc).__name__}); open-ended only')
        questions.append(_build_open_ended(templates, _open_ended_override(user_email)))
        return QuestionSet(
            user_email=user_email,
            generated_at=datetime.now(timezone.utc),
            questions=questions,
            notes=notes,
        )

    try:
        q = _build_belief_audit(conn, templates)
        if q:
            questions.append(q)
        else:
            notes.append('belief_audit: no eligible low-access schemas')

        q = _build_gist_validation(conn, templates)
        if q:
            questions.append(q)
        else:
            notes.append('gist_validation: no schemas in 7-30d / conf 0.7-0.9 band')

        q = _build_salience_calibration(conn, templates)
        if q:
            questions.append(q)
        else:
            notes.append('salience_calibration: no yesterday salience > 3.0')
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # arXiv research-loop Stage 2: if we have an un-surfaced
    # high-applicability paper, add a research_prompt question.
    # Fires opportunistically — no question if the queue is empty
    # or the top candidate is below the surface threshold. Only
    # applies to tier-1 (director) users so tier-2 replies stay
    # role-scoped.
    try:
        _role = None
        try:
            from .user_profile import get_profile
            _role = get_profile(user_email).role
        except Exception:
            _role = 'director'
        if _role == 'director':
            research_q = _build_research_prompt(conn)
            if research_q is not None:
                questions.append(research_q)
    except Exception as exc:
        notes.append(f'research_prompt: {type(exc).__name__}')

    questions.append(_build_open_ended(templates, _open_ended_override(user_email)))

    return QuestionSet(
        user_email=user_email,
        generated_at=datetime.now(timezone.utc),
        questions=questions,
        notes=notes,
    )


__all__ = ['Question', 'QuestionSet', 'generate_questions']
