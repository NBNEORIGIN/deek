"""
Classifies a query against loaded skills.

Two-path classification:
  Fast path: exact string match against trigger phrases.
             No embedding call. Under 1ms.

  Slow path: cosine similarity of query embedding against
             pre-embedded trigger phrases.
             Uses existing nomic-embed-text via Ollama.
             Under 10ms after warmup.

Returns up to MAX_ACTIVE_SKILLS matching skill_ids.
Returns empty list if nothing matches threshold.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from .skill_loader import SkillLoader

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.72
MAX_ACTIVE_SKILLS = 2


class SkillClassifier:

    def __init__(self, skill_loader: SkillLoader, embedder):
        self.skill_loader = skill_loader
        self.embedder = embedder
        self._trigger_embeddings: dict[str, np.ndarray] = {}
        self._skills_loaded = False

    async def initialise(self) -> None:
        """
        Pre-embed all trigger phrases at startup.
        Called once from API lifespan handler.

        If embedder unavailable: log warning and continue.
        Classifier falls back to exact-match only.
        """
        skills = self.skill_loader.all_skills()
        if not skills:
            logger.warning('[Skills] No skills loaded — classifier inactive')
            self._skills_loaded = True
            return

        embedded_count = 0
        for skill in skills:
            if not skill.triggers:
                continue
            embeddings: list[np.ndarray] = []
            for trigger in skill.triggers:
                try:
                    emb = await self.embedder.embed(trigger)
                    if emb is not None:
                        embeddings.append(np.array(emb, dtype=np.float32))
                except Exception as exc:
                    logger.debug('[Skills] Embed failed for %r: %s', trigger, exc)

            if embeddings:
                self._trigger_embeddings[skill.skill_id] = np.stack(embeddings)
                embedded_count += 1

        self._skills_loaded = True
        logger.info(
            '[Skills] Classifier ready — %d/%d skills embedded',
            embedded_count, len(skills),
        )

    async def classify(
        self,
        query: str,
        project_id: str,
    ) -> list[str]:
        """
        Returns list of matching skill_ids.
        Always tries fast path first.
        Falls back to embedding similarity if no exact match.
        """
        if not self._skills_loaded:
            return []

        project_skills = self.skill_loader.get_skills_for_project(project_id)
        if not project_skills:
            return []

        # Fast path — exact substring match
        query_lower = query.lower()
        for skill in project_skills:
            for trigger in skill.triggers:
                if trigger.lower() in query_lower:
                    logger.info(
                        '[Skills] Exact match: %r -> %s', trigger, skill.skill_id,
                    )
                    return [skill.skill_id]

        # Slow path — embedding similarity
        if not self._trigger_embeddings:
            return []

        try:
            raw_emb = await self.embedder.embed(query)
            if raw_emb is None:
                return []
            query_emb = np.array(raw_emb, dtype=np.float32)
        except Exception as exc:
            logger.debug('[Skills] Query embed failed: %s', exc)
            return []

        matches: list[tuple[str, float]] = []
        for skill in project_skills:
            sid = skill.skill_id
            if sid not in self._trigger_embeddings:
                continue

            trigger_embs = self._trigger_embeddings[sid]
            q_norm = query_emb / (np.linalg.norm(query_emb) + 1e-8)
            t_norms = trigger_embs / (
                np.linalg.norm(trigger_embs, axis=1, keepdims=True) + 1e-8
            )
            sims = t_norms @ q_norm
            max_sim = float(np.max(sims))

            if max_sim >= SIMILARITY_THRESHOLD:
                matches.append((sid, max_sim))
                logger.info(
                    '[Skills] Similarity match: %s score=%.3f', sid, max_sim,
                )

        matches.sort(key=lambda x: x[1], reverse=True)
        return [sid for sid, _ in matches[:MAX_ACTIVE_SKILLS]]
