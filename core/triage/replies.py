"""Triage digest reply parser + apply — Phase B.

When Toby replies to a triage digest, the inbox poll indexes the
reply into `claw_code_chunks` (chunk_type='email'). This module
recognises those replies by subject, parses the 4-question answer
block, and applies the actions back to:

  * `cairn_intel.email_triage` — update confirmed project, stash
    approved/edited draft, record project folder path, mark review
  * CRM via `POST /api/cairn/memory` — add a note on the confirmed
    project with the approved reply
  * `claw_code_chunks` — write a new memory chunk with
    toby_flag=true for free-text notes and edit corrections

Categories:

    match_confirm
      YES              → keep candidate 1 as the confirmed project
      NO               → demote (clear confirmed project; log for
                           Phase C/D to surface alternatives later)
      "1"|"2"|"3"      → swap confirmed project to that candidate
      text             → free-text → open issue, logged; don't guess

    reply_approval
      USE              → treat draft_reply as the final approved text
      EDIT: <text>     → override draft with the edited text
      REJECT           → record that the draft was not usable; no
                           CRM note is posted (we don't want to paste
                           a rejected draft into the project history)
      text (no verb)   → treat as an EDIT: … with the whole text

    project_folder
      path string      → store in project_folder_path + CRM note
      empty            → no-op

    notes
      text             → write to claw_code_chunks with toby_flag=true,
                           link via provenance to the triage row

Idempotency via SHA over (raw_body + triage_id). Re-running against
the same inbox state is a no-op. Parse failures are logged and
captured in review_notes; the row still gets marked reviewed so
future runs don't retry the same bad reply forever.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# Subject pattern on the OUTGOING digest is
#   [Deek] {classification} — {original_subject}
# A reply prefixes with Re: / RE: / FW: etc. We're deliberately loose
# on the separator (em-dash vs hyphen) because email clients re-encode.
_TRIAGE_REPLY_SUBJECT_RE = re.compile(
    r're:\s*\[deek\]\s+(existing_project_reply|new_enquiry)',
    re.IGNORECASE,
)

# Block delimiter — identical to the Memory Brief's.
_BLOCK_DELIM_RE = re.compile(
    r'^---\s*Q(\d+)\s*\(([a-z_]+)\)\s*---\s*$',
    re.MULTILINE,
)

_AFFIRMATIVE = frozenset({
    'yes', 'y', 'use', 'confirmed', 'correct', 'ok',
})
_NEGATIVE = frozenset({
    'no', 'n', 'reject', 'rejected', 'wrong', 'incorrect',
})
_CANDIDATE_NUMBER_RE = re.compile(r'^\s*([1-3])\s*$')
_EDIT_PREFIX_RE = re.compile(r'^\s*edit\s*:\s*', re.IGNORECASE)


# ── Data types ───────────────────────────────────────────────────────

@dataclass
class ParsedAnswer:
    q_number: int
    category: str
    raw_text: str
    verdict: str                  # affirm | deny | select_candidate | edit | text | empty
    selected_candidate_index: int | None = None   # 1-based when verdict='select_candidate'
    edited_text: str = ''         # populated when verdict='edit'
    free_text: str = ''           # populated when verdict='text' or 'edit'


@dataclass
class ParsedReply:
    triage_id: int | None
    user_email: str
    answers: list[ParsedAnswer] = field(default_factory=list)
    parse_notes: list[str] = field(default_factory=list)


# ── Subject handling ─────────────────────────────────────────────────

def is_triage_reply(subject: str) -> bool:
    """True if the subject matches our outgoing digest pattern."""
    if not subject:
        return False
    return bool(_TRIAGE_REPLY_SUBJECT_RE.search(subject))


def strip_reply_prefix(subject: str) -> str:
    """Drop leading 'Re: ' / 'FW: ' etc. to recover the original digest
    subject for matching. Lightweight — handles the common cases.
    """
    s = (subject or '').strip()
    for _ in range(3):
        lower = s.lower()
        if lower.startswith('re:') or lower.startswith('fw:'):
            s = s[3:].strip()
        elif lower.startswith('fwd:'):
            s = s[4:].strip()
        else:
            break
    return s


def match_triage_row_by_subject(
    conn, original_subject: str,
) -> int | None:
    """Find the triage row whose digest had this subject.

    Our outgoing subject is `[Deek] {classification} — {original_subject}`.
    We search backwards over the last 14 days (a user might reply to
    an older digest), matching by email_subject content. If multiple
    rows match, the most recent wins.
    """
    # Extract the original_subject slice after the em-dash
    # e.g. "[Deek] existing_project_reply — Re: Window displays"
    sep_idx = original_subject.find('—')
    if sep_idx < 0:
        sep_idx = original_subject.find(' - ')
    if sep_idx < 0:
        # No separator — fall back to searching the whole string
        needle = original_subject
    else:
        needle = original_subject[sep_idx + 1:].strip()
    if not needle:
        return None

    with conn.cursor() as cur:
        cur.execute(
            """SELECT id FROM cairn_intel.email_triage
                WHERE email_subject = %s
                  AND classification = 'existing_project_reply'
                  AND processed_at > NOW() - INTERVAL '14 days'
                ORDER BY processed_at DESC
                LIMIT 1""",
            (needle,),
        )
        row = cur.fetchone()
    return int(row[0]) if row else None


# ── Body parsing ─────────────────────────────────────────────────────

def strip_quoted(text: str) -> str:
    """Mirror of core.brief.replies.strip_quoted — drop quoted email
    tails so the original digest content doesn't pollute the parse.
    """
    lines: list[str] = []
    for line in (text or '').splitlines():
        stripped = line.lstrip()
        if stripped.startswith('>'):
            break
        if stripped.startswith('--- Original Message ---'):
            break
        if re.match(r'^On .+wrote:\s*$', stripped):
            break
        lines.append(line)
    return '\n'.join(lines).strip()


def _classify_match_confirm(text: str) -> ParsedAnswer:
    cleaned = text.strip()
    if not cleaned:
        return ParsedAnswer(q_number=1, category='match_confirm',
                            raw_text=text, verdict='empty')
    first = cleaned.splitlines()[0].strip()
    # Candidate number wins over YES/NO
    m = _CANDIDATE_NUMBER_RE.match(first)
    if m:
        return ParsedAnswer(
            q_number=1, category='match_confirm', raw_text=text,
            verdict='select_candidate',
            selected_candidate_index=int(m.group(1)),
        )
    first_tokens = re.split(r'[\s/,]+', first.lower())
    first_tokens = [t for t in first_tokens if t]
    if first_tokens and first_tokens[0] in _AFFIRMATIVE:
        return ParsedAnswer(q_number=1, category='match_confirm',
                            raw_text=text, verdict='affirm')
    if first_tokens and first_tokens[0] in _NEGATIVE:
        return ParsedAnswer(q_number=1, category='match_confirm',
                            raw_text=text, verdict='deny')
    return ParsedAnswer(q_number=1, category='match_confirm',
                        raw_text=text, verdict='text', free_text=cleaned)


def _classify_reply_approval(text: str) -> ParsedAnswer:
    cleaned = text.strip()
    if not cleaned:
        return ParsedAnswer(q_number=2, category='reply_approval',
                            raw_text=text, verdict='empty')
    # "EDIT: <text>" — support both EDIT on its own line with text
    # below AND inline "EDIT: whole edited reply".
    if _EDIT_PREFIX_RE.match(cleaned):
        edited = _EDIT_PREFIX_RE.sub('', cleaned, count=1).strip()
        return ParsedAnswer(
            q_number=2, category='reply_approval', raw_text=text,
            verdict='edit', edited_text=edited,
        )
    first = cleaned.splitlines()[0].strip().lower()
    if first in _AFFIRMATIVE or first == 'use':
        return ParsedAnswer(q_number=2, category='reply_approval',
                            raw_text=text, verdict='affirm')
    if first in _NEGATIVE:
        return ParsedAnswer(q_number=2, category='reply_approval',
                            raw_text=text, verdict='deny')
    # Multi-line reply without "USE" / "EDIT:" prefix — treat whole
    # block as the edited reply.
    return ParsedAnswer(
        q_number=2, category='reply_approval', raw_text=text,
        verdict='edit', edited_text=cleaned,
    )


def _classify_simple_text(q: int, category: str, text: str) -> ParsedAnswer:
    cleaned = (text or '').strip()
    if not cleaned:
        return ParsedAnswer(q_number=q, category=category,
                            raw_text=text, verdict='empty')
    return ParsedAnswer(q_number=q, category=category,
                        raw_text=text, verdict='text', free_text=cleaned)


def _strip_format_hint(text: str) -> str:
    """Drop the '(Expected reply format: ...)' hint lines if the user
    left them intact."""
    return '\n'.join(
        line for line in text.splitlines()
        if not line.lstrip().startswith('(Expected reply format:')
    ).strip()


def parse_reply_body(body: str, user_email: str, triage_id: int | None) -> ParsedReply:
    """Split a triage-reply body into answer blocks.

    Same delimiter contract as the Memory Brief. Each block is
    classified according to the category (four fixed categories for
    triage).
    """
    reply = ParsedReply(triage_id=triage_id, user_email=user_email)
    stripped = strip_quoted(body or '')
    if not stripped:
        reply.parse_notes.append('body empty after quote stripping')
        return reply

    matches = list(_BLOCK_DELIM_RE.finditer(stripped))
    if not matches:
        reply.parse_notes.append('no Q<n> delimiters found; treating whole body as notes')
        reply.answers.append(_classify_simple_text(4, 'notes', stripped))
        return reply

    for i, m in enumerate(matches):
        q_num = int(m.group(1))
        category = m.group(2)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(stripped)
        block_text = _strip_format_hint(stripped[start:end].strip())

        if category == 'match_confirm':
            reply.answers.append(_classify_match_confirm(block_text))
        elif category == 'reply_approval':
            reply.answers.append(_classify_reply_approval(block_text))
        elif category in ('project_folder', 'notes'):
            reply.answers.append(
                _classify_simple_text(q_num, category, block_text),
            )
        else:
            # Unknown category — preserve for diagnostics
            reply.parse_notes.append(f'unknown category: {category}')
            reply.answers.append(
                _classify_simple_text(q_num, category, block_text),
            )
    return reply


# ── DB + CRM operations ──────────────────────────────────────────────

def _connect():
    import psycopg2
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        raise RuntimeError('DATABASE_URL not set')
    return psycopg2.connect(db_url, connect_timeout=5)


def _body_hash(raw_body: str, triage_id: int) -> str:
    h = hashlib.sha256()
    h.update(str(triage_id).encode('utf-8'))
    h.update(b'\0')
    h.update((raw_body or '').encode('utf-8', errors='replace'))
    return h.hexdigest()


def already_applied(conn, triage_id: int, raw_body: str) -> bool:
    """True iff a review with this exact body has already been applied.

    Per-row state lives in cairn_intel.email_triage.reviewed_at /
    review_notes. We encode the body hash into review_notes (prefix
    'sha256:<digest>') so a fresh reply to an already-reviewed row
    doesn't double-apply.
    """
    digest = _body_hash(raw_body, triage_id)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT review_notes FROM cairn_intel.email_triage
                WHERE id = %s""",
            (triage_id,),
        )
        row = cur.fetchone()
    if not row or not row[0]:
        return False
    return digest in str(row[0])


def load_triage_row(conn, triage_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, email_subject, email_sender, project_id,
                      match_candidates, draft_reply, draft_model
                 FROM cairn_intel.email_triage
                WHERE id = %s""",
            (triage_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    candidates = row[4]
    if isinstance(candidates, str):
        try:
            candidates = json.loads(candidates)
        except Exception:
            candidates = None
    return {
        'id': int(row[0]),
        'email_subject': row[1],
        'email_sender': row[2],
        'project_id': row[3],
        'match_candidates': candidates,
        'draft_reply': row[5],
        'draft_model': row[6],
    }


def _post_crm_note(
    project_id: str, message: str, source: str = 'triage_reply',
) -> str | None:
    """POST a note to the CRM via /api/cairn/memory. Returns the note
    id or None on any failure."""
    import httpx
    if not project_id:
        return None
    base = (os.getenv('CRM_BASE_URL') or 'https://crm.nbnesigns.co.uk').rstrip('/')
    token = (
        os.getenv('DEEK_API_KEY')
        or os.getenv('CAIRN_API_KEY')
        or os.getenv('CLAW_API_KEY', '')
    ).strip()
    if not token:
        return None
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(
                f'{base}/api/cairn/memory',
                json={
                    'type': 'note',
                    'priority': 'low',
                    'message': message[:3000],
                    'project_id': project_id,
                    'source_modules': ['deek', source],
                },
                headers={'Authorization': f'Bearer {token}'},
            )
    except Exception as exc:
        logger.warning('[triage-reply] CRM POST failed: %s', exc)
        return None
    if r.status_code not in (200, 201):
        logger.warning(
            '[triage-reply] CRM returned HTTP %d — %s',
            r.status_code, r.text[:200],
        )
        return None
    try:
        data = r.json()
    except Exception:
        return None
    return (data or {}).get('id')


def _write_toby_memory(
    conn, text: str, reference_triage_id: int,
    tag: str = 'triage_reply_note',
) -> int | None:
    """Mirror of core.brief.replies._write_toby_memory — new memory
    chunk with toby_flag=true, linked to the originating triage row."""
    if not text.strip():
        return None
    try:
        from core.wiki.embeddings import get_embed_fn
        embed_fn = get_embed_fn()
        emb = embed_fn(text[:6000]) if embed_fn else None
    except Exception as exc:
        logger.warning('[triage-reply] embed failed (non-fatal): %s', exc)
        emb = None

    content_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
    file_path = f'memory/{tag}/{content_hash[:16]}'
    signals = {
        'toby_flag': 1.0,
        'via': 'triage_reply',
        'triage_id': int(reference_triage_id),
        'tag': tag,
    }
    with conn.cursor() as cur:
        if emb is not None:
            cur.execute(
                """INSERT INTO claw_code_chunks
                    (project_id, file_path, chunk_content, chunk_type,
                     chunk_name, content_hash, embedding, indexed_at,
                     salience, salience_signals, last_accessed_at,
                     access_count)
                   VALUES ('deek', %s, %s, 'memory', %s, %s, %s::vector,
                           NOW(), 7.0, %s::jsonb, NOW(), 0)
                   RETURNING id""",
                (file_path, text, text[:200], content_hash, emb,
                 json.dumps(signals)),
            )
        else:
            cur.execute(
                """INSERT INTO claw_code_chunks
                    (project_id, file_path, chunk_content, chunk_type,
                     chunk_name, content_hash, indexed_at,
                     salience, salience_signals, last_accessed_at,
                     access_count)
                   VALUES ('deek', %s, %s, 'memory', %s, %s, NOW(),
                           7.0, %s::jsonb, NOW(), 0)
                   RETURNING id""",
                (file_path, text, text[:200], content_hash,
                 json.dumps(signals)),
            )
        (new_id,) = cur.fetchone()
    return int(new_id)


def apply_reply(conn, reply: ParsedReply, raw_body: str) -> dict:
    """Apply a parsed reply to the triage row + CRM + memory.

    Never raises. Returns a summary dict for logging / test assertions.
    """
    if reply.triage_id is None:
        return {'error': 'no triage_id provided', 'answers_processed': []}

    row = load_triage_row(conn, reply.triage_id)
    if row is None:
        return {'error': f'triage row {reply.triage_id} not found',
                'answers_processed': []}

    summary: dict = {
        'triage_id': reply.triage_id,
        'answers_processed': [],
        'parse_notes': reply.parse_notes,
    }

    # Compute the final project_id + approved_reply + project_folder from
    # the answers so we can write the triage row update in one UPDATE.
    final_project_id = row['project_id']
    approved_reply = row.get('draft_reply') or ''
    project_folder_path = ''
    review_action = 'reviewed'

    for ans in reply.answers:
        action: dict = {
            'q_number': ans.q_number,
            'category': ans.category,
            'verdict': ans.verdict,
        }
        try:
            if ans.category == 'match_confirm':
                if ans.verdict == 'affirm':
                    # Keep candidate 1 (already in project_id)
                    action['result'] = f'kept project_id={final_project_id}'
                elif ans.verdict == 'select_candidate':
                    idx = ans.selected_candidate_index or 1
                    cands = row.get('match_candidates') or []
                    if 1 <= idx <= len(cands):
                        final_project_id = cands[idx - 1].get('project_id') or final_project_id
                        action['result'] = (
                            f'selected candidate #{idx}: {final_project_id}'
                        )
                    else:
                        action['result'] = f'invalid candidate #{idx}'
                elif ans.verdict == 'deny':
                    # Toby rejected the match — clear confirmed project
                    final_project_id = None
                    action['result'] = 'cleared project_id (rejected match)'
                elif ans.verdict == 'text':
                    # Free-text correction — captured but not auto-applied
                    action['result'] = 'free-text match correction captured'
                    _write_toby_memory(
                        conn, f'Match correction on triage {reply.triage_id}: {ans.free_text}',
                        reference_triage_id=reply.triage_id,
                        tag='triage_match_correction',
                    )
                else:
                    action['result'] = 'empty; no change'

            elif ans.category == 'reply_approval':
                if ans.verdict == 'affirm':
                    action['result'] = 'approved draft as-is'
                elif ans.verdict == 'edit':
                    approved_reply = ans.edited_text or approved_reply
                    action['result'] = 'approved edited reply'
                elif ans.verdict == 'deny':
                    approved_reply = ''  # don't post a rejected draft
                    action['result'] = 'draft rejected; no CRM note'
                else:
                    action['result'] = 'empty; keeping existing draft'

            elif ans.category == 'project_folder':
                if ans.verdict == 'text' and ans.free_text:
                    project_folder_path = ans.free_text
                    action['result'] = f'set project folder'
                else:
                    action['result'] = 'empty; no folder recorded'

            elif ans.category == 'notes':
                if ans.verdict == 'text' and ans.free_text:
                    new_id = _write_toby_memory(
                        conn, ans.free_text,
                        reference_triage_id=reply.triage_id,
                        tag='triage_note',
                    )
                    action['result'] = f'wrote memory {new_id}'
                    action['new_memory_id'] = new_id
                else:
                    action['result'] = 'empty'

            else:
                action['result'] = 'unknown category; skipped'

        except Exception as exc:
            action['error'] = f'{type(exc).__name__}: {exc}'
            logger.warning('[triage-reply] per-answer failure: %s', exc)

        summary['answers_processed'].append(action)

    # CRM note: post the approved reply (if any) and the folder path
    # update (if any) as a single consolidated note.
    crm_note_id: str | None = None
    if final_project_id and (approved_reply or project_folder_path):
        parts = []
        if approved_reply:
            parts.append('Approved reply (sent by Toby):\n\n' + approved_reply.strip())
        if project_folder_path:
            parts.append(f'Project folder: {project_folder_path}')
        note_body = '\n\n'.join(parts)
        crm_note_id = _post_crm_note(final_project_id, note_body)
        summary['crm_note_id'] = crm_note_id

    # Update the triage row — single UPDATE so the state is atomic.
    digest_tag = f'sha256:{_body_hash(raw_body, reply.triage_id)}'
    review_notes_combined = digest_tag
    # Stash a brief human summary too so future inspection is easy
    verdicts = [a.get('verdict') for a in summary['answers_processed']]
    review_notes_combined += f' | verdicts={verdicts}'

    with conn.cursor() as cur:
        cur.execute(
            """UPDATE cairn_intel.email_triage
                  SET project_id = %s,
                      draft_reply = CASE
                          WHEN %s = '' THEN draft_reply
                          ELSE %s
                      END,
                      project_folder_path = COALESCE(NULLIF(%s, ''), project_folder_path),
                      reviewed_at = NOW(),
                      review_action = %s,
                      review_notes = %s
                WHERE id = %s""",
            (
                final_project_id,
                approved_reply, approved_reply,
                project_folder_path,
                review_action,
                review_notes_combined,
                reply.triage_id,
            ),
        )
    conn.commit()

    summary['applied'] = True
    summary['final_project_id'] = final_project_id
    summary['approved_reply_length'] = len(approved_reply)
    summary['project_folder_path'] = project_folder_path
    return summary


__all__ = [
    'ParsedAnswer', 'ParsedReply',
    'is_triage_reply', 'strip_reply_prefix',
    'match_triage_row_by_subject', 'strip_quoted',
    'parse_reply_body', 'apply_reply',
    'already_applied', 'load_triage_row',
]
