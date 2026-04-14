"""
Cairn Counterparty Risk proxy endpoint.

Proxies to CRM's counterparty-risk API. Sub-agents consume this endpoint
to get terms profiles — they never access CRM's database directly.

Phase 0: read-only proxy. The CRM owns the data and access control.
"""

import os
import httpx
from fastapi import APIRouter, Depends, HTTPException
from api.middleware.auth import verify_api_key

router = APIRouter(
    prefix="/api/counterparty-risk",
    tags=["counterparty-risk"],
)

_CRM_BASE = os.getenv("CRM_BASE_URL", "https://crm.nbnesigns.co.uk")
_CRM_API_KEY = os.getenv("CAIRN_API_KEY", "")


async def _proxy_to_crm(path: str) -> dict:
    """Forward a GET request to the CRM counterparty-risk API."""
    url = f"{_CRM_BASE}/api/counterparty-risk/{path}"
    headers = {
        "Authorization": f"Bearer {_CRM_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, headers=headers)

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Risk profile not found")
    if resp.status_code == 403:
        raise HTTPException(status_code=403, detail="Access denied by CRM")
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"CRM returned {resp.status_code}: {resp.text[:200]}",
        )

    return resp.json()


@router.get(
    "/{counterparty_risk_id}",
    summary="Get counterparty terms profile",
    description=(
        "Returns the terms profile for a counterparty. Consumed by PM agent "
        "and other sub-agents. Sub-agents see band + terms only (no signals)."
    ),
    dependencies=[Depends(verify_api_key)],
)
async def get_counterparty_risk(counterparty_risk_id: str):
    """
    Proxy to CRM: GET /api/counterparty-risk/{id}

    The CRM enforces access control based on the session role.
    This endpoint uses the Cairn service account which gets sub-agent
    level access (band + terms profile only, no signal evidence).
    """
    data = await _proxy_to_crm(counterparty_risk_id)

    # Strip any fields that sub-agents should not see (defence in depth)
    # CRM already filters based on role, but we enforce here too
    return {
        "id": data.get("id"),
        "counterpartyId": data.get("counterpartyId"),
        "counterpartyName": data.get("counterpartyName"),
        "band": data.get("band"),
        "matrixCell": data.get("matrixCell"),
        "confidence": data.get("confidence"),
        "hardNoFlag": data.get("hardNoFlag"),
        "termsProfile": data.get("termsProfile"),
    }
