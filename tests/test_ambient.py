"""
Tests for api/routes/ambient.py — Phase 0 endpoints.

These tests use stub DB helpers so they don't need a live Postgres.
Integration coverage happens via live curl against Hetzner after deploy.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


MANUFACTURE_MD_SAMPLE = """# manufacture live snapshot

Generated at 2026-04-18T10:36:19.459986+00:00 by Manufacture /api/cairn/snapshot

## Production — open orders

- Total open orders: **10**
- By machine:
  - ROLF: 6 orders, 119 units
  - MIMAKI: 4 orders, 18 units
- By simple stage:
  - in_process: 2
  - on_bench: 6

## Stock — deficits

- Products below target: **204**
- Total deficit units: **2847**
- Products at zero stock: **1746**
- Top 10 by deficit:
  - M0634: 0 on hand, 962 short
  - M0008: 19 on hand, 115 short
  - M0015: 88 on hand, 84 short
  - M0002: 198 on hand, 78 short
  - M0682: 0 on hand, 68 short
"""


CRM_MD_SAMPLE = """# CRM pipeline snapshot

**Pipeline value**: £57,922.5 across 85 active projects
**EV-weighted pipeline**: £18,244.35 (21 projects with EV)
**Open loops**: 0
**Recent activity (7d)**: 115
**Follow-ups overdue**: 3
**Stale leads (14+ days)**: 0
"""


LEDGER_MD_SAMPLE = """## Ledger — Financial Snapshot

**Cash Position:** £28,275 (wise_nbne_main: £7,167)
**Revenue MTD:** £41,098
**Revenue YTD:** £271,520
**Gross margin MTD:** 79.8%
"""


class TestParsers:

    def test_parse_manufacture(self):
        from api.routes.ambient import _parse_manufacture_snapshot
        out = _parse_manufacture_snapshot(MANUFACTURE_MD_SAMPLE)
        assert out["open_orders"] == 10
        assert out["rolf_units"] == {"orders": 6, "units": 119}
        assert out["mimaki_units"] == {"orders": 4, "units": 18}
        assert len(out["top_deficits"]) == 5
        assert out["top_deficits"][0] == {"sku": "M0634", "on_hand": 0, "short": 962}

    def test_parse_crm(self):
        from api.routes.ambient import _parse_crm_snapshot
        out = _parse_crm_snapshot(CRM_MD_SAMPLE)
        assert out["pipeline_value"] == 57922.5
        assert out["active_projects"] == 85
        assert out["follow_ups_overdue"] == 3
        assert out["stale_leads"] == 0
        assert out["recent_activity_7d"] == 115

    def test_parse_ledger(self):
        from api.routes.ambient import _parse_ledger_snapshot
        out = _parse_ledger_snapshot(LEDGER_MD_SAMPLE)
        assert out["cash_position"] == 28275
        assert out["revenue_mtd"] == 41098
        assert out["revenue_ytd"] == 271520
        assert out["gross_margin_mtd"] == 79.8


class TestStaleness:

    def test_fresh_snapshot_not_stale(self):
        from api.routes.ambient import _is_stale
        fresh = datetime.now(timezone.utc) - timedelta(minutes=15)
        assert _is_stale(fresh) is False

    def test_old_snapshot_stale(self):
        from api.routes.ambient import _is_stale
        old = datetime.now(timezone.utc) - timedelta(hours=3)
        assert _is_stale(old) is True

    def test_none_is_stale(self):
        from api.routes.ambient import _is_stale
        assert _is_stale(None) is True

    def test_naive_datetime_treated_as_utc(self):
        from api.routes.ambient import _is_stale
        # Naive datetime should be coerced to UTC, not crash
        naive_fresh = datetime.utcnow() - timedelta(minutes=15)
        assert _is_stale(naive_fresh) is False


class TestMorningNumberLocations:

    def _patch_snapshot_loader(self, module_md_map):
        """Return a patcher that stubs _load_snapshot for the given modules."""
        def fake_load(module):
            md = module_md_map.get(module)
            if md is None:
                return None, None
            return md, datetime.now(timezone.utc) - timedelta(minutes=10)
        return patch("api.routes.ambient._load_snapshot", side_effect=fake_load)

    def test_workshop_reads_manufacture(self):
        from api.routes.ambient import _morning_number_workshop
        with self._patch_snapshot_loader({"manufacture": MANUFACTURE_MD_SAMPLE}):
            mn = _morning_number_workshop()
        assert mn.source_module == "manufacture"
        assert mn.stale is False
        assert mn.number == "10"
        assert "10" in mn.headline
        # 119 + 18 = 137 units across 10 orders
        assert "137" in mn.subtitle and "10" in mn.subtitle

    def test_office_reads_crm(self):
        from api.routes.ambient import _morning_number_office
        with self._patch_snapshot_loader({"crm": CRM_MD_SAMPLE}):
            mn = _morning_number_office()
        assert mn.source_module == "crm"
        assert mn.stale is False
        # 3 follow-ups overdue takes precedence over project count
        assert mn.number == "3"
        assert "follow-up" in mn.headline.lower()
        assert "57,922" in mn.subtitle or "57923" in mn.subtitle or "58" in mn.subtitle

    def test_home_reads_ledger(self):
        from api.routes.ambient import _morning_number_home
        with self._patch_snapshot_loader({"ledger": LEDGER_MD_SAMPLE}):
            mn = _morning_number_home()
        assert mn.source_module == "ledger"
        assert mn.stale is False
        assert "28,275" in mn.number or "28275" in mn.number

    def test_missing_snapshot_returns_stale(self):
        from api.routes.ambient import _morning_number_workshop
        with self._patch_snapshot_loader({}):
            mn = _morning_number_workshop()
        assert mn.stale is True
        assert mn.number == "—"

    def test_old_snapshot_flagged_stale(self):
        from api.routes.ambient import _morning_number_workshop
        def fake_load(_module):
            return MANUFACTURE_MD_SAMPLE, datetime.now(timezone.utc) - timedelta(hours=5)
        with patch("api.routes.ambient._load_snapshot", side_effect=fake_load):
            mn = _morning_number_workshop()
        assert mn.stale is True


class TestHTTPEndpoints:
    """End-to-end tests via TestClient. Stubs DB so no Postgres needed."""

    @pytest.fixture
    def client(self, monkeypatch):
        # Stub _load_snapshot at module level
        def fake_load(module):
            md_map = {
                "manufacture": MANUFACTURE_MD_SAMPLE,
                "crm": CRM_MD_SAMPLE,
                "ledger": LEDGER_MD_SAMPLE,
            }
            md = md_map.get(module)
            if md is None:
                return None, None
            return md, datetime.now(timezone.utc) - timedelta(minutes=5)
        monkeypatch.setattr("api.routes.ambient._load_snapshot", fake_load)
        # Stub the triage counts so we don't need DB
        monkeypatch.setattr(
            "api.routes.ambient._inbox_triage_counts",
            lambda: {"total": 5, "new_enquiry": 2, "existing_project_reply": 3, "unread": 1},
        )
        from api.main import app
        tc = TestClient(app)
        import os
        headers = {"X-API-Key": os.environ.get("DEEK_API_KEY", "deek-dev-key-change-in-production")}
        return tc, headers

    def test_morning_number_workshop(self, client):
        tc, headers = client
        r = tc.get("/api/deek/morning-number?location=workshop", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["source_module"] == "manufacture"
        assert data["stale"] is False

    def test_morning_number_invalid_location(self, client):
        tc, headers = client
        r = tc.get("/api/deek/morning-number?location=moon", headers=headers)
        assert r.status_code == 400

    def test_morning_number_also_via_cairn_alias(self, client):
        tc, headers = client
        # Dual-mount: /api/cairn/morning-number should work IF we also mount
        # the ambient router under /api/cairn. Currently we only mount the
        # federation router there. So this SHOULD fail — confirming we've
        # not accidentally exposed the new routes under the legacy prefix.
        r = tc.get("/api/cairn/morning-number?location=workshop", headers=headers)
        assert r.status_code == 404

    def test_ambient_workshop(self, client):
        tc, headers = client
        r = tc.get("/api/deek/ambient?location=workshop", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["location"] == "workshop"
        assert data["morning_number"]["source_module"] == "manufacture"
        panel_ids = [p["id"] for p in data["panels"]]
        assert "machine_status" in panel_ids
        assert "stock_deficits" in panel_ids

    def test_ambient_office_includes_inbox(self, client):
        tc, headers = client
        r = tc.get("/api/deek/ambient?location=office", headers=headers)
        assert r.status_code == 200
        panel_ids = [p["id"] for p in r.json()["panels"]]
        assert "inbox_triage" in panel_ids
        assert "crm_followups" in panel_ids

    def test_ambient_home_includes_financial(self, client):
        tc, headers = client
        r = tc.get("/api/deek/ambient?location=home", headers=headers)
        assert r.status_code == 200
        panel_ids = [p["id"] for p in r.json()["panels"]]
        assert "financial_health" in panel_ids
