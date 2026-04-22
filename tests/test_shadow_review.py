"""Tests for /admin/shadow/* endpoints.

Focus on the POST /review validation + routing. The listing
endpoints hit the DB so are smoke-tested via the summary which
at least exercises connection + SQL shape.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """FastAPI test client with env set up so verify_api_key accepts a
    fixture token. DB calls are monkeypatched per-test."""
    monkeypatch.setenv('DEEK_API_KEY', 'test-token')
    from api.main import app
    return TestClient(app)


AUTH = {'X-API-Key': 'test-token'}


class TestReviewPostValidation:
    def test_missing_auth(self, client):
        r = client.post(
            '/admin/shadow/review',
            json={'source': 'arxiv', 'id': 1, 'verdict': 'yes'},
        )
        # Either 401 / 403 depending on auth middleware defaults —
        # the important thing is NOT 200.
        assert r.status_code in (401, 403)

    def test_unknown_source(self, client):
        r = client.post(
            '/admin/shadow/review',
            json={'source': 'bogus', 'id': 1, 'verdict': 'yes'},
            headers=AUTH,
        )
        assert r.status_code == 400
        assert 'unknown source' in r.text.lower()

    def test_invalid_verdict_for_source(self, client):
        r = client.post(
            '/admin/shadow/review',
            json={'source': 'arxiv', 'id': 1, 'verdict': 'maybe'},
            headers=AUTH,
        )
        assert r.status_code == 400
        assert 'invalid verdict' in r.text.lower()

    def test_invalid_verdict_for_conversational(self, client):
        r = client.post(
            '/admin/shadow/review',
            json={'source': 'conversational', 'id': 1, 'verdict': 'yes'},
            headers=AUTH,
        )
        assert r.status_code == 400

    def test_non_integer_id(self, client):
        r = client.post(
            '/admin/shadow/review',
            json={'source': 'arxiv', 'id': 'abc', 'verdict': 'yes'},
            headers=AUTH,
        )
        assert r.status_code == 400
        assert 'integer' in r.text.lower()


class TestReviewPostDbPath:
    def test_happy_arxiv(self, client, monkeypatch):
        """Stub the DB — verify the SQL path + response shape."""
        class _Cur:
            def __init__(self): self.sql = ''
            def execute(self, sql, params):
                self.sql = sql
            def fetchone(self):
                return [42]
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class _Conn:
            def cursor(self): return _Cur()
            def commit(self): pass
            def close(self): pass

        monkeypatch.setattr(
            'api.routes.shadow_review._connect', lambda: _Conn(),
        )
        r = client.post(
            '/admin/shadow/review',
            json={'source': 'arxiv', 'id': 42, 'verdict': 'yes'},
            headers=AUTH,
        )
        assert r.status_code == 200
        body = r.json()
        assert body['ok'] is True
        assert body['id'] == 42
        assert body['verdict'] == 'yes'

    def test_row_not_found_404(self, client, monkeypatch):
        class _Cur:
            def execute(self, sql, params): pass
            def fetchone(self): return None
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class _Conn:
            def cursor(self): return _Cur()
            def commit(self): pass
            def close(self): pass

        monkeypatch.setattr(
            'api.routes.shadow_review._connect', lambda: _Conn(),
        )
        r = client.post(
            '/admin/shadow/review',
            json={'source': 'arxiv', 'id': 999, 'verdict': 'no'},
            headers=AUTH,
        )
        assert r.status_code == 404

    def test_triage_similarity_useful_index_mapping(self, client, monkeypatch):
        """'good'/'partial'/'wrong' → useful_index 1/2/0."""
        captured_params: list = []
        class _Cur:
            def execute(self, sql, params):
                captured_params.append(params)
            def fetchone(self):
                return [1]
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class _Conn:
            def cursor(self): return _Cur()
            def commit(self): pass
            def close(self): pass

        monkeypatch.setattr(
            'api.routes.shadow_review._connect', lambda: _Conn(),
        )
        for verdict, expected_index in [
            ('good', 1), ('partial', 2), ('wrong', 0),
        ]:
            captured_params.clear()
            r = client.post(
                '/admin/shadow/review',
                json={'source': 'triage_similarity',
                      'id': 1, 'verdict': verdict},
                headers=AUTH,
            )
            assert r.status_code == 200
            # First param in UPDATE is the useful_index value
            assert captured_params[0][0] == expected_index


class TestReviewUi:
    def test_ui_served_without_auth(self, client):
        """The UI HTML is open — auth happens client-side."""
        r = client.get('/admin/shadow/review-ui')
        assert r.status_code == 200
        assert 'Deek' in r.text
        assert 'Shadow Review' in r.text
        # Sanity: the JS paste-in-token pattern is there
        assert 'X-API-Key' in r.text
