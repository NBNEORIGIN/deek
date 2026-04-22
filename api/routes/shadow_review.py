"""Shadow-review dashboard — replaces "SQL by hand" with "one click per row".

Four shadow tables have accumulated this session:

  cairn_intel.triage_similarity_debug        (Phase D similarity surfacing)
  cairn_intel.conversational_reply_shadow    (Qwen reply normaliser)
  cairn_intel.arxiv_candidates               (research loop)

Plus the pre-existing impressions + crosslink shadows, left alone
here because they have their own analyser scripts already.

Endpoints under /admin/shadow/*:

  GET  /admin/shadow/summary
       — counts per source + pending-review size

  GET  /admin/shadow/triage-similarity?limit=20
  GET  /admin/shadow/conversational?source=brief|triage&limit=20
  GET  /admin/shadow/arxiv?verdict=pending|yes|no|later&limit=20
       — return the rows with reviewer-friendly fields

  POST /admin/shadow/review
       body: {source, id, verdict}
       source ∈ {triage_similarity, conversational, arxiv}
       verdict ∈ {good, partial, wrong}  (or yes/no/later for arxiv)

  GET  /admin/shadow/review-ui
       — minimal HTML page for Toby to thumb-up/down without writing SQL

Auth: same Bearer token as the rest of /admin. UI endpoint is open
so browser can load it; XHR calls from the UI send the token.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from api.middleware.auth import verify_api_key

log = logging.getLogger(__name__)
router = APIRouter(prefix='/admin/shadow', tags=['Admin — Shadow Review'])


def _connect():
    import psycopg2
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        raise HTTPException(500, 'DATABASE_URL not set')
    try:
        return psycopg2.connect(db_url, connect_timeout=5)
    except Exception as exc:
        raise HTTPException(500, f'db connect failed: {exc}')


# ── Summary ─────────────────────────────────────────────────────────

@router.get('/summary')
async def shadow_summary(_: bool = Depends(verify_api_key)) -> JSONResponse:
    """Return per-source counts + pending-review sizes."""
    conn = _connect()
    out: dict[str, dict] = {}
    try:
        with conn.cursor() as cur:
            # Triage similarity
            cur.execute("""
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE useful_index IS NULL),
                       COALESCE(MIN(created_at), NOW()),
                       COALESCE(MAX(created_at), NOW())
                  FROM cairn_intel.triage_similarity_debug
            """)
            total, unreviewed, first, last = cur.fetchone()
            out['triage_similarity'] = {
                'total': int(total or 0),
                'pending_review': int(unreviewed or 0),
                'first_row_at': first.isoformat() if first else None,
                'last_row_at': last.isoformat() if last else None,
            }

            # Conversational reply (brief + triage)
            cur.execute("""
                SELECT source,
                       COUNT(*),
                       COUNT(*) FILTER (WHERE toby_reviewed = FALSE),
                       COALESCE(MAX(created_at), NOW())
                  FROM cairn_intel.conversational_reply_shadow
                 GROUP BY source
            """)
            out['conversational'] = {}
            for source, total, pending, last in cur.fetchall():
                out['conversational'][source] = {
                    'total': int(total or 0),
                    'pending_review': int(pending or 0),
                    'last_row_at': last.isoformat() if last else None,
                }

            # arXiv candidates
            cur.execute("""
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE toby_verdict IS NULL
                                           AND surfaced_at IS NOT NULL),
                       COUNT(*) FILTER (WHERE toby_verdict = 'yes'),
                       COUNT(*) FILTER (WHERE toby_verdict = 'no'),
                       COUNT(*) FILTER (WHERE toby_verdict = 'later'),
                       COUNT(*) FILTER (WHERE applicability_score >= 7
                                           AND surfaced_at IS NULL)
                  FROM cairn_intel.arxiv_candidates
            """)
            total, pending, yes_n, no_n, later_n, queued = cur.fetchone()
            out['arxiv'] = {
                'total': int(total or 0),
                'surfaced_pending_verdict': int(pending or 0),
                'verdict_yes': int(yes_n or 0),
                'verdict_no': int(no_n or 0),
                'verdict_later': int(later_n or 0),
                'queued_for_surfacing': int(queued or 0),
            }
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return JSONResponse(out)


# ── Listing endpoints ───────────────────────────────────────────────

@router.get('/triage-similarity')
async def list_triage_similarity(
    limit: int = Query(20, ge=1, le=200),
    unreviewed_only: bool = True,
    _: bool = Depends(verify_api_key),
) -> JSONResponse:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            where = 'WHERE useful_index IS NULL' if unreviewed_only else ''
            cur.execute(
                f"""SELECT id, triage_id, LEFT(enquiry_summary, 400),
                           candidates, latency_ms, useful_index,
                           created_at
                      FROM cairn_intel.triage_similarity_debug
                      {where}
                     ORDER BY created_at DESC
                     LIMIT %s""",
                (limit,),
            )
            rows = []
            for r in cur.fetchall():
                cands = r[3]
                if isinstance(cands, str):
                    try:
                        cands = json.loads(cands)
                    except Exception:
                        cands = []
                rows.append({
                    'id': r[0],
                    'triage_id': r[1],
                    'enquiry_summary': r[2],
                    'candidates': cands,
                    'latency_ms': r[4],
                    'useful_index': r[5],
                    'created_at': r[6].isoformat() if r[6] else None,
                })
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return JSONResponse({'rows': rows})


@router.get('/conversational')
async def list_conversational(
    source: str = Query('brief', pattern='^(brief|triage)$'),
    limit: int = Query(20, ge=1, le=200),
    unreviewed_only: bool = True,
    _: bool = Depends(verify_api_key),
) -> JSONResponse:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            where = 'WHERE source = %s'
            params: list[Any] = [source]
            if unreviewed_only:
                where += ' AND toby_reviewed = FALSE'
            cur.execute(
                f"""SELECT id, source, reference_id,
                           LEFT(raw_body, 2000), normalised, applied,
                           toby_reviewed, toby_verdict, created_at
                      FROM cairn_intel.conversational_reply_shadow
                      {where}
                     ORDER BY created_at DESC
                     LIMIT %s""",
                (*params, limit),
            )
            rows = []
            for r in cur.fetchall():
                normalised = r[4]
                if isinstance(normalised, str):
                    try:
                        normalised = json.loads(normalised)
                    except Exception:
                        normalised = {}
                rows.append({
                    'id': r[0],
                    'source': r[1],
                    'reference_id': r[2],
                    'raw_body': r[3],
                    'normalised': normalised,
                    'applied': bool(r[5]),
                    'toby_reviewed': bool(r[6]),
                    'toby_verdict': r[7],
                    'created_at': r[8].isoformat() if r[8] else None,
                })
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return JSONResponse({'rows': rows})


@router.get('/arxiv')
async def list_arxiv(
    verdict: str = Query('pending', pattern='^(pending|yes|no|later|all)$'),
    limit: int = Query(20, ge=1, le=200),
    _: bool = Depends(verify_api_key),
) -> JSONResponse:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            where = ''
            if verdict == 'pending':
                where = 'WHERE surfaced_at IS NOT NULL AND toby_verdict IS NULL'
            elif verdict in ('yes', 'no', 'later'):
                where = "WHERE toby_verdict = %s"
            cur.execute(
                f"""SELECT id, arxiv_id, title, LEFT(abstract, 600),
                           pdf_url, applicability_score,
                           applicability_reason, surfaced_at,
                           toby_verdict, toby_verdict_at
                      FROM cairn_intel.arxiv_candidates
                      {where}
                     ORDER BY applicability_score DESC NULLS LAST,
                              created_at DESC
                     LIMIT %s""",
                ((verdict, limit) if verdict in ('yes', 'no', 'later')
                 else (limit,)),
            )
            rows = []
            for r in cur.fetchall():
                rows.append({
                    'id': r[0],
                    'arxiv_id': r[1],
                    'title': r[2],
                    'abstract': r[3],
                    'pdf_url': r[4],
                    'applicability_score': float(r[5]) if r[5] is not None else None,
                    'applicability_reason': r[6],
                    'surfaced_at': r[7].isoformat() if r[7] else None,
                    'toby_verdict': r[8],
                    'toby_verdict_at': r[9].isoformat() if r[9] else None,
                })
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return JSONResponse({'rows': rows})


# ── Review POST ─────────────────────────────────────────────────────

_VALID_VERDICTS = {
    'triage_similarity': {'good', 'partial', 'wrong'},
    'conversational': {'good', 'partial', 'wrong'},
    'arxiv': {'yes', 'no', 'later'},
}

_TABLE_BY_SOURCE = {
    'triage_similarity': 'cairn_intel.triage_similarity_debug',
    'conversational': 'cairn_intel.conversational_reply_shadow',
    'arxiv': 'cairn_intel.arxiv_candidates',
}


@router.post('/review')
async def submit_review(
    payload: dict, _: bool = Depends(verify_api_key),
) -> JSONResponse:
    source = str(payload.get('source') or '').strip()
    row_id = payload.get('id')
    verdict = str(payload.get('verdict') or '').strip().lower()

    if source not in _TABLE_BY_SOURCE:
        raise HTTPException(400, f'unknown source {source!r}')
    if verdict not in _VALID_VERDICTS[source]:
        raise HTTPException(
            400, f'invalid verdict {verdict!r} for source {source!r}',
        )
    try:
        row_id_int = int(row_id)
    except (TypeError, ValueError):
        raise HTTPException(400, 'id must be integer')

    table = _TABLE_BY_SOURCE[source]
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if source == 'arxiv':
                cur.execute(
                    f"""UPDATE {table}
                          SET toby_verdict = %s,
                              toby_verdict_at = NOW()
                        WHERE id = %s
                        RETURNING id""",
                    (verdict, row_id_int),
                )
            elif source == 'triage_similarity':
                # Maps verdict → useful_index heuristic: good=1,
                # partial=2, wrong=0. Keeps the existing column
                # shape without a migration.
                useful_index_map = {'good': 1, 'partial': 2, 'wrong': 0}
                cur.execute(
                    f"""UPDATE {table}
                          SET useful_index = %s,
                              useful_flagged_at = NOW()
                        WHERE id = %s
                        RETURNING id""",
                    (useful_index_map[verdict], row_id_int),
                )
            else:  # conversational
                cur.execute(
                    f"""UPDATE {table}
                          SET toby_reviewed = TRUE,
                              toby_verdict = %s
                        WHERE id = %s
                        RETURNING id""",
                    (verdict, row_id_int),
                )
            row = cur.fetchone()
            conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not row:
        raise HTTPException(404, 'row not found')
    return JSONResponse({'ok': True, 'id': row[0], 'verdict': verdict})


# ── Minimal HTML UI ─────────────────────────────────────────────────

_REVIEW_UI_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Deek — Shadow Review</title>
<style>
  body { font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
         max-width: 960px; margin: 2rem auto; padding: 0 1rem;
         color: #222; }
  h1 { border-bottom: 2px solid #222; padding-bottom: .25rem; }
  h2 { margin-top: 2rem; }
  .row { border: 1px solid #ddd; border-radius: 6px;
         padding: 1rem; margin: 1rem 0; background: #fafafa; }
  .meta { color: #666; font-size: .85rem; margin-bottom: .5rem; }
  pre { background: #f0f0f0; padding: .75rem; border-radius: 4px;
        white-space: pre-wrap; font-size: .85rem; max-height: 300px;
        overflow-y: auto; }
  button { border: 1px solid #888; background: #fff;
           padding: .4rem .8rem; cursor: pointer; margin-right: .25rem;
           border-radius: 4px; font-size: .9rem; }
  button:hover { background: #eef; }
  button.primary { background: #9f9; border-color: #5a5; }
  button.danger  { background: #f99; border-color: #a55; }
  button.neutral { background: #ffc; border-color: #aa5; }
  .ok   { color: #0a0; }
  .fail { color: #a00; }
  #token { width: 30rem; font-family: monospace; font-size: .85rem; }
  .auth-row { background: #fff3cd; padding: 1rem; border-radius: 6px;
              border: 1px solid #ffeeba; margin-bottom: 2rem; }
  .empty { color: #888; font-style: italic; }
</style>
</head>
<body>
<h1>Deek — Shadow Review</h1>

<div class="auth-row">
  <label>API token (sent as X-API-Key header):</label><br>
  <input id="token" type="password" placeholder="DEEK_API_KEY">
  <button onclick="refreshAll()">Load</button>
  <span id="authStatus"></span>
</div>

<h2>Summary</h2>
<div id="summary" class="empty">(load to populate)</div>

<h2 id="arxiv-h">arXiv candidates (pending verdict)</h2>
<div id="arxiv" class="empty">(load to populate)</div>

<h2 id="conv-h">Conversational replies — brief (pending review)</h2>
<div id="conv_brief" class="empty">(load to populate)</div>

<h2>Conversational replies — triage (pending review)</h2>
<div id="conv_triage" class="empty">(load to populate)</div>

<h2>Triage similarity (pending review)</h2>
<div id="similarity" class="empty">(load to populate)</div>

<script>
function getToken() {
  return document.getElementById('token').value.trim();
}

async function api(path, opts={}) {
  const t = getToken();
  if (!t) {
    alert('Paste your DEEK_API_KEY first');
    return null;
  }
  const headers = {'X-API-Key': t, 'Content-Type': 'application/json'};
  const r = await fetch(path, {...opts, headers: {...headers, ...(opts.headers||{})}});
  if (!r.ok) {
    document.getElementById('authStatus').innerHTML = '<span class="fail">HTTP ' + r.status + '</span>';
    return null;
  }
  document.getElementById('authStatus').innerHTML = '<span class="ok">OK</span>';
  return r.json();
}

function renderSummary(s) {
  if (!s) return;
  const a = s.arxiv || {}, c = s.conversational || {}, t = s.triage_similarity || {};
  const brief = c.brief || {}, triage = c.triage || {};
  document.getElementById('summary').innerHTML = `
    <table>
      <tr><td>arXiv queued for surfacing:</td><td>${a.queued_for_surfacing ?? 0}</td></tr>
      <tr><td>arXiv surfaced, awaiting verdict:</td><td>${a.surfaced_pending_verdict ?? 0}</td></tr>
      <tr><td>arXiv verdicts (yes / no / later):</td><td>${a.verdict_yes ?? 0} / ${a.verdict_no ?? 0} / ${a.verdict_later ?? 0}</td></tr>
      <tr><td>Conversational brief (total / pending):</td><td>${brief.total ?? 0} / ${brief.pending_review ?? 0}</td></tr>
      <tr><td>Conversational triage (total / pending):</td><td>${triage.total ?? 0} / ${triage.pending_review ?? 0}</td></tr>
      <tr><td>Triage similarity (total / pending):</td><td>${t.total ?? 0} / ${t.pending_review ?? 0}</td></tr>
    </table>
  `;
}

function renderArxiv(rows) {
  const div = document.getElementById('arxiv');
  if (!rows || rows.length === 0) { div.innerHTML = '<div class="empty">nothing pending</div>'; return; }
  div.innerHTML = '';
  rows.forEach(r => {
    const el = document.createElement('div');
    el.className = 'row';
    el.innerHTML = `
      <div class="meta">arXiv ${r.arxiv_id} · score ${r.applicability_score?.toFixed(1) ?? '?'}/10 · surfaced ${r.surfaced_at || '—'}</div>
      <strong>${r.title}</strong><br>
      <div class="meta">${r.applicability_reason || ''}</div>
      <pre>${r.abstract}</pre>
      <a href="${r.pdf_url}" target="_blank">Open PDF</a><br><br>
      <button class="primary" onclick="review('arxiv', ${r.id}, 'yes', this)">YES — worth a deeper look</button>
      <button class="danger" onclick="review('arxiv', ${r.id}, 'no', this)">NO</button>
      <button class="neutral" onclick="review('arxiv', ${r.id}, 'later', this)">LATER</button>
    `;
    div.appendChild(el);
  });
}

function renderConversational(divId, rows) {
  const div = document.getElementById(divId);
  if (!rows || rows.length === 0) { div.innerHTML = '<div class="empty">nothing pending</div>'; return; }
  div.innerHTML = '';
  rows.forEach(r => {
    const el = document.createElement('div');
    el.className = 'row';
    el.innerHTML = `
      <div class="meta">${r.source} · ref=${r.reference_id} · ${r.created_at} · applied=${r.applied}</div>
      <div><strong>User's prose reply:</strong></div>
      <pre>${r.raw_body}</pre>
      <div><strong>Normaliser output:</strong></div>
      <pre>${JSON.stringify(r.normalised?.answers ?? r.normalised, null, 2)}</pre>
      <button class="primary" onclick="review('conversational', ${r.id}, 'good', this)">Good</button>
      <button class="neutral" onclick="review('conversational', ${r.id}, 'partial', this)">Partial</button>
      <button class="danger" onclick="review('conversational', ${r.id}, 'wrong', this)">Wrong</button>
    `;
    div.appendChild(el);
  });
}

function renderSimilarity(rows) {
  const div = document.getElementById('similarity');
  if (!rows || rows.length === 0) { div.innerHTML = '<div class="empty">nothing pending</div>'; return; }
  div.innerHTML = '';
  rows.forEach(r => {
    const el = document.createElement('div');
    el.className = 'row';
    el.innerHTML = `
      <div class="meta">triage ${r.triage_id} · ${r.created_at} · ${r.latency_ms}ms</div>
      <div><strong>Enquiry summary:</strong></div>
      <pre>${r.enquiry_summary}</pre>
      <div><strong>Surfaced candidates:</strong></div>
      <pre>${JSON.stringify(r.candidates, null, 2)}</pre>
      <button class="primary" onclick="review('triage_similarity', ${r.id}, 'good', this)">Good</button>
      <button class="neutral" onclick="review('triage_similarity', ${r.id}, 'partial', this)">Partial</button>
      <button class="danger" onclick="review('triage_similarity', ${r.id}, 'wrong', this)">Wrong</button>
    `;
    div.appendChild(el);
  });
}

async function review(source, id, verdict, btn) {
  const r = await api('/admin/shadow/review', {
    method: 'POST',
    body: JSON.stringify({source, id, verdict}),
  });
  if (r && r.ok) {
    btn.parentElement.style.opacity = '0.5';
    btn.parentElement.querySelectorAll('button').forEach(b => b.disabled = true);
    btn.innerHTML = '<strong>' + btn.innerHTML + ' ✓</strong>';
  }
}

async function refreshAll() {
  const s = await api('/admin/shadow/summary');
  renderSummary(s);
  const ar = await api('/admin/shadow/arxiv?verdict=pending&limit=20');
  renderArxiv(ar?.rows);
  const cb = await api('/admin/shadow/conversational?source=brief&limit=20');
  renderConversational('conv_brief', cb?.rows);
  const ct = await api('/admin/shadow/conversational?source=triage&limit=20');
  renderConversational('conv_triage', ct?.rows);
  const sim = await api('/admin/shadow/triage-similarity?limit=20');
  renderSimilarity(sim?.rows);
}
</script>
</body>
</html>
"""


@router.get('/review-ui', response_class=HTMLResponse, include_in_schema=False)
async def review_ui() -> HTMLResponse:
    """Open HTML endpoint — auth happens client-side via paste-in
    token. This keeps the page loadable in a browser without
    auth-wrapping every static asset."""
    return HTMLResponse(_REVIEW_UI_HTML)
