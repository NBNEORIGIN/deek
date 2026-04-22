"""arXiv research-loop — Stage 1 (poll) + Stage 2 (surface).

Three-stage pipeline for "Deek curates its own research roadmap":

  Stage 1 (poll_arxiv.py cron, daily):
    - Rotate through Deek-relevant query phrases
    - Fetch recent abstracts from arXiv API
    - Score each for applicability via local Qwen (0-10 scale)
    - Persist the top-N per query to cairn_intel.arxiv_candidates

  Stage 2 (memory brief integration):
    - The brief question builder picks the top un-surfaced
      candidate (score >= threshold) and adds a research_prompt
      question
    - User replies YES / NO / LATER (handled by reply parser via
      the new category)

  Stage 3 (follow-up, not in this module):
    - On a YES verdict, a drafter fetches the PDF and writes a
      briefs/research-<slug>.md against the standard brief shape

This module is pure — no cron logic, no email send — so it is
importable from both the poll script and the question generator
without circular dependency.
"""
from __future__ import annotations

import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx


logger = logging.getLogger(__name__)


ARXIV_API_URL = 'https://export.arxiv.org/api/query'
ARXIV_API_TIMEOUT = 20.0
OLLAMA_DEFAULT_URL = 'http://localhost:11434'
OLLAMA_DEFAULT_MODEL = 'qwen2.5:7b-instruct'

# Topics rotated through daily. Tuning this list is config, not
# code — moved to a yaml if it grows, but keeping inline while
# small.
DEFAULT_QUERIES = (
    'agentic memory language model',
    'retrieval augmented generation',
    'self-improving language models',
    'semantic search reranking',
    'continual learning language models',
    'episodic memory transformer',
)

# Min applicability score a candidate needs to be surfaced.
# Calibrated conservatively: sub-7 means "interesting maybe, but
# I'd rather not waste your attention". Tune after first week of
# data.
SURFACE_THRESHOLD = 7.0

# How many abstracts to pull per query per day. arXiv returns
# ~25 recent results in the default sort; 10 is a comfortable
# middle ground.
DEFAULT_MAX_PER_QUERY = 10


@dataclass
class ArxivPaper:
    arxiv_id: str
    title: str
    abstract: str
    authors: list[str]
    published_at: date
    pdf_url: str

    def to_db_tuple(
        self, *, query: str,
        applicability_score: float | None,
        applicability_reason: str | None,
    ) -> tuple:
        return (
            self.arxiv_id, self.title, self.abstract,
            self.authors, self.published_at, self.pdf_url, query,
            applicability_score, applicability_reason,
        )


# ── arXiv API ────────────────────────────────────────────────────────

_ATOM_NS = {
    'atom': 'http://www.w3.org/2005/Atom',
    'arxiv': 'http://arxiv.org/schemas/atom',
}


def fetch_recent(
    query: str, max_results: int = DEFAULT_MAX_PER_QUERY,
) -> list[ArxivPaper]:
    """Hit the arXiv API for the N most recent papers matching
    ``query``. Never raises — returns [] on any failure. The API
    is courteous (3-sec throttle per their terms); at ~6 queries
    per day we stay well under their limits."""
    params = {
        'search_query': f'all:{query}',
        'sortBy': 'submittedDate',
        'sortOrder': 'descending',
        'max_results': int(max_results),
    }
    try:
        with httpx.Client(timeout=ARXIV_API_TIMEOUT) as client:
            r = client.get(ARXIV_API_URL, params=params)
            r.raise_for_status()
    except Exception as exc:
        logger.warning('[arxiv] fetch failed for %r: %s', query, exc)
        return []
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as exc:
        logger.warning('[arxiv] atom parse failed: %s', exc)
        return []

    out: list[ArxivPaper] = []
    for entry in root.findall('atom:entry', _ATOM_NS):
        try:
            out.append(_parse_entry(entry))
        except Exception as exc:
            logger.debug('[arxiv] skipped entry: %s', exc)
            continue
    return out


def _parse_entry(entry) -> ArxivPaper:
    """Turn one <atom:entry> into an ArxivPaper. Raises on any missing
    required field so fetch_recent can skip it."""
    id_url = _text(entry, 'atom:id')
    if not id_url:
        raise ValueError('no id')
    # id looks like http://arxiv.org/abs/2404.12345v1
    m = re.search(r'/abs/([^/\s]+?)(v\d+)?$', id_url.strip())
    if not m:
        raise ValueError(f'cannot extract arxiv id from {id_url!r}')
    arxiv_id = m.group(1)

    title = ' '.join((_text(entry, 'atom:title') or '').split())
    abstract = ' '.join((_text(entry, 'atom:summary') or '').split())
    published = _text(entry, 'atom:published') or ''
    try:
        published_at = datetime.fromisoformat(
            published.replace('Z', '+00:00')
        ).date()
    except Exception:
        published_at = datetime.now(timezone.utc).date()

    authors = [
        (_text(a, 'atom:name') or '').strip()
        for a in entry.findall('atom:author', _ATOM_NS)
    ]
    authors = [a for a in authors if a]

    pdf_url = ''
    for link in entry.findall('atom:link', _ATOM_NS):
        if link.attrib.get('title') == 'pdf' or link.attrib.get('type') == 'application/pdf':
            pdf_url = link.attrib.get('href', '')
            break
    if not pdf_url:
        pdf_url = f'https://arxiv.org/pdf/{arxiv_id}'

    if not (title and abstract):
        raise ValueError('missing title or abstract')
    return ArxivPaper(
        arxiv_id=arxiv_id,
        title=title,
        abstract=abstract,
        authors=authors,
        published_at=published_at,
        pdf_url=pdf_url,
    )


def _text(elem, path: str) -> str:
    node = elem.find(path, _ATOM_NS)
    return (node.text or '').strip() if node is not None and node.text else ''


# ── Applicability scoring (local Qwen) ───────────────────────────────

_APPLICABILITY_SYSTEM = """You are a research scout for Deek, an internal memory + retrieval system run at NBNE. Deek's architecture:

  - Pgvector + BM25 hybrid retrieval over a growing corpus of code, email, wiki articles, module snapshots, and memory chunks
  - Salience-weighted reranking with decay + toby_flag boost
  - Consolidation pipeline that distils memories into schemas nightly
  - Daily memory-brief email → human-in-the-loop corrections
  - Triage pipeline: match inbound emails to CRM projects + draft replies
  - Local Ollama inference (qwen2.5:7b-instruct) as the default tier, Claude API reserved for complex work

Given a paper's title + abstract, judge how applicable it is to improving Deek's architecture or workflow. Return ONLY a single JSON object:

  {
    "score": <float 0-10>,
    "reason": "<one sentence explaining the score>"
  }

Scoring rubric:
  9-10: directly applicable mechanism we could port or adapt within a week
  7-8:  useful pattern we should know about; may inform a design decision
  4-6:  tangentially related; interesting context
  1-3:  narrow research problem not relevant to our build
  0:    off-topic (unrelated field, survey paper with no actionable content)

Do not invent content. If the abstract is vague, lean toward lower scores. Output ONLY the JSON object, no markdown fence, no commentary.
"""


def score_applicability(
    paper: ArxivPaper,
    *,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = 30.0,
) -> tuple[float | None, str]:
    """Return (score, reason). Score is None on any failure."""
    base = (base_url or os.getenv('OLLAMA_BASE_URL') or OLLAMA_DEFAULT_URL).rstrip('/')
    mdl = model or os.getenv('OLLAMA_RESEARCH_MODEL') or OLLAMA_DEFAULT_MODEL

    user = (
        f'Title: {paper.title}\n\n'
        f'Abstract: {paper.abstract}\n\n'
        'Output JSON now.'
    )
    payload = {
        'model': mdl,
        'messages': [
            {'role': 'system', 'content': _APPLICABILITY_SYSTEM},
            {'role': 'user', 'content': user},
        ],
        'stream': False,
        'format': 'json',
        'options': {'temperature': 0.1, 'num_ctx': 4096},
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(f'{base}/api/chat', json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.warning('[arxiv] applicability call failed: %s', exc)
        return None, ''

    content = (data.get('message') or {}).get('content') or ''
    parsed = _parse_applicability_json(content)
    if not parsed:
        return None, ''
    score = parsed.get('score')
    reason = str(parsed.get('reason') or '').strip()[:500]
    try:
        score_f = max(0.0, min(10.0, float(score)))
    except (TypeError, ValueError):
        return None, reason
    return score_f, reason


def _parse_applicability_json(text: str) -> dict | None:
    if not text:
        return None
    text = text.strip()
    fence = re.match(r'^```(?:json)?\s*(.+?)\s*```$', text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        brace = re.search(r'\{.*\}', text, re.DOTALL)
        if brace:
            try:
                return json.loads(brace.group(0))
            except json.JSONDecodeError:
                return None
    return None


# ── DB persistence ───────────────────────────────────────────────────

def insert_candidate(conn, paper: ArxivPaper, *, query: str,
                     score: float | None, reason: str) -> int | None:
    """Upsert into cairn_intel.arxiv_candidates. Returns the row id
    on insert, or None if the arxiv_id was already stored (we don't
    re-score existing papers)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO cairn_intel.arxiv_candidates
                    (arxiv_id, title, abstract, authors, published_at,
                     pdf_url, query, applicability_score,
                     applicability_reason)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (arxiv_id) DO NOTHING
                   RETURNING id""",
                (
                    paper.arxiv_id, paper.title, paper.abstract,
                    paper.authors, paper.published_at, paper.pdf_url,
                    query, score, reason,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return int(row[0]) if row else None
    except Exception as exc:
        logger.warning('[arxiv] insert failed for %s: %s',
                       paper.arxiv_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def pick_next_candidate(conn, min_score: float = SURFACE_THRESHOLD) -> dict | None:
    """Return the highest-scoring un-surfaced candidate, or None.
    Callers mark surfaced_at via mark_surfaced() after they've
    actually rendered the question into a brief."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, arxiv_id, title, abstract, pdf_url,
                          applicability_score, applicability_reason
                     FROM cairn_intel.arxiv_candidates
                    WHERE surfaced_at IS NULL
                      AND applicability_score >= %s
                    ORDER BY applicability_score DESC,
                             published_at DESC
                    LIMIT 1""",
                (min_score,),
            )
            row = cur.fetchone()
    except Exception as exc:
        logger.warning('[arxiv] pick_next_candidate failed: %s', exc)
        return None
    if not row:
        return None
    return {
        'id': int(row[0]),
        'arxiv_id': row[1],
        'title': row[2],
        'abstract': row[3],
        'pdf_url': row[4],
        'applicability_score': float(row[5] or 0.0),
        'applicability_reason': row[6] or '',
    }


def mark_surfaced(conn, candidate_id: int) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE cairn_intel.arxiv_candidates
                      SET surfaced_at = NOW()
                    WHERE id = %s""",
                (int(candidate_id),),
            )
            conn.commit()
        return True
    except Exception as exc:
        logger.warning('[arxiv] mark_surfaced failed: %s', exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def record_verdict(conn, candidate_id: int, verdict: str) -> bool:
    """Store Toby's verdict on a surfaced candidate.
    verdict ∈ {'yes', 'no', 'later'}."""
    v = (verdict or '').strip().lower()
    if v not in {'yes', 'no', 'later'}:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE cairn_intel.arxiv_candidates
                      SET toby_verdict = %s,
                          toby_verdict_at = NOW()
                    WHERE id = %s""",
                (v, int(candidate_id)),
            )
            conn.commit()
        return True
    except Exception as exc:
        logger.warning('[arxiv] record_verdict failed: %s', exc)
        return False


__all__ = [
    'ArxivPaper',
    'DEFAULT_QUERIES',
    'DEFAULT_MAX_PER_QUERY',
    'SURFACE_THRESHOLD',
    'fetch_recent',
    'score_applicability',
    'insert_candidate',
    'pick_next_candidate',
    'mark_surfaced',
    'record_verdict',
]
