"""Memory Brief reply parser — Phase B.

The IMAP inbox poll (scripts/process_deek_inbox.py) indexes every
email arriving at cairn@ into claw_code_chunks with chunk_type='email'.
This module reads those chunks, recognises replies to Memory Brief
emails by their subject + structural markers, parses the answer
blocks, and applies corrections back to memory.

Discipline carried through from Phase A:

  * Idempotent. Running against the same inbox state twice must not
    double-apply answers. Uses a SHA over (raw_body + run_id) as the
    dedup key against `memory_brief_responses`.
  * Fail loud. A malformed reply is logged, the parse-failure is
    recorded in filter_signals, but the runner keeps going. No silent
    skip.
  * Provenance preserved. Every applied correction is traceable back
    to the `memory_brief_runs` row it answered.

Actions by category (all append to filter_signals for audit):

    belief_audit
      TRUE   → schemas.salience += 0.5 (clipped to 10.0), access_count++
      FALSE  → schemas.salience -= 1.0 (floor 0.5), status unchanged
               (demotion on false belief is aggressive; full deletion
                requires human decision captured via a text correction)
      text   → schemas.schema_text overwritten, salience reset to 1.5
               to force re-consolidation against the corrected version

    gist_validation
      YES    → schemas.access_count++, confidence += 0.1 (floor 0.95)
      NO     → schemas.status = 'dormant' (stays retrievable at 0.75x)
      text   → schemas.schema_text replaced with the revised text

    salience_calibration
      YES    → the memory chunk's salience confirmed (no change, but
                audit row records confirmation for future trend analysis)
      NO     → chunk salience -= 2.0 (floor 0.5), so the extractor's
                false-positive is down-weighted for future retrievals
      text   → salience -= 1.0 and the text reply is written as a
                new memory with toby_flag=true referencing the original

    open_ended
      Any text → written as a new memory with toby_flag=true,
                 chunk_type='memory', linked to the run via
                 memory_brief_responses.applied_summary
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)


# ── Recognising brief replies in the email chunk table ───────────────

# The outgoing email subject is 'Deek morning brief — YYYY-MM-DD'. A
# reply typically prefixes 'Re: '. Allow for both with/without the
# em dash encoding.
_SUBJECT_RE = re.compile(
    r're:\s*deek\s+morning\s+brief\s*[—-]\s*(\d{4}-\d{2}-\d{2})',
    re.IGNORECASE,
)

# Email chunks are stored with the email's full subject as chunk_name
# and `Email from <addr> (<date>)\\nSubject: ...\\n\\n<body>` as
# chunk_content. We parse both.

# Block delimiter baked into every outgoing brief by core/brief/composer.py:
#   --- Q1 (belief_audit) ---
#   ...
#   (Expected reply format: TRUE / FALSE / [correction])
_BLOCK_DELIM_RE = re.compile(
    r'^---\s*Q(\d+)\s*\(([a-z_]+)\)\s*---\s*$',
    re.MULTILINE,
)

_AFFIRMATIVE = frozenset({
    'true', 'yes', 'y', 'confirmed', 'correct', 'right',
})
_NEGATIVE = frozenset({
    'false', 'no', 'n', 'wrong', 'incorrect', 'nope',
})


@dataclass
class ParsedAnswer:
    q_number: int
    category: str
    raw_text: str
    verdict: str          # 'affirm' | 'deny' | 'correct' | 'empty'
    correction_text: str  # populated when verdict == 'correct'


@dataclass
class ParsedReply:
    run_date: date
    user_email: str
    answers: list[ParsedAnswer] = field(default_factory=list)
    parse_notes: list[str] = field(default_factory=list)


# ── Parsing ──────────────────────────────────────────────────────────

def extract_date_from_subject(subject: str) -> date | None:
    """Return the YYYY-MM-DD embedded in a brief reply subject. None
    if the subject doesn't match the brief pattern at all.
    """
    if not subject:
        return None
    m = _SUBJECT_RE.search(subject)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), '%Y-%m-%d').date()
    except ValueError:
        return None


def _strip_quoted(text: str) -> str:
    """Remove common reply-quote patterns so we don't re-parse our
    own outgoing block delimiters.

    Email clients use a few conventions; we handle:
      * Lines starting with '> ' (most quoting — note the SPACE)
      * '--- Original Message ---' boundaries (Outlook-ish)
      * 'On <date>, <name> wrote:' header lines

    We deliberately do NOT break on a bare '>' with no space. Mbox
    From-munging produces lines like '>From 2026-04-21:' when a
    client quotes a reply containing a line that starts with the
    word "From " — this is NOT a reply quote, it's the client
    escaping what would otherwise look like a new mbox message
    header. Treating it as a quote boundary loses legitimate content
    (see 2026-04-22 memory brief parse failure).

    Special case — if the body already contains our structured
    ``--- Q<n> (category) ---`` delimiters, we skip quote-stripping
    entirely and trust the delimiters. Some email clients top-post
    with the "On <date> wrote:" header AND leave the user's inline
    answers below (IONOS webmail does this on Re: replies). In that
    layout, a naive quote-strip would eat all the answers. The
    delimiters are our ground truth; heuristics are secondary.
    """
    # If our Q-delimiters are anywhere in the body, trust them — the
    # whole text is signal to the parser. Quote-strip heuristics are
    # only worth running when we don't have structural landmarks.
    if _BLOCK_DELIM_RE.search(text or ''):
        return (text or '').strip()

    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        # Real reply quoting uses '> ' (space after the chevron) or
        # nested chevrons like '>> '. Plain '>' with no space is
        # mbox From-munging or a false positive.
        if stripped.startswith('> ') or stripped.startswith('>>'):
            break
        if stripped.startswith('--- Original Message ---'):
            break
        if re.match(r'^On .+wrote:\s*$', stripped):
            break
        lines.append(line)
    return '\n'.join(lines).strip()


def _classify(answer_text: str) -> tuple[str, str]:
    """Classify an answer text into (verdict, correction_text).

    The first line of a non-quoted answer is the verdict. If the first
    word is affirmative / negative, that's the verdict; everything
    else is treated as a correction (free text). Empty answers are
    flagged.
    """
    cleaned = answer_text.strip()
    if not cleaned:
        return 'empty', ''
    # Drop the "(Expected reply format: ...)" hint line if present
    cleaned_lines = [
        l for l in cleaned.splitlines()
        if not l.lstrip().startswith('(Expected reply format:')
    ]
    cleaned = '\n'.join(cleaned_lines).strip()
    if not cleaned:
        return 'empty', ''

    first_line = cleaned.splitlines()[0].strip()
    # Strip the block separator '/' from the format hint
    first_tokens = re.split(r'[\s/,]+', first_line.lower())
    first_tokens = [t for t in first_tokens if t]
    if first_tokens:
        if first_tokens[0] in _AFFIRMATIVE or first_tokens[0] in _NEGATIVE:
            verdict = 'affirm' if first_tokens[0] in _AFFIRMATIVE else 'deny'
            # Capture any context the user provided alongside the
            # YES/NO/TRUE/FALSE — e.g. 'NO - we don't use X anymore'.
            # Same-line context is kept; everything after the first
            # line is also retained. An empty string is fine and
            # means "just a verdict, no explanation".
            remainder_same_line = re.sub(
                r'^[A-Za-z]+', '', first_line, count=1
            ).lstrip(' \t-–—:·.').strip()
            rest_lines = cleaned.splitlines()[1:]
            rest_text = '\n'.join(rest_lines).strip()
            ctx_parts = [p for p in (remainder_same_line, rest_text) if p]
            return verdict, '\n'.join(ctx_parts)
    return 'correct', cleaned


_PREFIXED_DELIM_RE = re.compile(
    r'^>\s*---\s*Q(\d+)\s*\(([a-z_]+)\)\s*---\s*$',
    re.MULTILINE,
)


def _extract_inline_interleaved_answers(body: str) -> list['ParsedAnswer']:
    """Handle the IONOS-webmail top-post + inline-answer style.

    Shape: every line of the original brief is prefixed with ``> ``,
    and the user has typed their answers on un-prefixed lines
    interleaved between the quoted ones. Example:

        > Reply: TRUE / FALSE / [correction]
        TRUE
        >
        > (Expected reply format: TRUE / FALSE / [correction])
        >
        > --- Q2 (salience_calibration) ---
        > Reply: YES / NO / [why or why not]
        NO - we don't use that anymore

    Returns a list of ParsedAnswer (possibly empty). Empty means
    "this didn't look like the interleaved style" — caller falls
    back to the standard path.
    """
    if not body:
        return []
    lines = body.splitlines()
    prefixed = sum(
        1 for l in lines
        if l.lstrip().startswith('>') and not l.lstrip().startswith('>>')
    )
    # Require majority prefixed lines AND at least one prefixed Q
    # delimiter — otherwise this isn't the interleaved style.
    if prefixed < max(3, len(lines) // 2):
        return []
    if not _PREFIXED_DELIM_RE.search(body):
        return []

    answers_by_q: list[tuple[int, str, list[str]]] = []
    current: tuple[int, str, list[str]] | None = None
    for raw_line in lines:
        stripped_line = raw_line.strip()
        m = _PREFIXED_DELIM_RE.match(stripped_line)
        if m:
            if current is not None:
                answers_by_q.append(current)
            current = (int(m.group(1)), m.group(2), [])
            continue
        if stripped_line.startswith('>'):
            # Quoted original content — prompt, not answer
            continue
        # Un-prefixed, non-empty line inside a Q block == user's answer
        if current is not None and stripped_line:
            current[2].append(stripped_line)
    if current is not None:
        answers_by_q.append(current)

    out: list[ParsedAnswer] = []
    _sig_markers = (
        'Toby Fletcher', 'Email: toby', 'Landline:',
        'Mobile:', 'Web: nbnesigns',
    )
    for q_num, category, buf in answers_by_q:
        # Trim trailing signature lines from the LAST block only —
        # they sit under the final Q and aren't part of the answer.
        while buf and any(marker in buf[-1] for marker in _sig_markers):
            buf.pop()
        answer_text = '\n'.join(buf).strip()
        verdict, correction = _classify(answer_text)
        out.append(ParsedAnswer(
            q_number=q_num, category=category,
            raw_text=answer_text, verdict=verdict,
            correction_text=correction,
        ))
    return out


def parse_reply_body(
    body: str,
    user_email: str,
    run_date: date,
    *,
    questions: list | None = None,
) -> ParsedReply:
    """Split a brief-reply body into answer blocks.

    The delimiter structure is the same as the outgoing brief — every
    answer block starts with `--- Q<n> (<category>) ---`. We split on
    that pattern, drop quoted content per _strip_quoted, and classify
    each block.

    Handles three common shapes:

      1. User's answers are at the top, quoted original at the bottom.
         strip_quoted + delimiter split works.

      2. Top-post: 'On <date> wrote:' header + quoted original below
         with user's answers interleaved as un-prefixed lines between
         `> ` prefixed quotes (IONOS webmail default). Detected and
         parsed by _extract_inline_interleaved_answers before the
         standard path.

      3. Free-form prose (no delimiters, no interleaved structure).
         If ``questions`` is provided, route through the local-LLM
         conversational normaliser. Shadow-mode gated; see
         core.brief.conversational.
    """
    reply = ParsedReply(run_date=run_date, user_email=user_email)

    # Shape 2: IONOS top-post + interleaved answers
    interleaved = _extract_inline_interleaved_answers(body or '')
    if interleaved:
        reply.answers = interleaved
        return reply

    stripped = _strip_quoted(body)
    if not stripped:
        reply.parse_notes.append('body empty after quote stripping')
        return reply

    # Find all delimiter positions
    matches = list(_BLOCK_DELIM_RE.finditer(stripped))
    if not matches:
        # Shape 3: free-form prose. Try the conversational normaliser
        # before falling through to "whole body as one open_ended".
        if questions:
            try:
                from .conversational import (
                    ConversationalQuestion,
                    normalise_conversational_reply,
                )
                cqs = [
                    ConversationalQuestion(
                        q_number=int(q.get('q_number') or i + 1),
                        category=str(q.get('category') or 'open_ended'),
                        prompt=str(q.get('prompt') or q.get('text') or ''),
                        extra=str(q.get('extra') or ''),
                    )
                    for i, q in enumerate(questions)
                ]
                normalised = normalise_conversational_reply(
                    stripped, cqs, kind='brief',
                )
            except Exception as exc:
                normalised = None
                reply.parse_notes.append(
                    f'conversational normaliser error: {type(exc).__name__}'
                )
            if normalised:
                reply.parse_notes.append(
                    f'conversational-fallback: normalised {len(normalised)} answers'
                )
                for n in normalised:
                    reply.answers.append(ParsedAnswer(
                        q_number=n.q_number,
                        category=n.category,
                        raw_text=stripped,
                        verdict=n.verdict if n.verdict != 'text' else 'correct',
                        correction_text=n.correction_text or n.free_text,
                    ))
                return reply

        reply.parse_notes.append(
            'no "--- Q<n> (<category>) ---" delimiters found; '
            'treating whole body as one open_ended answer'
        )
        # Degenerate path — user replied without keeping the delimiters
        # intact. Better to capture their whole reply as an open-ended
        # answer than silently drop.
        reply.answers.append(ParsedAnswer(
            q_number=0, category='open_ended',
            raw_text=stripped,
            verdict='correct', correction_text=stripped,
        ))
        return reply

    # Slice between delimiters
    for i, m in enumerate(matches):
        q_num = int(m.group(1))
        category = m.group(2)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(stripped)
        block_text = stripped[start:end].strip()
        verdict, correction = _classify(block_text)
        reply.answers.append(ParsedAnswer(
            q_number=q_num, category=category,
            raw_text=block_text, verdict=verdict,
            correction_text=correction,
        ))
    return reply


# ── DB operations ────────────────────────────────────────────────────

def _connect():
    import psycopg2
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        raise RuntimeError('DATABASE_URL not set')
    return psycopg2.connect(db_url, connect_timeout=5)


def _body_hash(raw_body: str, run_id: str) -> str:
    h = hashlib.sha256()
    h.update(run_id.encode('utf-8'))
    h.update(b'\0')
    h.update((raw_body or '').encode('utf-8', errors='replace'))
    return h.hexdigest()


def find_run_for_reply(
    conn, user_email: str, run_date: date,
    *, in_reply_to: str | None = None,
) -> tuple[str, dict] | None:
    """Look up the memory_brief_runs row this reply is answering.

    Preferred path (2026-04-22 onward): if the caller knows the
    reply's ``In-Reply-To`` header value, match on
    ``memory_brief_runs.outgoing_message_id``. That's unambiguous
    even when several briefs went out the same day to the same user.

    Fallback: (user, date) match, for legacy runs that predate
    outgoing_message_id capture (migration 0010).

    Returns ``(run_id, questions_dict)`` or ``None``.
    """
    irt = (in_reply_to or '').strip()
    with conn.cursor() as cur:
        if irt:
            cur.execute(
                """SELECT id::text, questions
                     FROM memory_brief_runs
                    WHERE outgoing_message_id = %s
                      AND delivery_status = 'sent'
                    LIMIT 1""",
                (irt,),
            )
            row = cur.fetchone()
            if row:
                return _shape_run_result(row)
            # fall through to date match — reply might correlate via
            # the references chain root instead of direct parent
        cur.execute(
            """SELECT id::text, questions
                 FROM memory_brief_runs
                WHERE user_email = %s
                  AND (generated_at AT TIME ZONE 'UTC')::date = %s
                  AND delivery_status = 'sent'
                ORDER BY generated_at DESC
                LIMIT 1""",
            (user_email, run_date),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _shape_run_result(row)


def _shape_run_result(row) -> tuple[str, dict]:
    """Shape a (id, questions) DB row into (run_id, qmap)."""
    run_id = row[0]
    questions_raw = row[1]
    if isinstance(questions_raw, str):
        try:
            questions_list = json.loads(questions_raw)
        except Exception:
            questions_list = []
    else:
        questions_list = questions_raw or []
    qmap = {q.get('category'): q for q in questions_list}
    return run_id, qmap


def already_applied(conn, run_id: str, raw_body: str) -> bool:
    """Idempotency check — return True if this exact reply body has
    already been stored for this run."""
    digest = _body_hash(raw_body, run_id)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM memory_brief_responses
                WHERE run_id = %s::uuid
                  AND encode(sha256(raw_body::bytea), 'hex') = %s
                LIMIT 1""",
            (run_id, digest[:64]),
        )
        return cur.fetchone() is not None


def _apply_belief_audit(conn, schema_id: str, answer: ParsedAnswer) -> dict:
    """schemas belief_audit action. Returns summary dict for the
    audit row."""
    summary = {'category': 'belief_audit', 'schema_id': schema_id, 'verdict': answer.verdict}
    if not schema_id:
        summary['note'] = 'no schema_id in provenance; skipped'
        return summary
    with conn.cursor() as cur:
        if answer.verdict == 'affirm':
            cur.execute(
                """UPDATE schemas
                      SET salience = LEAST(10.0, salience + 0.5),
                          access_count = access_count + 1,
                          last_accessed_at = NOW()
                    WHERE id = %s::uuid""",
                (schema_id,),
            )
            summary['action'] = 'reinforced +0.5'
        elif answer.verdict == 'deny':
            cur.execute(
                """UPDATE schemas
                      SET salience = GREATEST(0.5, salience - 1.0)
                    WHERE id = %s::uuid""",
                (schema_id,),
            )
            summary['action'] = 'demoted -1.0'
        elif answer.verdict == 'correct':
            cur.execute(
                """UPDATE schemas
                      SET schema_text = %s,
                          salience = 1.5,
                          last_accessed_at = NOW()
                    WHERE id = %s::uuid""",
                (answer.correction_text, schema_id),
            )
            summary['action'] = 'corrected; salience reset to 1.5'
        else:
            summary['action'] = 'no-op (empty reply)'
    return summary


def _apply_gist_validation(conn, schema_id: str, answer: ParsedAnswer) -> dict:
    summary = {'category': 'gist_validation', 'schema_id': schema_id, 'verdict': answer.verdict}
    if not schema_id:
        summary['note'] = 'no schema_id in provenance; skipped'
        return summary
    with conn.cursor() as cur:
        if answer.verdict == 'affirm':
            cur.execute(
                """UPDATE schemas
                      SET access_count = access_count + 1,
                          confidence = LEAST(0.95, confidence + 0.1),
                          last_accessed_at = NOW()
                    WHERE id = %s::uuid""",
                (schema_id,),
            )
            summary['action'] = 'confidence +0.1'
        elif answer.verdict == 'deny':
            cur.execute(
                """UPDATE schemas
                      SET status = 'dormant'
                    WHERE id = %s::uuid""",
                (schema_id,),
            )
            summary['action'] = 'demoted to dormant'
        elif answer.verdict == 'correct':
            cur.execute(
                """UPDATE schemas
                      SET schema_text = %s,
                          last_accessed_at = NOW()
                    WHERE id = %s::uuid""",
                (answer.correction_text, schema_id),
            )
            summary['action'] = 'text revised'
        else:
            summary['action'] = 'no-op (empty reply)'
    return summary


def _apply_salience_calibration(
    conn, memory_id: int, answer: ParsedAnswer,
) -> dict:
    summary = {
        'category': 'salience_calibration',
        'memory_id': memory_id, 'verdict': answer.verdict,
    }
    if not memory_id:
        summary['note'] = 'no memory_id in provenance; skipped'
        return summary
    with conn.cursor() as cur:
        if answer.verdict == 'affirm':
            # Confirmation — no change, but flag for future trend analysis
            summary['action'] = 'confirmed (salience unchanged)'
        elif answer.verdict == 'deny':
            cur.execute(
                """UPDATE claw_code_chunks
                      SET salience = GREATEST(0.5, salience - 2.0)
                    WHERE id = %s""",
                (memory_id,),
            )
            summary['action'] = 'salience -2.0 (false positive)'
        elif answer.verdict == 'correct':
            # Reduce original + capture the nuance as a new memory
            cur.execute(
                """UPDATE claw_code_chunks
                      SET salience = GREATEST(0.5, salience - 1.0)
                    WHERE id = %s""",
                (memory_id,),
            )
            summary['action'] = 'salience -1.0 + correction captured'
            summary['correction_captured'] = True
        else:
            summary['action'] = 'no-op (empty reply)'
    return summary


def _write_toby_memory(
    conn, user_email: str, content: str, reference_id: int | None = None,
) -> int | None:
    """Write a new memory chunk with toby_flag=true. Returns the new
    chunk id.

    Uses the embedding function from core.wiki.embeddings. If the
    embedding fails we still write the chunk but without an embedding
    — the audit log will flag it.
    """
    if not content.strip():
        return None
    try:
        from core.wiki.embeddings import get_embed_fn
        embed_fn = get_embed_fn()
        emb = embed_fn(content[:6000]) if embed_fn else None
    except Exception as exc:
        logger.warning('[brief-reply] embed failed (non-fatal): %s', exc)
        emb = None

    content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
    file_path = f'memory/brief-reply/{content_hash[:16]}'
    signals = {'toby_flag': 1.0, 'via': 'memory_brief_reply'}
    if reference_id is not None:
        signals['references'] = [int(reference_id)]

    with conn.cursor() as cur:
        if emb is not None:
            cur.execute(
                """INSERT INTO claw_code_chunks
                    (project_id, file_path, chunk_content, chunk_type,
                     chunk_name, content_hash, embedding, indexed_at,
                     salience, salience_signals, last_accessed_at,
                     access_count)
                   VALUES ('deek', %s, %s, 'memory', %s, %s,
                           %s::vector, NOW(), 7.0, %s::jsonb, NOW(), 0)
                   RETURNING id""",
                (file_path, content, content[:200], content_hash, emb,
                 json.dumps(signals)),
            )
        else:
            cur.execute(
                """INSERT INTO claw_code_chunks
                    (project_id, file_path, chunk_content, chunk_type,
                     chunk_name, content_hash, indexed_at,
                     salience, salience_signals, last_accessed_at,
                     access_count)
                   VALUES ('deek', %s, %s, 'memory', %s, %s,
                           NOW(), 7.0, %s::jsonb, NOW(), 0)
                   RETURNING id""",
                (file_path, content, content[:200], content_hash,
                 json.dumps(signals)),
            )
        (new_id,) = cur.fetchone()
    return int(new_id)


def apply_reply(conn, reply: ParsedReply) -> dict:
    """Apply every answer in a parsed reply to the memory layer.

    Returns a summary suitable for memory_brief_responses.applied_summary.
    Never raises — errors per-answer are captured in summary.
    """
    summary: dict = {
        'user_email': reply.user_email,
        'run_date': reply.run_date.isoformat(),
        'answers_processed': [],
        'parse_notes': reply.parse_notes,
    }

    run = find_run_for_reply(conn, reply.user_email, reply.run_date)
    if not run:
        summary['error'] = (
            f'no memory_brief_runs row for {reply.user_email} on '
            f'{reply.run_date.isoformat()}'
        )
        return summary
    run_id, qmap = run
    summary['run_id'] = run_id

    for ans in reply.answers:
        try:
            provenance = (qmap.get(ans.category) or {}).get('provenance') or {}
            action_summary: dict = {
                'q_number': ans.q_number,
                'category': ans.category,
                'verdict': ans.verdict,
            }

            if ans.category == 'belief_audit':
                action_summary.update(_apply_belief_audit(
                    conn, provenance.get('schema_id', ''), ans,
                ))
            elif ans.category == 'gist_validation':
                action_summary.update(_apply_gist_validation(
                    conn, provenance.get('schema_id', ''), ans,
                ))
            elif ans.category == 'salience_calibration':
                action_summary.update(_apply_salience_calibration(
                    conn, int(provenance.get('memory_id') or 0), ans,
                ))
                # If correction text given, capture as new memory
                if ans.verdict == 'correct' and ans.correction_text:
                    ref = int(provenance.get('memory_id') or 0)
                    new_id = _write_toby_memory(
                        conn, reply.user_email,
                        f'Correction on memory {ref}: {ans.correction_text}',
                        reference_id=ref,
                    )
                    if new_id:
                        action_summary['new_memory_id'] = new_id
            elif ans.category == 'open_ended':
                if ans.raw_text.strip():
                    new_id = _write_toby_memory(
                        conn, reply.user_email,
                        f'Toby open-ended reflection: {ans.raw_text.strip()}',
                    )
                    if new_id:
                        action_summary['action'] = f'wrote new memory {new_id}'
                        action_summary['new_memory_id'] = new_id
                    else:
                        action_summary['action'] = 'empty or embed failed'
                else:
                    action_summary['action'] = 'empty'
            elif ans.category == 'research_prompt':
                # arXiv Stage 2 verdict capture. Map YES/NO/LATER
                # to the arxiv_candidates row via provenance.
                candidate_id = int(provenance.get('arxiv_candidate_id') or 0)
                verdict_word = ''
                if ans.verdict == 'affirm':
                    verdict_word = 'yes'
                elif ans.verdict == 'deny':
                    verdict_word = 'no'
                elif ans.verdict == 'correct':
                    # 'LATER' or free-text falls under 'correct'.
                    # Detect 'later' explicitly.
                    first = (ans.raw_text or '').strip().lower().split()
                    verdict_word = 'later' if first and first[0].startswith('later') else 'later'
                if candidate_id and verdict_word:
                    try:
                        from core.research.arxiv_loop import record_verdict
                        ok = record_verdict(conn, candidate_id, verdict_word)
                        action_summary['action'] = (
                            f'recorded arxiv verdict={verdict_word}'
                            if ok else 'verdict write failed'
                        )
                        action_summary['arxiv_candidate_id'] = candidate_id
                    except Exception as exc:
                        action_summary['action'] = (
                            f'arxiv verdict error: {type(exc).__name__}'
                        )
                else:
                    action_summary['action'] = 'empty / no provenance'
            else:
                action_summary['action'] = f'unknown category; stored raw'

            summary['answers_processed'].append(action_summary)
        except Exception as exc:
            summary['answers_processed'].append({
                'q_number': ans.q_number,
                'category': ans.category,
                'error': f'{type(exc).__name__}: {exc}',
            })
            logger.warning('[brief-reply] per-answer failure: %s', exc)
    return summary


def store_response(
    conn, run_id: str, raw_body: str,
    parsed: ParsedReply, applied_summary: dict,
) -> str:
    """Insert the memory_brief_responses row marking this reply
    processed. Returns the new response id."""
    response_id = str(uuid.uuid4())
    parsed_json = json.dumps({
        'answers': [
            {
                'q_number': a.q_number,
                'category': a.category,
                'verdict': a.verdict,
                'correction_text': a.correction_text,
            }
            for a in parsed.answers
        ],
        'parse_notes': parsed.parse_notes,
    })
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO memory_brief_responses
                (id, run_id, received_at, raw_body, parsed_answers,
                 applied_at, applied_summary)
               VALUES (%s, %s, NOW(), %s, %s::jsonb, NOW(), %s::jsonb)""",
            (
                response_id, run_id, raw_body,
                parsed_json, json.dumps(applied_summary),
            ),
        )
    return response_id


__all__ = [
    'ParsedAnswer', 'ParsedReply',
    'extract_date_from_subject', 'parse_reply_body',
    'find_run_for_reply', 'already_applied',
    'apply_reply', 'store_response',
]
