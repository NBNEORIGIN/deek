"""arXiv research-loop Stage 3 — auto-draft a brief on YES verdict.

When Toby replies YES to a research_prompt question in the daily
memory brief, the reply processor writes ``toby_verdict='yes'``
onto the arxiv_candidates row. This module is invoked by the
Stage 3 cron (scripts/draft_pending_research_briefs.py) and:

  1. Finds rows with toby_verdict='yes' AND brief_drafted_at IS NULL
  2. Fetches the PDF from pdf_url
  3. Extracts text (first N pages, best-effort — flaky PDFs degrade
     to abstract-only)
  4. Sends to local Qwen with the Pattern-B brief structure prompt
  5. Writes briefs/research-<arxiv_id>-<slug>.md
  6. Updates the row with brief_drafted_at + brief_path

Pure-ish: all side effects are the file write + DB update. The
Qwen + HTTP calls are time-bounded and never raise to the caller.
"""
from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx


logger = logging.getLogger(__name__)


OLLAMA_DEFAULT_URL = 'http://localhost:11434'
OLLAMA_DEFAULT_MODEL = 'qwen2.5:7b-instruct'

# PDF extraction limits. Papers are typically 8-20 pages; we cap at
# 30 so we don't choke on surveys. Qwen's 8k ctx bounds the useful
# text we can feed anyway.
MAX_PDF_PAGES = 30
MAX_EXTRACTED_CHARS = 20000   # comfortably fits in 8192 tokens

PDF_FETCH_TIMEOUT = 45.0
QWEN_DRAFT_TIMEOUT = 120.0     # drafts are long responses

REPO_ROOT = Path(__file__).resolve().parents[2]
BRIEFS_DIR = REPO_ROOT / 'briefs'


@dataclass
class DraftResult:
    arxiv_id: str
    candidate_id: int
    success: bool
    brief_path: str | None
    chars_extracted: int
    error: str | None = None


# ── PDF fetch + extract ─────────────────────────────────────────────

def fetch_pdf_bytes(pdf_url: str) -> bytes | None:
    """Fetch the raw PDF. Returns None on any failure. Uses a
    generous timeout — arxiv mirrors can be slow."""
    if not pdf_url:
        return None
    try:
        with httpx.Client(
            timeout=PDF_FETCH_TIMEOUT, follow_redirects=True,
        ) as client:
            r = client.get(pdf_url)
            r.raise_for_status()
            if not r.content or len(r.content) < 1000:
                return None
            return r.content
    except Exception as exc:
        logger.warning('[autodraft] pdf fetch failed %s: %s', pdf_url, exc)
        return None


def extract_pdf_text(pdf_bytes: bytes, max_pages: int = MAX_PDF_PAGES,
                     max_chars: int = MAX_EXTRACTED_CHARS) -> str:
    """Extract readable text from a PDF. Graceful on scanned /
    corrupt / encrypted files — returns '' rather than raising so
    the caller falls back to abstract-only."""
    if not pdf_bytes:
        return ''
    try:
        from pypdf import PdfReader
    except Exception as exc:
        logger.warning('[autodraft] pypdf unavailable: %s', exc)
        return ''
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        logger.warning('[autodraft] pdf parse failed: %s', exc)
        return ''
    if getattr(reader, 'is_encrypted', False):
        try:
            reader.decrypt('')
        except Exception:
            logger.warning('[autodraft] pdf encrypted; skipping')
            return ''
    pages_out: list[str] = []
    total_chars = 0
    for i, page in enumerate(reader.pages[:max_pages]):
        try:
            txt = page.extract_text() or ''
        except Exception:
            continue
        if not txt.strip():
            continue
        pages_out.append(f'[page {i + 1}]\n{txt.strip()}')
        total_chars += len(txt)
        if total_chars >= max_chars:
            break
    return '\n\n'.join(pages_out)[:max_chars]


# ── Brief drafting prompt ───────────────────────────────────────────

_DRAFTER_SYSTEM = """You are a technical brief drafter for Deek, NBNE's memory + retrieval system. When given an arXiv paper (abstract + extracted body text) that Toby has already approved for deeper study, produce a Pattern-B brief that a Claude Code session could execute.

Deek's current architecture (for grounding applicability claims):
- Pgvector + BM25 hybrid retrieval over code, email, wiki, memory chunks, module snapshots
- Salience-weighted reranking with decay + toby_flag boost + via-tag boost
- Nightly consolidation of memories into schemas
- Daily human-in-the-loop memory brief email
- Triage pipeline: match inbound to CRM projects + draft replies + similarity surfacing
- Local Ollama (qwen2.5:7b-instruct) as default inference tier; Claude API reserved for complex work
- Strict cost discipline + cutover-cron shadow pattern for risky changes

Output ONE single markdown document exactly in this shape:

# BRIEF — <Short action-oriented title>

**Target repo:** Deek
**Module:** <area of Deek that changes>
**Consumer:** Claude Code (Deek session)
**Source paper:** <arXiv id> — <paper title>
**Abstract applicability score:** <X>/10

---

## Why this brief exists

<2-3 paragraphs. What the paper proposes, what's interesting about
it, why it's applicable to Deek TODAY (cite specific Deek
surfaces it would touch).>

## Pre-flight self-check

1. <concrete verification step>
2. <concrete verification step>
3. Report findings before Task 1.

## Tasks

### Task 1 — <Short name>

<What to build. Keep it concrete. Reference file paths only when
you're confident they exist in Deek's architecture described above.>

### Task 2 — <...>

### Task N — Tests

- Unit: ...
- Integration: ...
- Regression: all existing Deek tests must still pass

## Out of scope

- <Explicit non-goals>

## Constraints

- No breaking changes to existing /api/deek/* endpoints
- Shadow-mode gated with default-on for any risky retrieval /
  ranking change; cutover cron after 1-2 weeks review
- Local Qwen first for inference; no new Claude API surface
- No new cloud dependencies without explicit justification

## Open questions (delete answered before shipping)

- <honest uncertainty about applicability / tradeoffs>

---

Rules:
1. NEVER invent Deek file paths that weren't listed in the architecture above.
2. Keep tasks concrete and bounded. If a task needs 3+ days, split it.
3. Prefer shadow-mode + cutover over immediate flip-over for any ranking / retrieval change.
4. Flag genuine uncertainty in the open-questions section — don't paper over it.
5. Output ONLY the markdown. No preamble, no code fences around the doc.
"""


def slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r'[^a-zA-Z0-9]+', '-', text or '').strip('-').lower()
    return (s[:max_len].rstrip('-')) or 'untitled'


def draft_brief(
    *,
    arxiv_id: str,
    title: str,
    abstract: str,
    pdf_text: str,
    applicability_score: float | None,
    base_url: str | None = None,
    model: str | None = None,
) -> str | None:
    """Produce the brief markdown. None on Qwen failure."""
    base = (base_url or os.getenv('OLLAMA_BASE_URL') or OLLAMA_DEFAULT_URL).rstrip('/')
    mdl = model or os.getenv('OLLAMA_RESEARCH_MODEL') or OLLAMA_DEFAULT_MODEL

    body = pdf_text if pdf_text else (
        f'(PDF extraction failed or empty — draft from abstract only.)\n\n'
        f'Abstract:\n{abstract}'
    )
    user_prompt = (
        f'Paper title: {title}\n'
        f'arXiv id:    {arxiv_id}\n'
        f'Applicability score: {applicability_score}/10\n\n'
        f'Abstract:\n{abstract}\n\n'
        f'Paper body (truncated):\n{body[:MAX_EXTRACTED_CHARS]}\n\n'
        'Draft the brief now.'
    )
    payload = {
        'model': mdl,
        'messages': [
            {'role': 'system', 'content': _DRAFTER_SYSTEM},
            {'role': 'user', 'content': user_prompt},
        ],
        'stream': False,
        'options': {
            'temperature': 0.2,   # some creativity but grounded
            'num_ctx': 8192,
            'num_predict': 2048,
        },
    }
    try:
        with httpx.Client(timeout=QWEN_DRAFT_TIMEOUT) as client:
            r = client.post(f'{base}/api/chat', json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.warning('[autodraft] qwen drafter failed: %s', exc)
        return None
    content = (data.get('message') or {}).get('content') or ''
    content = content.strip()
    if not content:
        return None
    # Strip any accidental markdown fence wrapping
    if content.startswith('```'):
        first_nl = content.find('\n')
        if first_nl > 0:
            content = content[first_nl + 1:]
        if content.endswith('```'):
            content = content[:-3].rstrip()
    return content


# ── File write + DB update ──────────────────────────────────────────

def write_brief_file(arxiv_id: str, title: str, content: str) -> Path:
    """Write the brief to briefs/research-<id>-<slug>.md. Never
    overwrites; if a file already exists, appends a numeric suffix."""
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    slug = slugify(title)
    base_name = f'research-{arxiv_id}-{slug}.md'
    target = BRIEFS_DIR / base_name
    n = 2
    while target.exists():
        target = BRIEFS_DIR / f'research-{arxiv_id}-{slug}-{n}.md'
        n += 1
    target.write_text(content, encoding='utf-8')
    return target


def mark_drafted(conn, candidate_id: int, brief_path: str) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE cairn_intel.arxiv_candidates
                     SET brief_drafted_at = NOW(),
                         brief_path = %s
                   WHERE id = %s""",
                (brief_path, int(candidate_id)),
            )
            conn.commit()
        return True
    except Exception as exc:
        logger.warning('[autodraft] mark_drafted failed: %s', exc)
        return False


def list_pending(conn, limit: int = 5) -> list[dict]:
    """YES-verdict rows that haven't been drafted yet."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, arxiv_id, title, abstract, pdf_url,
                          applicability_score
                     FROM cairn_intel.arxiv_candidates
                    WHERE toby_verdict = 'yes'
                      AND brief_drafted_at IS NULL
                    ORDER BY toby_verdict_at ASC
                    LIMIT %s""",
                (int(limit),),
            )
            rows = cur.fetchall()
    except Exception as exc:
        logger.warning('[autodraft] list_pending failed: %s', exc)
        return []
    out = []
    for r in rows:
        out.append({
            'id': int(r[0]),
            'arxiv_id': r[1],
            'title': r[2],
            'abstract': r[3],
            'pdf_url': r[4],
            'applicability_score': float(r[5]) if r[5] is not None else None,
        })
    return out


def draft_one(conn, candidate: dict) -> DraftResult:
    """Full draft pipeline for one candidate. Updates DB on
    success. Never raises."""
    arxiv_id = candidate['arxiv_id']
    cand_id = candidate['id']
    try:
        pdf_bytes = fetch_pdf_bytes(candidate['pdf_url'])
        pdf_text = extract_pdf_text(pdf_bytes or b'')
        brief_md = draft_brief(
            arxiv_id=arxiv_id,
            title=candidate['title'],
            abstract=candidate['abstract'],
            pdf_text=pdf_text,
            applicability_score=candidate.get('applicability_score'),
        )
        if not brief_md:
            return DraftResult(
                arxiv_id=arxiv_id, candidate_id=cand_id,
                success=False, brief_path=None,
                chars_extracted=len(pdf_text),
                error='qwen drafter returned empty',
            )
        path = write_brief_file(arxiv_id, candidate['title'], brief_md)
        rel_path = str(path.relative_to(REPO_ROOT)).replace('\\', '/')
        mark_drafted(conn, cand_id, rel_path)
        return DraftResult(
            arxiv_id=arxiv_id, candidate_id=cand_id,
            success=True, brief_path=rel_path,
            chars_extracted=len(pdf_text),
        )
    except Exception as exc:
        logger.exception('[autodraft] unhandled error for %s', arxiv_id)
        return DraftResult(
            arxiv_id=arxiv_id, candidate_id=cand_id,
            success=False, brief_path=None,
            chars_extracted=0,
            error=f'{type(exc).__name__}: {exc}',
        )


__all__ = [
    'DraftResult',
    'fetch_pdf_bytes',
    'extract_pdf_text',
    'draft_brief',
    'write_brief_file',
    'mark_drafted',
    'list_pending',
    'draft_one',
    'slugify',
]
