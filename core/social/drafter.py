"""
Cairn Social drafter — assembles prompts and calls Claude.

Two entry points:
  draft_from_brief(brief, platforms, ...)   — Jo's short prompt → drafts in her voice
  proofread_post(original_text, platforms)  — Jo's finished post → polished per platform

A third helper:
  refine_variant(variant, instruction, ...) — chat-style refinement of one variant

All Claude calls go through claude-sonnet-4-6 (per CAIRN_SOCIAL_V2_HANDOFF.md
correction B). Cost is logged via /costs/log by the route layer — this module
just returns usage data alongside the response.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import anthropic

from .voice_guide import (
    CONTENT_PILLARS,
    PLATFORM_ADAPTATIONS,
    PLATFORMS,
    SEED_POSTS,
    SEED_VERSION,
    VOICE_GUIDE,
)

# Per CAIRN_SOCIAL_V2_HANDOFF.md correction B:
DEFAULT_MODEL = os.getenv('CAIRN_SOCIAL_MODEL', 'claude-sonnet-4-6')

# Approximate sonnet rates in GBP per 1M tokens (per CLAUDE.md cost table)
PRICE_INPUT_GBP_PER_M = 0.24
PRICE_OUTPUT_GBP_PER_M = 1.20


def _client() -> anthropic.Anthropic:
    api_key = os.getenv('ANTHROPIC_API_KEY', '')
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — Cairn Social needs it to draft posts"
        )
    return anthropic.Anthropic(api_key=api_key)


def estimate_gbp(input_tokens: int, output_tokens: int) -> float:
    return round(
        (input_tokens / 1_000_000) * PRICE_INPUT_GBP_PER_M
        + (output_tokens / 1_000_000) * PRICE_OUTPUT_GBP_PER_M,
        6,
    )


# ── System prompt assembly ────────────────────────────────────────────────────

def _few_shot_examples(recent_published: list[dict]) -> str:
    """Combine the seed posts (always included) with up to a few recently
    published variants. Seed posts are the permanent voice anchors per the
    brief — they stay even when newer posts exist.
    """
    blocks: list[str] = []
    blocks.append("### Seed examples (Jo's actual posts — these are the voice anchor)\n")
    for s in SEED_POSTS:
        blocks.append(
            f"**Seed — {s['title']} (pillar: {s['pillar']}, "
            f"platform: {s['platform']})**\n\n{s['content']}\n"
        )
    if recent_published:
        blocks.append("\n### Recent published posts\n")
        for r in recent_published:
            blocks.append(
                f"**Published on {r['platform']} ({(r.get('published_at') or '')})**\n\n"
                f"{r['content']}\n"
            )
    return '\n'.join(blocks)


def _platform_section(platforms: list[str]) -> str:
    return '\n'.join(PLATFORM_ADAPTATIONS[p] for p in platforms if p in PLATFORM_ADAPTATIONS)


def build_system_prompt(
    *,
    mode: str,
    platforms: list[str],
    recent_published: Optional[list[dict]] = None,
) -> str:
    """Assemble the system prompt for either drafting or proofreading.

    The voice guide and seed posts are identical across both modes — only the
    final instruction differs (drafted from a brief vs polishing Jo's own
    finished text).
    """
    recent_published = recent_published or []

    if mode == 'brief':
        task_intro = (
            "You are Cairn Social, a drafting tool for NBNE (a sign-making "
            "business in Alnwick, Northumberland, run by Jo Fletcher and her "
            "team). Jo will give you a short brief about something she wants "
            "to post. Your job is to draft posts in Jo's voice for each "
            "requested platform, following the voice guide below precisely. "
            "If the brief lacks concrete details, prefer to ask for them by "
            "saying so in the draft itself rather than inventing facts."
        )
    else:  # proofread
        task_intro = (
            "You are Cairn Social, acting as a proof-reader and editor for "
            "Jo Fletcher at NBNE (a sign-making business in Alnwick, "
            "Northumberland). Jo will give you a finished post she has "
            "already written. Your job is to produce a polished version of "
            "her post for each requested platform — fixing typos, smoothing "
            "awkward phrasing, applying the platform-specific length and "
            "hashtag conventions below, and gently aligning with the voice "
            "guide where the original drifts from it. **Preserve Jo's voice "
            "and intent. Do not rewrite. Do not invent new facts or details. "
            "If the original is already strong, return it nearly unchanged.**"
        )

    output_instruction = (
        "Return a JSON object with this exact shape and nothing else "
        "(no markdown fences, no commentary):\n\n"
        "{\n"
        '  "detected_pillar": "job|what_we_do|team|development",\n'
        '  "drafts": {\n'
        + ',\n'.join(f'    "{p}": "..."' for p in platforms) + '\n'
        "  },\n"
        '  "notes_for_jo": "Optional — anything you want Jo to know, e.g. '
        "missing details, suggestions to add a photo, anything you noticed."
        '"\n'
        "}"
    )

    return (
        f"{task_intro}\n\n"
        f"## Voice guide\n\n{VOICE_GUIDE}\n\n"
        f"## Platform adaptations\n\n{_platform_section(platforms)}\n\n"
        f"## Few-shot examples\n\n{_few_shot_examples(recent_published)}\n\n"
        f"## Output format\n\n{output_instruction}"
    )


# ── Claude calls ──────────────────────────────────────────────────────────────

def _parse_json_response(text: str) -> dict[str, Any]:
    """Tolerantly parse the model's JSON response. Handles accidental code
    fences and stray prose around the JSON object.
    """
    cleaned = text.strip()
    # Strip markdown code fences if the model wrapped JSON in them anyway
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)
    # Find first { and last } in case there's stray prose
    first = cleaned.find('{')
    last = cleaned.rfind('}')
    if first != -1 and last != -1:
        cleaned = cleaned[first:last + 1]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Cairn Social: model did not return valid JSON. Raw response: {text[:500]}"
        ) from exc


def _call_claude(
    *,
    system_prompt: str,
    user_message: str,
    model: str = DEFAULT_MODEL,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Single Claude call. Returns (parsed_json, usage_dict)."""
    client = _client()
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system_prompt,
        messages=[{'role': 'user', 'content': user_message}],
    )
    text = ''
    for block in response.content:
        if getattr(block, 'type', None) == 'text':
            text += block.text

    parsed = _parse_json_response(text)

    usage = {
        'model': model,
        'input_tokens': response.usage.input_tokens,
        'output_tokens': response.usage.output_tokens,
        'cost_gbp': estimate_gbp(
            response.usage.input_tokens,
            response.usage.output_tokens,
        ),
    }
    return parsed, usage


def _validate_platforms(platforms: list[str]) -> list[str]:
    cleaned = [p.lower().strip() for p in platforms if p]
    valid = [p for p in cleaned if p in PLATFORMS]
    if not valid:
        raise ValueError(
            f"No valid platforms provided. Got {platforms!r}; "
            f"expected any of {PLATFORMS}."
        )
    return valid


def draft_from_brief(
    *,
    brief: str,
    platforms: list[str],
    content_pillar: Optional[str] = None,
    recent_published: Optional[list[dict]] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate platform drafts from a short brief written by Jo."""
    if not brief or not brief.strip():
        raise ValueError("Brief is empty")
    platforms = _validate_platforms(platforms)
    system_prompt = build_system_prompt(
        mode='brief',
        platforms=platforms,
        recent_published=recent_published,
    )
    pillar_hint = (
        f"\n\n(Jo selected the content pillar: {content_pillar})"
        if content_pillar in CONTENT_PILLARS else ''
    )
    user_message = (
        f"Brief from Jo:\n\n{brief.strip()}{pillar_hint}\n\n"
        f"Please draft posts for: {', '.join(platforms)}."
    )
    parsed, usage = _call_claude(
        system_prompt=system_prompt,
        user_message=user_message,
    )
    return parsed, usage


def proofread_post(
    *,
    original_text: str,
    platforms: list[str],
    content_pillar: Optional[str] = None,
    recent_published: Optional[list[dict]] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Polish a post Jo has already written, per platform."""
    if not original_text or not original_text.strip():
        raise ValueError("Original text is empty")
    platforms = _validate_platforms(platforms)
    system_prompt = build_system_prompt(
        mode='proofread',
        platforms=platforms,
        recent_published=recent_published,
    )
    pillar_hint = (
        f"\n\n(Jo selected the content pillar: {content_pillar})"
        if content_pillar in CONTENT_PILLARS else ''
    )
    user_message = (
        f"Jo wrote the following post and would like it proof-read and "
        f"adapted for the listed platforms. Preserve her voice. Fix typos, "
        f"smooth awkward phrasing, and apply per-platform length/hashtag "
        f"conventions. Do not rewrite from scratch.\n\n"
        f"---\n{original_text.strip()}\n---{pillar_hint}\n\n"
        f"Platforms requested: {', '.join(platforms)}."
    )
    parsed, usage = _call_claude(
        system_prompt=system_prompt,
        user_message=user_message,
    )
    return parsed, usage


def refine_variant(
    *,
    previous_content: str,
    platform: str,
    instruction: str,
    original_brief: Optional[str] = None,
    recent_published: Optional[list[dict]] = None,
) -> tuple[str, dict[str, Any]]:
    """Apply a chat-style refinement instruction to a single platform draft.

    Returns (refined_text, usage_dict).
    """
    platform = platform.lower().strip()
    if platform not in PLATFORMS:
        raise ValueError(f"Unknown platform {platform!r}")
    if not instruction or not instruction.strip():
        raise ValueError("Refinement instruction is empty")

    system_prompt = (
        "You are Cairn Social, refining a single social media post draft for "
        "Jo at NBNE. Apply Jo's instruction to the previous draft. Return "
        "only the refined post text — no JSON, no commentary, no markdown "
        "fences. Preserve Jo's voice exactly per the voice guide below.\n\n"
        f"## Voice guide\n\n{VOICE_GUIDE}\n\n"
        f"## Platform adaptation\n\n{PLATFORM_ADAPTATIONS[platform]}"
    )

    user_message_parts = []
    if original_brief:
        user_message_parts.append(f"Original brief from Jo: {original_brief}")
    user_message_parts.append(f"Previous {platform} draft:\n\n{previous_content}")
    user_message_parts.append(f"Jo's refinement instruction: {instruction.strip()}")
    user_message = '\n\n'.join(user_message_parts)

    client = _client()
    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=1500,
        system=system_prompt,
        messages=[{'role': 'user', 'content': user_message}],
    )
    text = ''
    for block in response.content:
        if getattr(block, 'type', None) == 'text':
            text += block.text

    refined = text.strip()
    # Defensive: if the model wrapped its answer in code fences, strip them
    if refined.startswith('```'):
        refined = re.sub(r'^```[^\n]*\n', '', refined)
        refined = re.sub(r'\n```\s*$', '', refined)

    usage = {
        'model': DEFAULT_MODEL,
        'input_tokens': response.usage.input_tokens,
        'output_tokens': response.usage.output_tokens,
        'cost_gbp': estimate_gbp(
            response.usage.input_tokens,
            response.usage.output_tokens,
        ),
    }
    return refined, usage
