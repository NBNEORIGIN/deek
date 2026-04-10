"""
Cairn Social API routes — drafting, proof-reading, refining, and publishing
posts for Jo at NBNE.

Mounted at /social/* in the Cairn FastAPI app. Reference structural pattern:
api/routes/amazon_intel.py.

Phase 1 endpoints:

  GET  /social/health             — module + DB health check
  GET  /social/voice-guide        — current voice guide (read-only)
  POST /social/migrate            — create/upgrade social_* tables

  POST /social/drafts             — create a draft from a brief
  POST /social/drafts/proofread   — proof-read a finished post
  GET  /social/drafts             — list recent drafts
  GET  /social/drafts/{id}        — full draft + variants

  POST /social/variants/{id}/refine    — chat-style refinement of one variant
  POST /social/variants/{id}/regenerate — alternative draft for one platform
  POST /social/variants/{id}/publish   — mark as published, write to memory

  GET  /social/published          — list published variants
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.middleware.auth import verify_api_key

router = APIRouter(prefix='/social', tags=['Cairn Social'])


# ── Request/response models ──────────────────────────────────────────────────

class DraftRequest(BaseModel):
    brief: str
    platforms: list[str] = Field(default_factory=lambda: ['facebook', 'instagram', 'linkedin'])
    content_pillar: Optional[str] = None


class ProofreadRequest(BaseModel):
    original_text: str
    platforms: list[str] = Field(default_factory=lambda: ['facebook', 'instagram', 'linkedin'])
    content_pillar: Optional[str] = None


class RefineRequest(BaseModel):
    instruction: str


class PublishRequest(BaseModel):
    published_url: Optional[str] = None


# ── Health + meta ────────────────────────────────────────────────────────────

@router.get('/health')
async def social_health():
    """Module + DB health check."""
    from core.social.db import get_conn
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM social_draft")
                drafts = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM social_draft_variant")
                variants = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM social_draft_variant WHERE is_published = TRUE"
                )
                published = cur.fetchone()[0]
        return {
            'status': 'ok',
            'module': 'cairn_social',
            'counts': {
                'drafts': drafts,
                'variants': variants,
                'published': published,
            },
        }
    except Exception as exc:
        return {'status': 'error', 'detail': str(exc)}


@router.post('/migrate')
async def social_migrate():
    """Create/upgrade social_* tables. Idempotent."""
    from core.social.db import ensure_schema
    ensure_schema()
    return {'status': 'complete', 'message': 'social_* schema applied'}


@router.get('/voice-guide')
async def get_voice_guide():
    """Return the current voice guide as plain text + version."""
    from core.social.voice_guide import (
        VOICE_GUIDE,
        SEED_VERSION,
        PLATFORMS,
        CONTENT_PILLARS,
        SEED_POSTS,
    )
    return {
        'version': SEED_VERSION,
        'voice_guide': VOICE_GUIDE,
        'platforms': list(PLATFORMS),
        'content_pillars': list(CONTENT_PILLARS),
        'seed_posts': SEED_POSTS,
    }


# ── Draft creation ───────────────────────────────────────────────────────────

def _new_session_id() -> str:
    return f'social_{uuid.uuid4().hex[:12]}'


def _persist_drafts(
    *,
    draft_id: int,
    parsed: dict,
    model: str,
) -> list[dict]:
    """Insert one variant per platform from a parsed Claude response and
    return the variant rows."""
    from core.social.db import insert_variant, get_variant, isoformat_row
    drafts = parsed.get('drafts') or {}
    variants: list[dict] = []
    for platform, content in drafts.items():
        if not isinstance(content, str) or not content.strip():
            continue
        vid = insert_variant(
            draft_id=draft_id,
            platform=platform,
            content=content.strip(),
            generation_model=model,
        )
        v = get_variant(vid)
        if v:
            variants.append(isoformat_row(v))
    return variants


@router.post('/drafts')
async def create_draft_from_brief(
    body: DraftRequest,
    _: bool = Depends(verify_api_key),
):
    """Generate platform drafts from a short brief written by Jo."""
    from core.social.cost import log_social_cost
    from core.social.db import create_draft, recent_published_for_few_shot
    from core.social.drafter import draft_from_brief
    from core.social.voice_guide import SEED_VERSION

    if not body.brief or not body.brief.strip():
        raise HTTPException(400, "brief is required")

    recent = recent_published_for_few_shot(limit=5)

    try:
        parsed, usage = draft_from_brief(
            brief=body.brief,
            platforms=body.platforms,
            content_pillar=body.content_pillar,
            recent_published=recent,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"drafting failed: {exc}")

    detected_pillar = parsed.get('detected_pillar') or body.content_pillar

    draft_id = create_draft(
        source_mode='brief',
        brief_text=body.brief.strip(),
        original_text=None,
        platforms=body.platforms,
        content_pillar=detected_pillar,
        voice_guide_version=SEED_VERSION,
    )

    variants = _persist_drafts(
        draft_id=draft_id,
        parsed=parsed,
        model=usage['model'],
    )

    log_social_cost(
        session_id=_new_session_id(),
        prompt_summary=f"social draft from brief: {body.brief[:80]}",
        model=usage['model'],
        tokens_in=usage['input_tokens'],
        tokens_out=usage['output_tokens'],
        cost_gbp=usage['cost_gbp'],
    )

    return {
        'draft_id': draft_id,
        'detected_pillar': detected_pillar,
        'variants': variants,
        'notes_for_jo': parsed.get('notes_for_jo') or '',
        'usage': usage,
    }


@router.post('/drafts/proofread')
async def create_draft_proofread(
    body: ProofreadRequest,
    _: bool = Depends(verify_api_key),
):
    """Proof-read and adapt a finished post Jo has already written."""
    from core.social.cost import log_social_cost
    from core.social.db import create_draft, recent_published_for_few_shot
    from core.social.drafter import proofread_post
    from core.social.voice_guide import SEED_VERSION

    if not body.original_text or not body.original_text.strip():
        raise HTTPException(400, "original_text is required")

    recent = recent_published_for_few_shot(limit=5)

    try:
        parsed, usage = proofread_post(
            original_text=body.original_text,
            platforms=body.platforms,
            content_pillar=body.content_pillar,
            recent_published=recent,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"proofreading failed: {exc}")

    detected_pillar = parsed.get('detected_pillar') or body.content_pillar

    draft_id = create_draft(
        source_mode='proofread',
        brief_text=None,
        original_text=body.original_text.strip(),
        platforms=body.platforms,
        content_pillar=detected_pillar,
        voice_guide_version=SEED_VERSION,
    )

    variants = _persist_drafts(
        draft_id=draft_id,
        parsed=parsed,
        model=usage['model'],
    )

    log_social_cost(
        session_id=_new_session_id(),
        prompt_summary=f"social proofread: {body.original_text[:80]}",
        model=usage['model'],
        tokens_in=usage['input_tokens'],
        tokens_out=usage['output_tokens'],
        cost_gbp=usage['cost_gbp'],
    )

    return {
        'draft_id': draft_id,
        'detected_pillar': detected_pillar,
        'variants': variants,
        'notes_for_jo': parsed.get('notes_for_jo') or '',
        'usage': usage,
    }


# ── Draft retrieval ──────────────────────────────────────────────────────────

@router.get('/drafts')
async def list_drafts(
    limit: int = Query(30, le=200),
    _: bool = Depends(verify_api_key),
):
    from core.social.db import (
        list_recent_drafts,
        list_variants_for_draft,
        isoformat_row,
    )
    drafts = list_recent_drafts(limit=limit)
    out = []
    for d in drafts:
        variants = list_variants_for_draft(d['id'])
        out.append({
            **isoformat_row(d),
            'variants': [isoformat_row(v) for v in variants],
        })
    return {'drafts': out}


@router.get('/drafts/{draft_id}')
async def get_draft_detail(
    draft_id: int,
    _: bool = Depends(verify_api_key),
):
    from core.social.db import (
        get_draft,
        list_variants_for_draft,
        isoformat_row,
    )
    draft = get_draft(draft_id)
    if not draft:
        raise HTTPException(404, f"Draft {draft_id} not found")
    variants = list_variants_for_draft(draft_id)
    return {
        **isoformat_row(draft),
        'variants': [isoformat_row(v) for v in variants],
    }


# ── Refinement / regeneration ────────────────────────────────────────────────

@router.post('/variants/{variant_id}/refine')
async def refine(
    variant_id: int,
    body: RefineRequest,
    _: bool = Depends(verify_api_key),
):
    """Apply a chat-style refinement instruction to one variant."""
    from core.social.cost import log_social_cost
    from core.social.db import (
        get_variant,
        get_draft,
        insert_variant,
        recent_published_for_few_shot,
        isoformat_row,
    )
    from core.social.drafter import refine_variant

    variant = get_variant(variant_id)
    if not variant:
        raise HTTPException(404, f"Variant {variant_id} not found")

    draft = get_draft(variant['draft_id'])
    if not draft:
        raise HTTPException(404, f"Parent draft {variant['draft_id']} not found")

    try:
        refined_text, usage = refine_variant(
            previous_content=variant['content'],
            platform=variant['platform'],
            instruction=body.instruction,
            original_brief=draft.get('brief_text') or draft.get('original_text'),
            recent_published=recent_published_for_few_shot(limit=5),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"refinement failed: {exc}")

    new_id = insert_variant(
        draft_id=draft['id'],
        platform=variant['platform'],
        content=refined_text,
        generation_model=usage['model'],
        parent_variant_id=variant['id'],
        revision_count=int(variant.get('revision_count') or 0) + 1,
    )
    new_variant = get_variant(new_id)

    log_social_cost(
        session_id=_new_session_id(),
        prompt_summary=f"social refine v{variant_id}: {body.instruction[:80]}",
        model=usage['model'],
        tokens_in=usage['input_tokens'],
        tokens_out=usage['output_tokens'],
        cost_gbp=usage['cost_gbp'],
    )

    return {
        'variant': isoformat_row(new_variant) if new_variant else None,
        'usage': usage,
    }


@router.post('/variants/{variant_id}/regenerate')
async def regenerate(
    variant_id: int,
    _: bool = Depends(verify_api_key),
):
    """Produce an alternative draft for the same platform from the same brief."""
    from core.social.cost import log_social_cost
    from core.social.db import (
        get_variant,
        get_draft,
        insert_variant,
        recent_published_for_few_shot,
        isoformat_row,
    )
    from core.social.drafter import draft_from_brief, proofread_post

    variant = get_variant(variant_id)
    if not variant:
        raise HTTPException(404, f"Variant {variant_id} not found")
    draft = get_draft(variant['draft_id'])
    if not draft:
        raise HTTPException(404, f"Parent draft {variant['draft_id']} not found")

    recent = recent_published_for_few_shot(limit=5)
    try:
        if draft['source_mode'] == 'brief':
            parsed, usage = draft_from_brief(
                brief=draft['brief_text'] or '',
                platforms=[variant['platform']],
                content_pillar=draft.get('content_pillar'),
                recent_published=recent,
            )
        else:
            parsed, usage = proofread_post(
                original_text=draft['original_text'] or '',
                platforms=[variant['platform']],
                content_pillar=draft.get('content_pillar'),
                recent_published=recent,
            )
    except Exception as exc:
        raise HTTPException(500, f"regeneration failed: {exc}")

    new_text = (parsed.get('drafts') or {}).get(variant['platform'], '').strip()
    if not new_text:
        raise HTTPException(500, "regeneration returned empty content")

    new_id = insert_variant(
        draft_id=draft['id'],
        platform=variant['platform'],
        content=new_text,
        generation_model=usage['model'],
        parent_variant_id=variant['id'],
        revision_count=int(variant.get('revision_count') or 0) + 1,
    )
    new_variant = get_variant(new_id)

    log_social_cost(
        session_id=_new_session_id(),
        prompt_summary=f"social regenerate v{variant_id}",
        model=usage['model'],
        tokens_in=usage['input_tokens'],
        tokens_out=usage['output_tokens'],
        cost_gbp=usage['cost_gbp'],
    )

    return {
        'variant': isoformat_row(new_variant) if new_variant else None,
        'usage': usage,
    }


# ── Publishing + memory write-back ───────────────────────────────────────────

@router.post('/variants/{variant_id}/publish')
async def publish_variant(
    variant_id: int,
    body: PublishRequest,
    _: bool = Depends(verify_api_key),
):
    """Mark a variant as published and write it to Cairn memory.

    Per CAIRN_SOCIAL_V2_HANDOFF.md Blocker 2 + implementation note 6:
    this is the most important flow in the module. We do a dual write:

      1. claw_code_chunks (chunk_type='social_post') with embedding —
         makes the post discoverable via Cairn's Ask interface
      2. core.memory.store decision row — equivalent to /memory/write,
         makes the post visible in chat history retrieval

    Both are best-effort. The publish itself succeeds even if memory
    write-back fails (the variant is still marked is_published=true).
    """
    from datetime import datetime as _dt

    from core.social.db import (
        get_variant,
        get_draft,
        mark_variant_published,
        isoformat_row,
    )
    from core.social.memory import (
        write_published_post_to_chunks,
        write_published_post_to_decisions,
    )

    variant = get_variant(variant_id)
    if not variant:
        raise HTTPException(404, f"Variant {variant_id} not found")
    draft = get_draft(variant['draft_id'])
    if not draft:
        raise HTTPException(404, f"Parent draft {variant['draft_id']} not found")

    published_at = _dt.utcnow()
    brief_or_original = draft.get('brief_text') or draft.get('original_text')

    chunk_path = write_published_post_to_chunks(
        variant_id=variant_id,
        platform=variant['platform'],
        pillar=draft.get('content_pillar'),
        post_text=variant['content'],
        published_at=published_at,
        published_url=body.published_url,
        source_mode=draft.get('source_mode') or 'brief',
        brief_or_original=brief_or_original,
    )
    decision_session = write_published_post_to_decisions(
        variant_id=variant_id,
        platform=variant['platform'],
        pillar=draft.get('content_pillar'),
        post_text=variant['content'],
        published_url=body.published_url,
        source_mode=draft.get('source_mode') or 'brief',
    )

    cairn_memory_id = chunk_path or decision_session

    updated = mark_variant_published(
        variant_id=variant_id,
        published_url=body.published_url,
        cairn_memory_id=cairn_memory_id,
    )

    return {
        'variant': isoformat_row(updated) if updated else None,
        'memory': {
            'chunk_path': chunk_path,
            'decision_session': decision_session,
        },
    }


@router.get('/published')
async def list_published_posts(
    platform: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    _: bool = Depends(verify_api_key),
):
    from core.social.db import list_published, isoformat_row
    rows = list_published(platform=platform, limit=limit, offset=offset)
    return {'published': [isoformat_row(r) for r in rows]}
