"""
Cairn Social — drafting + proof-reading assistant for Jo at NBNE.

Phase 1 scope (per CAIRN_SOCIAL_V2_HANDOFF.md + Toby 2026-04-10):
  - Centre + right panels only (no left "Today's context" panel)
  - Three platforms only: Facebook, Instagram, LinkedIn (no TikTok)
  - Two input modes:
      brief    — Jo gives a short prompt, the tool drafts in her voice
      proofread — Jo writes her own post, the tool proof-reads/refines per platform
  - Single user (Jo hard-coded; Cairn is API-key-only at present)
  - Drafting via claude-sonnet-4-6
  - Cost logging via /costs/log
  - Memory write-back on publish via /memory/write + claw_code_chunks (chunk_type='social_post')
  - No direct platform publishing — copy-to-clipboard only
"""
