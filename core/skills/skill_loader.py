"""
Skill loader — scans projects/*/skills/*/skill.yaml and loads SkillDefinitions.

Cross-project: loads all skills from all projects at once.
Caches by skill_id for fast lookup.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SkillDefinition:
    skill_id: str
    project_id: str
    display_name: str
    description: str
    triggers: list[str]
    subproject_id: Optional[str]
    tools_allowed: list[str]
    key_rules: list[str]
    escalate_to_opus_keywords: list[str]
    context_budget_override: Optional[int]
    decisions_path: Path
    skill_dir: Path


class SkillLoader:

    def __init__(self, projects_root: str = 'projects'):
        self.projects_root = Path(projects_root)
        self._cache: dict[str, SkillDefinition] = {}

    def load_all_skills(self) -> list[SkillDefinition]:
        """
        Scan projects/*/skills/*/skill.yaml.
        Load and validate each. Cache by skill_id.
        Return all loaded skills sorted by skill_id.
        Skip invalid files with a warning — never raise.
        """
        skills: list[SkillDefinition] = []
        pattern = '*/skills/*/skill.yaml'
        for skill_yaml in sorted(self.projects_root.glob(pattern)):
            try:
                skill = self._load_skill(skill_yaml)
                self._cache[skill.skill_id] = skill
                skills.append(skill)
                logger.info('[Skills] Loaded: %s', skill.skill_id)
            except Exception as exc:
                logger.warning('[Skills] Failed to load %s: %s', skill_yaml, exc)
        logger.info('[Skills] Total loaded: %d', len(skills))
        return skills

    def get_skill(self, skill_id: str) -> Optional[SkillDefinition]:
        return self._cache.get(skill_id)

    def get_skills_for_project(self, project_id: str) -> list[SkillDefinition]:
        return [
            s for s in self._cache.values()
            if s.project_id == project_id
        ]

    def all_skills(self) -> list[SkillDefinition]:
        return list(self._cache.values())

    def _load_skill(self, path: Path) -> SkillDefinition:
        with open(path, encoding='utf-8') as f:
            data = yaml.safe_load(f)

        if not data or not isinstance(data, dict):
            raise ValueError(f'Empty or invalid YAML in {path}')

        required = ['skill_id', 'project_id', 'display_name', 'description', 'triggers']
        for field_name in required:
            if field_name not in data:
                raise ValueError(f"Missing required field '{field_name}' in {path}")

        subproject_id = data.get('subproject_id')
        if subproject_id is not None:
            subproject_id = str(subproject_id).strip() or None

        return SkillDefinition(
            skill_id=str(data['skill_id']),
            project_id=str(data['project_id']),
            display_name=str(data['display_name']),
            description=str(data['description']),
            triggers=[str(t).strip() for t in data.get('triggers', []) if str(t).strip()],
            subproject_id=subproject_id,
            tools_allowed=[str(t).strip() for t in data.get('tools_allowed', [])],
            key_rules=[str(r).strip() for r in data.get('key_rules', [])],
            escalate_to_opus_keywords=[
                str(k).strip() for k in data.get('escalate_to_opus_keywords', [])
            ],
            context_budget_override=data.get('context_budget_override'),
            decisions_path=path.parent / 'decisions.md',
            skill_dir=path.parent,
        )
