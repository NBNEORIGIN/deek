"""Unit tests for Triage Phase D — similar-past-jobs surfacing.

Covers:
  - Reranker: same-client + won-status + folder boosts
  - Shadow-mode gating
  - Q5 classification on the reply parser
  - Digest block rendering

DB-dependent paths (find_and_log, _mark_similar_job_useful,
log_similarity_debug) are smoke-tested with stub connections.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core.triage.similar_jobs import (
    SimilarJob,
    _classify_status,
    _summarise_content,
    find_similar_jobs,
    is_similarity_shadow,
)
from core.triage.replies import (
    _classify_similar_job_useful,
    parse_reply_body,
)


# ── _classify_status ───────────────────────────────────────────────

class TestClassifyStatus:
    def test_won_upper(self):
        assert _classify_status('WON') == 'won'

    def test_won_variants(self):
        assert _classify_status('INVOICED') == 'won'
        assert _classify_status('COMPLETED') == 'won'

    def test_lost(self):
        assert _classify_status('LOST') == 'lost'
        assert _classify_status('cancelled') == 'lost'

    def test_quoted(self):
        assert _classify_status('QUOTED') == 'quoted'

    def test_in_progress(self):
        assert _classify_status('LEAD') == 'in_progress'

    def test_unknown_passthrough_lower(self):
        assert _classify_status('SomeOtherStage') == 'someotherstage'

    def test_none(self):
        assert _classify_status(None) is None
        assert _classify_status('') is None


# ── _summarise_content ─────────────────────────────────────────────

class TestSummariseContent:
    def test_trims_update_blocks(self):
        content = (
            'Shop fascia sign for coffee shop. Needs LED backlighting.'
            '\n\n--- Update 3/12/2026 ---\n'
            'Quote sent £2,800+VAT'
        )
        out = _summarise_content(content)
        assert 'Quote sent' not in out
        assert 'Shop fascia' in out or 'coffee shop' in out

    def test_drops_field_prefixes(self):
        content = 'Signs for Julie. Client: Flowers by Julie. Status: WON'
        out = _summarise_content(content)
        assert 'Signs for Julie' in out
        assert 'Status: WON' not in out

    def test_empty(self):
        assert _summarise_content('') == ''
        assert _summarise_content(None) == ''

    def test_truncates_long(self):
        content = 'Sentence. ' * 200
        out = _summarise_content(content, max_chars=100)
        assert len(out) <= 101  # trailing ellipsis


# ── Reranker ───────────────────────────────────────────────────────

def _fake_crm_response(results: list[dict]):
    """Return a fake httpx.Client whose .get returns a mock response
    object with a json() method returning ``{'results': results}``."""
    class _R:
        status_code = 200
        text = ''
        def json(self):
            return {'results': results}

    class _C:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k):
            return _R()

    return _C


class TestFindSimilarJobs:
    def test_empty_summary(self, monkeypatch):
        monkeypatch.setenv('DEEK_API_KEY', 'test')
        assert find_similar_jobs('') == []
        assert find_similar_jobs('   ') == []

    def test_no_token(self, monkeypatch):
        for var in ('DEEK_API_KEY', 'CAIRN_API_KEY', 'CLAW_API_KEY'):
            monkeypatch.delenv(var, raising=False)
        assert find_similar_jobs('coffee shop fascia') == []

    def test_basic_shape(self, monkeypatch):
        monkeypatch.setenv('DEEK_API_KEY', 'test')
        results = [{
            'source_id': 'pid-1',
            'source_type': 'project',
            'content': 'Shop fascia signs. Client: Flowers by Julie. Status: WON.',
            'metadata': {
                'project_name': 'Flowers by Julie fascia',
                'client': 'Flowers by Julie',
                'stage': 'WON',
                'value': 2850,
            },
            'score': 0.05,
        }]
        with patch('httpx.Client', _fake_crm_response(results)):
            jobs = find_similar_jobs('coffee shop fascia signs')
        assert len(jobs) == 1
        assert jobs[0].project_id == 'pid-1'
        assert jobs[0].status == 'won'
        assert jobs[0].quoted_amount == 2850.0
        # Reranker: won gets a boost
        assert jobs[0].score > jobs[0].raw_score

    def test_excludes_self(self, monkeypatch):
        monkeypatch.setenv('DEEK_API_KEY', 'test')
        results = [
            {'source_id': 'pid-self', 'source_type': 'project',
             'content': 'X', 'metadata': {'stage': 'WON'}, 'score': 0.05},
            {'source_id': 'pid-other', 'source_type': 'project',
             'content': 'Y', 'metadata': {'stage': 'WON'}, 'score': 0.03},
        ]
        with patch('httpx.Client', _fake_crm_response(results)):
            jobs = find_similar_jobs('x', exclude_project_id='pid-self')
        ids = [j.project_id for j in jobs]
        assert 'pid-self' not in ids
        assert 'pid-other' in ids

    def test_same_client_boost(self, monkeypatch):
        """A same-client job should outrank a higher-raw-score stranger."""
        monkeypatch.setenv('DEEK_API_KEY', 'test')
        results = [
            {'source_id': 'stranger', 'source_type': 'project',
             'content': 'X', 'metadata': {'stage': 'QUOTED',
             'client': 'Somebody Else'}, 'score': 0.06},
            {'source_id': 'home', 'source_type': 'project',
             'content': 'X', 'metadata': {'stage': 'QUOTED',
             'client': 'Flowers by Julie'}, 'score': 0.04},
        ]
        with patch('httpx.Client', _fake_crm_response(results)):
            jobs = find_similar_jobs('x', client_name='Flowers by Julie')
        assert jobs[0].project_id == 'home'

    def test_below_threshold_dropped(self, monkeypatch):
        monkeypatch.setenv('DEEK_API_KEY', 'test')
        # raw_score + any boost still < DEFAULT_MIN_SCORE (0.02)
        results = [{
            'source_id': 'tiny', 'source_type': 'project',
            'content': 'X', 'metadata': {'stage': 'LEAD'},
            'score': 0.005,
        }]
        with patch('httpx.Client', _fake_crm_response(results)):
            jobs = find_similar_jobs('x')
        assert jobs == []

    def test_won_before_lost_at_equal_score(self, monkeypatch):
        monkeypatch.setenv('DEEK_API_KEY', 'test')
        # Same raw score — tiebreak on status (won before lost)
        results = [
            {'source_id': 'lost', 'source_type': 'project',
             'content': 'X', 'metadata': {'stage': 'LOST'}, 'score': 0.06},
            {'source_id': 'won', 'source_type': 'project',
             'content': 'X', 'metadata': {'stage': 'WON'}, 'score': 0.06},
        ]
        with patch('httpx.Client', _fake_crm_response(results)):
            jobs = find_similar_jobs('x')
        # won gets +0.03 boost → leads naturally
        assert jobs[0].project_id == 'won'

    def test_network_error_returns_empty(self, monkeypatch):
        monkeypatch.setenv('DEEK_API_KEY', 'test')
        class _ExplodingClient:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def get(self, *a, **k):
                raise RuntimeError('boom')
        with patch('httpx.Client', _ExplodingClient):
            assert find_similar_jobs('x') == []

    def test_lost_jobs_included(self, monkeypatch):
        """Per Phase D decisions: include lost jobs, no penalty."""
        monkeypatch.setenv('DEEK_API_KEY', 'test')
        results = [{
            'source_id': 'lost-1', 'source_type': 'project',
            'content': 'X', 'metadata': {'stage': 'LOST'}, 'score': 0.05,
        }]
        with patch('httpx.Client', _fake_crm_response(results)):
            jobs = find_similar_jobs('x')
        assert len(jobs) == 1
        assert jobs[0].status == 'lost'


# ── Shadow mode ─────────────────────────────────────────────────────

class TestShadowMode:
    def test_default_is_shadow(self, monkeypatch):
        monkeypatch.delenv('DEEK_SIMILARITY_SHADOW', raising=False)
        assert is_similarity_shadow() is True

    def test_explicit_false(self, monkeypatch):
        monkeypatch.setenv('DEEK_SIMILARITY_SHADOW', 'false')
        assert is_similarity_shadow() is False

    def test_explicit_true(self, monkeypatch):
        monkeypatch.setenv('DEEK_SIMILARITY_SHADOW', 'true')
        assert is_similarity_shadow() is True

    def test_yes(self, monkeypatch):
        monkeypatch.setenv('DEEK_SIMILARITY_SHADOW', 'yes')
        assert is_similarity_shadow() is True

    def test_zero_is_off(self, monkeypatch):
        monkeypatch.setenv('DEEK_SIMILARITY_SHADOW', '0')
        assert is_similarity_shadow() is False


# ── SimilarJob serialisation ────────────────────────────────────────

class TestSimilarJobDataclass:
    def test_to_json_roundtrips(self):
        j = SimilarJob(
            project_id='pid', project_name='n', client_name='c',
            quoted_amount=100.0, quoted_currency='GBP',
            status='won', summary='s', score=0.5, raw_score=0.4,
            has_local_folder=True,
        )
        d = j.to_json()
        assert d['project_id'] == 'pid'
        assert d['quoted_amount'] == 100.0
        assert d['has_local_folder'] is True


# ── Q5 classifier ───────────────────────────────────────────────────

class TestQ5Classifier:
    def test_empty(self):
        a = _classify_similar_job_useful('')
        assert a.verdict == 'empty'

    def test_skip(self):
        assert _classify_similar_job_useful('SKIP').verdict == 'empty'
        assert _classify_similar_job_useful('skip').verdict == 'empty'
        assert _classify_similar_job_useful('none').verdict == 'empty'
        assert _classify_similar_job_useful('-').verdict == 'empty'

    def test_candidate_number(self):
        a = _classify_similar_job_useful('2')
        assert a.verdict == 'select_candidate'
        assert a.selected_candidate_index == 2

    def test_candidate_with_whitespace(self):
        a = _classify_similar_job_useful('  1  ')
        assert a.verdict == 'select_candidate'
        assert a.selected_candidate_index == 1

    def test_free_text(self):
        a = _classify_similar_job_useful(
            "Actually the Hanmade Bakes one was closer"
        )
        assert a.verdict == 'text'
        assert 'Hanmade' in a.free_text


# ── Full reply parse including Q5 ──────────────────────────────────

class TestParseReplyWithQ5:
    def test_q5_picked(self):
        body = """\
--- Q1 (match_confirm) ---
YES

--- Q2 (reply_approval) ---
USE

--- Q3 (project_folder) ---

--- Q4 (notes) ---

--- Q5 (similar_job_useful) ---
2
"""
        reply = parse_reply_body(body, 'toby@x', 155)
        assert len(reply.answers) == 5
        q5 = reply.answers[4]
        assert q5.category == 'similar_job_useful'
        assert q5.verdict == 'select_candidate'
        assert q5.selected_candidate_index == 2

    def test_q5_skip(self):
        body = """\
--- Q1 (match_confirm) ---
YES

--- Q5 (similar_job_useful) ---
SKIP
"""
        reply = parse_reply_body(body, 'toby@x', 155)
        q5 = [a for a in reply.answers if a.category == 'similar_job_useful']
        assert len(q5) == 1
        assert q5[0].verdict == 'empty'


# ── Digest block rendering ──────────────────────────────────────────

class TestDigestBlock:
    def test_empty_jobs_emits_nothing(self):
        from scripts.email_triage.digest_sender import _build_similar_jobs_block
        assert _build_similar_jobs_block([]) == []

    def test_renders_basic(self):
        from scripts.email_triage.digest_sender import _build_similar_jobs_block
        jobs = [SimilarJob(
            project_id='pid-1', project_name='Flowers by Julie',
            client_name='Julie', quoted_amount=2850.0,
            quoted_currency='GBP', status='won',
            summary='Shop fascia + window vinyl', score=0.67,
            raw_score=0.6, has_local_folder=True,
        )]
        out = '\n'.join(_build_similar_jobs_block(jobs))
        assert 'SIMILAR PAST JOBS' in out
        assert 'pid-1' in out
        assert 'Flowers by Julie' in out
        assert '£2,850' in out
        assert 'won' in out
        assert '0.670' in out

    def test_reply_block_q5_gated(self):
        from scripts.email_triage.digest_sender import _build_reply_back_block
        without = '\n'.join(_build_reply_back_block({}, include_q5=False))
        with_q5 = '\n'.join(_build_reply_back_block({}, include_q5=True))
        assert 'Q5 (similar_job_useful)' not in without
        assert 'Q5 (similar_job_useful)' in with_q5
