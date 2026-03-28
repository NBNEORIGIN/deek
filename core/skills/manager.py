"""
Skill Manager — orchestrates skill classification, context building,
and Opus escalation.

Phase 2 rewrite: integrates SkillLoader + SkillClassifier for
auto-classification while preserving backward-compatible sync API
used by agent.py.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .skill_classifier import SkillClassifier
from .skill_loader import SkillDefinition, SkillLoader

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_BUDGET = 700


class SkillManager:
    MAX_ACTIVE_SKILLS = 2
    CONTEXT_BUDGET_TOKENS = DEFAULT_CONTEXT_BUDGET

    def __init__(
        self,
        skill_loader: Optional[SkillLoader] = None,
        skill_classifier: Optional[SkillClassifier] = None,
        *,
        project_id: Optional[str] = None,
        projects_root: Optional[Path] = None,
    ):
        """
        Two construction modes:

        New (Layer 3):
            SkillManager(skill_loader=loader, skill_classifier=classifier)

        Legacy (backward compat):
            SkillManager(project_id='claw')
            Uses old loader.py internally. No classifier.
        """
        if skill_loader is not None:
            self.skill_loader = skill_loader
            self.skill_classifier = skill_classifier
            self._legacy = False
            self._project_id = project_id
        else:
            # Legacy path — instantiate old loader
            from .loader import SkillLoader as LegacyLoader
            if projects_root is None:
                projects_root = Path(__file__).resolve().parents[2] / 'projects'
            self._legacy_loader = LegacyLoader(
                project_id=project_id or '',
                projects_root=projects_root,
            )
            self.skill_loader = None
            self.skill_classifier = None
            self._legacy = True
            self._project_id = project_id

    # ------------------------------------------------------------------
    # Async API (Layer 3) — used after agent.py is rewired
    # ------------------------------------------------------------------

    async def get_active_skills(
        self,
        query: str,
        project_id: str,
        manual_skill_ids: Optional[list[str]] = None,
    ) -> list[str]:
        """
        Returns list of active skill_ids.

        Priority: manual_skill_ids if provided, else classifier.
        """
        if manual_skill_ids:
            return manual_skill_ids[:self.MAX_ACTIVE_SKILLS]

        if self.skill_classifier is None:
            return []

        return await self.skill_classifier.classify(query, project_id)

    def build_skill_context(
        self,
        skill_ids: list[str],
        budget_tokens: int = DEFAULT_CONTEXT_BUDGET,
    ) -> str:
        """
        Build context string for given skill_ids.
        Splits budget evenly across skills.
        Includes: display_name, description, key_rules, recent decisions.
        """
        if not skill_ids or self.skill_loader is None:
            return ''

        skills = [
            self.skill_loader.get_skill(sid)
            for sid in skill_ids
        ]
        skills = [s for s in skills if s is not None]
        if not skills:
            return ''

        per_skill_budget = max(120, int(budget_tokens / len(skills)))
        blocks: list[str] = []

        for skill in skills:
            lines = [
                f"## Skill: {skill.display_name}",
                f"Domain: {skill.description}",
            ]
            if skill.key_rules:
                lines.append("Key rules:")
                lines.extend(f"- {rule}" for rule in skill.key_rules)
            decisions = self._get_recent_decisions(skill)
            if decisions:
                lines.append("Recent decisions:")
                lines.extend(f"- {d}" for d in decisions)

            block = '\n'.join(lines).strip()
            words = block.split()
            max_words = max(1, int(per_skill_budget / 1.3))
            if len(words) > max_words:
                block = ' '.join(words[:max_words]) + ' …'
            blocks.append(block)

        return '\n\n'.join(blocks)

    def get_skill_subproject_id(self, skill_ids: list[str]) -> Optional[str]:
        """
        Returns subproject_id from the first skill that has one.
        """
        if not skill_ids or self.skill_loader is None:
            return None
        for sid in skill_ids:
            skill = self.skill_loader.get_skill(sid)
            if skill and skill.subproject_id:
                return skill.subproject_id
        return None

    def should_escalate_to_opus(
        self,
        query: str,
        skill_ids: list[str],
    ) -> bool:
        """
        Check if query + active skills warrant Opus escalation.
        Returns True if any escalation keyword from active skills
        appears in the query.
        """
        if not skill_ids or self.skill_loader is None:
            return False

        query_lower = query.lower()
        for sid in skill_ids:
            skill = self.skill_loader.get_skill(sid)
            if not skill:
                continue
            for keyword in skill.escalate_to_opus_keywords:
                if keyword.lower() in query_lower:
                    logger.info(
                        '[Skills] Opus escalation triggered: %r in skill %s',
                        keyword, sid,
                    )
                    return True
        return False

    def get_skills(self, skill_ids: list[str]) -> list[SkillDefinition]:
        """Return SkillDefinition objects for given ids."""
        if not skill_ids or self.skill_loader is None:
            return []
        return [
            s for s in (self.skill_loader.get_skill(sid) for sid in skill_ids)
            if s is not None
        ]

    # ------------------------------------------------------------------
    # Legacy sync API — backward compat for current agent.py
    # ------------------------------------------------------------------

    def all_skills(self) -> list:
        if self._legacy:
            return self._legacy_loader.load_all()
        if self.skill_loader is None:
            return []
        pid = self._project_id
        if pid:
            return self.skill_loader.get_skills_for_project(pid)
        return self.skill_loader.all_skills()

    def list_skills(self) -> list:
        return self.all_skills()

    def match(
        self,
        query: str,
        subproject_id: Optional[str] = None,
    ) -> list:
        query_lower = query.lower()
        matched = []
        for skill in self.all_skills():
            if subproject_id and skill.subproject_id == subproject_id:
                matched.append(skill)
                continue
            for trigger in skill.triggers:
                if trigger.lower() in query_lower:
                    matched.append(skill)
                    break
        seen: set[str] = set()
        unique = []
        for skill in matched:
            if skill.skill_id in seen:
                continue
            seen.add(skill.skill_id)
            unique.append(skill)
            if len(unique) >= self.MAX_ACTIVE_SKILLS:
                break
        return unique

    def resolve(self, skill_ids: Optional[list[str]]) -> list:
        requested = set(skill_ids or [])
        if not requested:
            return []
        skills = [s for s in self.all_skills() if s.skill_id in requested]
        return skills[:self.MAX_ACTIVE_SKILLS]

    def resolve_for_request(
        self,
        query: str,
        subproject_id: Optional[str] = None,
        manual_skill_ids: Optional[list[str]] = None,
    ) -> list:
        return self.resolve(manual_skill_ids)

    def build_context(self, skills: list) -> str:
        if not skills:
            return ''
        per_skill_budget = max(120, int(self.CONTEXT_BUDGET_TOKENS / len(skills)))
        blocks: list[str] = []
        for skill in skills:
            lines = [
                f"## Skill: {skill.display_name}",
                f"Domain: {skill.description}",
            ]
            hint = getattr(skill, 'prompt_hint', None)
            if hint:
                lines.append(f"Hint: {hint}")
            if skill.key_rules:
                lines.append("Key rules:")
                lines.extend(f"- {rule}" for rule in skill.key_rules)
            decisions = self._recent_decisions_legacy(skill)
            if decisions:
                lines.append("Recent decisions:")
                lines.extend(f"- {d}" for d in decisions)

            block = '\n'.join(lines).strip()
            words = block.split()
            max_words = max(1, int(per_skill_budget / 1.3))
            if len(words) > max_words:
                block = ' '.join(words[:max_words]) + ' …'
            blocks.append(block)
        return '\n\n'.join(blocks)

    def build_context_blocks(self, skills: list) -> list[str]:
        if not skills:
            return []
        block = self.build_context(skills)
        return [part for part in block.split('\n\n') if part.strip()]

    def active_skill_ids(self, skills: list) -> list[str]:
        return [skill.skill_id for skill in skills]

    def derived_subproject_id(self, skills: list) -> Optional[str]:
        for skill in skills:
            if skill.subproject_id:
                return skill.subproject_id
        return None

    def primary_subproject_id(self, skills: list) -> Optional[str]:
        return self.derived_subproject_id(skills)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_recent_decisions(
        self,
        skill: SkillDefinition,
        max_bullets: int = 5,
    ) -> list[str]:
        if not skill.decisions_path.exists():
            return []
        bullets = []
        for line in skill.decisions_path.read_text(encoding='utf-8').splitlines():
            stripped = line.strip()
            if stripped.startswith('- '):
                bullets.append(stripped[2:].strip())
        return bullets[-max_bullets:]

    def _recent_decisions_legacy(self, skill, max_bullets: int = 3) -> list[str]:
        dp = getattr(skill, 'decisions_path', None)
        if dp is None or not dp.exists():
            return []
        bullets = []
        for line in dp.read_text(encoding='utf-8').splitlines():
            stripped = line.strip()
            if stripped.startswith('- '):
                bullets.append(stripped[2:].strip())
        return bullets[-max_bullets:]
