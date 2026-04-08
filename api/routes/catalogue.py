"""
Cairn catalogue endpoint.

GET /api/cairn/catalogue — single call at session start to discover the
ecosystem: which modules exist, which wiki articles are current, which context
endpoints are live, how many pgvector chunks per project, and audit warnings.

Response is cached for 60 seconds to avoid repeated work within a session.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from api.middleware.auth import verify_api_key
from core.catalogue.builder import build_catalogue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cairn", tags=["Cairn"])


@router.get("/catalogue")
async def cairn_catalogue(
    _: bool = Depends(verify_api_key),
):
    """
    Return the current state of the Cairn ecosystem.

    Includes: registered modules, wiki article status, pgvector chunk counts,
    recompile queue state, and daily audit warnings.

    Cached for 60 seconds. Parallel HTTP checks for context endpoint reachability.

    Call this at the start of every CC session:
        GET http://localhost:8765/api/cairn/catalogue
    """
    return await build_catalogue()
