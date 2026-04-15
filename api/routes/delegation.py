"""Cross-module delegation API.

Mounted at ``/api/delegation/*`` in the Cairn FastAPI app.

``POST /api/delegation/call`` — invoked by the ``cairn_delegate`` MCP tool.
Routes a single one-shot request to Grok 4 Fast (generate) or Claude Haiku 4.5
(review / extract / classify) via OpenRouter, with call-level cost logging
into ``cairn_delegation_log`` (SQLite, ``CLAW_DATA_DIR/claw.db``).

Commit 1: scaffold only. Request is validated, table is ensured, a stub
response is returned so the MCP tool registration can be exercised end-to-end.
Commit 2 wires the real OpenRouter call, schema validation, cost logging,
and outcome categorisation.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.middleware.auth import verify_api_key
from core.delegation import log as delegation_log
from core.delegation.router import (
    VALID_TASK_TYPES,
    VALID_TIER_OVERRIDES,
    route,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/delegation",
    tags=["Delegation"],
    dependencies=[Depends(verify_api_key)],
)


class DelegateRequest(BaseModel):
    task_type: str = Field(..., description="generate | review | extract | classify")
    instructions: str = Field(..., min_length=1)
    context: Optional[str] = None
    output_schema: Optional[dict[str, Any]] = None
    max_tokens: int = Field(4000, ge=1, le=32000)
    tier_override: Optional[str] = Field(
        None, description="grok_fast | haiku | null — overrides task_type routing"
    )
    delegating_session: str = Field(..., min_length=1)
    rationale: str = Field(..., min_length=1)


class DelegateResponse(BaseModel):
    response: str
    parsed: Optional[Any] = None
    model_used: str
    tokens_in: int
    tokens_out: int
    cost_gbp: float
    duration_ms: int
    schema_valid: bool
    warnings: list[str] = []


@router.post("/call", response_model=DelegateResponse)
async def delegation_call(body: DelegateRequest) -> DelegateResponse:
    # Input validation beyond what pydantic catches.
    if body.task_type not in VALID_TASK_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"task_type must be one of {sorted(VALID_TASK_TYPES)}; "
                f"got {body.task_type!r}"
            ),
        )
    if body.tier_override is not None and body.tier_override not in VALID_TIER_OVERRIDES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"tier_override must be one of {sorted(VALID_TIER_OVERRIDES)} "
                f"or null; got {body.tier_override!r}"
            ),
        )

    # Ensure the log table exists on first call so Commit 2 can assume it.
    delegation_log.ensure_table()

    model = route(body.task_type, body.tier_override)

    # Commit 1 scaffold: return a well-formed stub without hitting OpenRouter.
    # Commit 2 replaces this with the real call, schema validation, cost calc,
    # and log insert.
    return DelegateResponse(
        response="[scaffold] cairn_delegate is registered but not yet wired to OpenRouter (Commit 2).",
        parsed=None,
        model_used=model,
        tokens_in=0,
        tokens_out=0,
        cost_gbp=0.0,
        duration_ms=0,
        schema_valid=body.output_schema is None,
        warnings=["scaffold: OpenRouter call not yet implemented in this commit"],
    )
