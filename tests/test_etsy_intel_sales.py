"""
Tests for the /etsy/sales endpoint (Phase 2B.0(a) of the manufacture
Sales Velocity feature).

This endpoint is consumed by the manufacture app's EtsyAdapter as a
cross-module read. It is explicitly authenticated via the existing
`verify_api_key` dependency (X-API-Key header), whereas the other
/etsy/* routes are currently unauthenticated.

The test suite uses a minimal FastAPI app that mounts only the etsy
router — the heavier `client` fixture in `test_claw.py` loads all
projects and mocks Claude, which is overkill for a single DB-backed
endpoint.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def minimal_app():
    """A FastAPI app with only the etsy router mounted."""
    from api.routes import etsy_intel
    app = FastAPI()
    app.include_router(etsy_intel.router)
    return app


@pytest.fixture
def client(minimal_app):
    return TestClient(minimal_app)


@pytest.fixture
def mock_rows():
    """
    Two clean rows + one NULL-sku row + one multi-sku row, so the
    defensive filtering can be asserted in a single fixture.
    """
    now = datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc)
    three_days_ago = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    ten_days_ago = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    return [
        # (shop_id, listing_id, sku, quantity, first_sale, last_sale)
        (11706740, 2001, "NBN-M0823-SM-OAK", 7, ten_days_ago, now),
        (11706740, 2002, "NBN-M0824-MD-OAK", 3, three_days_ago, three_days_ago),
        (11706740, 2003, None,               9, ten_days_ago, now),  # skip: null
        (11706740, 2004, "NBN-A,NBN-B",      5, ten_days_ago, now),  # skip: multi
    ]


@pytest.fixture
def mock_get_conn(mock_rows):
    """
    Patch `core.etsy_intel.db.get_conn` to return a MagicMock connection
    whose cursor yields the canned row set on fetchall().
    """
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = mock_rows
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=None)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=None)

    with patch("core.etsy_intel.db.get_conn", return_value=mock_conn):
        yield mock_cursor


# ── Auth ──────────────────────────────────────────────────────────────────────

class TestAuth:
    def test_missing_header_returns_401(self, client, mock_get_conn):
        r = client.get("/etsy/sales")
        assert r.status_code == 401
        assert r.json()["detail"] == "Invalid API key"

    def test_wrong_header_returns_401(self, client, mock_get_conn):
        r = client.get("/etsy/sales", headers={"X-API-Key": "bogus"})
        assert r.status_code == 401

    def test_correct_header_returns_200(self, client, mock_get_conn, auth_headers):
        r = client.get("/etsy/sales", headers=auth_headers)
        assert r.status_code == 200


# ── Response shape ────────────────────────────────────────────────────────────

class TestResponseShape:
    def test_returns_window_metadata(self, client, mock_get_conn, auth_headers):
        r = client.get("/etsy/sales", headers=auth_headers)
        body = r.json()
        assert body["window_days"] == 30
        assert "window_end" in body
        # ISO 8601
        datetime.fromisoformat(body["window_end"])
        assert body["shop_id_filter"] is None

    def test_returns_pre_aggregated_rows(self, client, mock_get_conn, auth_headers):
        body = client.get("/etsy/sales", headers=auth_headers).json()
        # 4 raw rows → 2 clean rows (1 null-sku + 1 multi-sku skipped)
        assert body["row_count"] == 2
        assert len(body["rows"]) == 2
        assert body["skipped_null_sku"] == 1
        assert body["skipped_multi_sku"] == 1

    def test_row_keys(self, client, mock_get_conn, auth_headers):
        body = client.get("/etsy/sales", headers=auth_headers).json()
        row = body["rows"][0]
        for key in (
            "shop_id", "listing_id", "external_sku",
            "total_quantity", "first_sale_date", "last_sale_date",
        ):
            assert key in row, f"missing key: {key}"

    def test_first_row_values(self, client, mock_get_conn, auth_headers):
        body = client.get("/etsy/sales", headers=auth_headers).json()
        row = body["rows"][0]
        assert row["shop_id"] == 11706740
        assert row["listing_id"] == 2001
        assert row["external_sku"] == "NBN-M0823-SM-OAK"
        assert row["total_quantity"] == 7
        # ISO 8601 strings
        datetime.fromisoformat(row["first_sale_date"])
        datetime.fromisoformat(row["last_sale_date"])

    def test_skipped_rows_not_in_output(self, client, mock_get_conn, auth_headers):
        body = client.get("/etsy/sales", headers=auth_headers).json()
        skus = [r["external_sku"] for r in body["rows"]]
        assert None not in skus
        assert "NBN-A,NBN-B" not in skus


# ── Query params ──────────────────────────────────────────────────────────────

class TestQueryParams:
    def test_days_default_is_30(self, client, mock_get_conn, auth_headers):
        client.get("/etsy/sales", headers=auth_headers)
        # mock_get_conn is the cursor; inspect SQL bind params
        call = mock_get_conn.execute.call_args
        _sql, params = call[0]
        assert params[0] == 30

    def test_days_custom(self, client, mock_get_conn, auth_headers):
        client.get("/etsy/sales?days=7", headers=auth_headers)
        call = mock_get_conn.execute.call_args
        _sql, params = call[0]
        assert params[0] == 7

    def test_days_rejects_zero(self, client, mock_get_conn, auth_headers):
        r = client.get("/etsy/sales?days=0", headers=auth_headers)
        assert r.status_code == 422  # FastAPI Query ge=1

    def test_days_rejects_over_365(self, client, mock_get_conn, auth_headers):
        r = client.get("/etsy/sales?days=400", headers=auth_headers)
        assert r.status_code == 422  # FastAPI Query le=365

    def test_shop_id_filter_passed_to_sql(self, client, mock_get_conn, auth_headers):
        client.get("/etsy/sales?shop_id=11706740", headers=auth_headers)
        call = mock_get_conn.execute.call_args
        _sql, params = call[0]
        # Params: (days, shop_id, shop_id)
        assert params[1] == 11706740
        assert params[2] == 11706740

    def test_shop_id_none_when_omitted(self, client, mock_get_conn, auth_headers):
        client.get("/etsy/sales", headers=auth_headers)
        call = mock_get_conn.execute.call_args
        _sql, params = call[0]
        assert params[1] is None
        assert params[2] is None


# ── Empty and error cases ─────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_result_returns_empty_rows_not_404(
        self, client, minimal_app, auth_headers,
    ):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=None)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=None)

        with patch("core.etsy_intel.db.get_conn", return_value=mock_conn):
            r = client.get("/etsy/sales", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["rows"] == []
        assert body["row_count"] == 0
        assert body["skipped_null_sku"] == 0
        assert body["skipped_multi_sku"] == 0

    def test_db_error_returns_500(self, client, minimal_app, auth_headers):
        with patch(
            "core.etsy_intel.db.get_conn",
            side_effect=RuntimeError("connection refused"),
        ):
            r = client.get("/etsy/sales", headers=auth_headers)
        assert r.status_code == 500
        assert "etsy_sales query failed" in r.json()["detail"]
