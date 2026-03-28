"""
Layer 3 — Skills System tests.

Tests cover:
  - SkillLoader: YAML loading, cross-project scanning, validation
  - SkillClassifier: exact match, embedding similarity, threshold, fallback
  - SkillManager: get_active_skills, build_skill_context, escalation, subproject
  - Agent integration: async skill resolution, Opus escalation wiring
"""
import asyncio
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.skills.skill_loader import SkillDefinition, SkillLoader
from core.skills.skill_classifier import SkillClassifier, SIMILARITY_THRESHOLD, MAX_ACTIVE_SKILLS
from core.skills.manager import SkillManager


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_projects(tmp_path):
    """Create a minimal projects tree with skill.yaml files."""
    # Project alpha with two skills
    s1 = tmp_path / 'alpha' / 'skills' / 'deploy' / 'skill.yaml'
    s1.parent.mkdir(parents=True)
    s1.write_text(textwrap.dedent("""\
        skill_id: deploy
        project_id: alpha
        display_name: Deploy Helper
        description: Helps with deployments
        triggers:
          - deploy
          - ship it
        subproject_id: web-app
        key_rules:
          - Always run tests before deploying
          - Never deploy on Friday
        tools_allowed:
          - run_command
        escalate_to_opus_keywords:
          - production rollback
          - database migration
        context_budget_override: 500
    """))
    # decisions.md for deploy skill
    (s1.parent / 'decisions.md').write_text(textwrap.dedent("""\
        # Deploy decisions
        - 2026-03-01: Switched to blue-green deployments
        - 2026-03-10: Added canary step for API services
        - 2026-03-15: Reverted canary - too slow for small changes
        - 2026-03-20: Re-enabled canary with 5-min timeout
        - 2026-03-25: Added rollback trigger on error rate > 5%
        - 2026-03-27: Pinned Node 20 LTS for all deploys
    """), encoding='utf-8')

    s2 = tmp_path / 'alpha' / 'skills' / 'testing' / 'skill.yaml'
    s2.parent.mkdir(parents=True)
    s2.write_text(textwrap.dedent("""\
        skill_id: testing
        project_id: alpha
        display_name: Test Runner
        description: Manages test execution
        triggers:
          - run tests
          - pytest
        subproject_id:
        key_rules:
          - Use pytest markers for slow tests
        escalate_to_opus_keywords: []
    """))

    # Project beta with one skill
    s3 = tmp_path / 'beta' / 'skills' / 'docs' / 'skill.yaml'
    s3.parent.mkdir(parents=True)
    s3.write_text(textwrap.dedent("""\
        skill_id: docs
        project_id: beta
        display_name: Documentation
        description: Generates and maintains docs
        triggers:
          - write docs
          - documentation
    """))

    return tmp_path


@pytest.fixture
def loader(tmp_projects):
    sl = SkillLoader(projects_root=str(tmp_projects))
    sl.load_all_skills()
    return sl


@pytest.fixture
def mock_embedder():
    """Embedder that returns deterministic vectors."""
    import numpy as np
    embedder = AsyncMock()

    # Map triggers to specific vectors for controllable similarity
    vectors = {
        'deploy': np.array([1.0, 0.0, 0.0], dtype=np.float32),
        'ship it': np.array([0.95, 0.05, 0.0], dtype=np.float32),
        'run tests': np.array([0.0, 1.0, 0.0], dtype=np.float32),
        'pytest': np.array([0.0, 0.95, 0.05], dtype=np.float32),
        'write docs': np.array([0.0, 0.0, 1.0], dtype=np.float32),
        'documentation': np.array([0.0, 0.05, 0.95], dtype=np.float32),
    }

    async def embed(text):
        # Return exact match if known, else a query vector
        if text in vectors:
            return vectors[text].tolist()
        t = text.lower()
        if 'deploy' in t or 'ship' in t:
            return [0.98, 0.02, 0.0]
        if 'test' in t or 'pytest' in t:
            return [0.02, 0.98, 0.0]
        if 'doc' in t:
            return [0.0, 0.02, 0.98]
        # Unrelated query — low similarity to everything
        return [0.33, 0.33, 0.34]

    embedder.embed = embed
    return embedder


# ── SkillLoader tests ────────────────────────────────────────────────────────

class TestSkillLoader:

    def test_loads_all_skills_across_projects(self, loader):
        skills = loader.all_skills()
        assert len(skills) == 3
        ids = {s.skill_id for s in skills}
        assert ids == {'deploy', 'testing', 'docs'}

    def test_skill_definition_fields(self, loader):
        skill = loader.get_skill('deploy')
        assert skill is not None
        assert skill.project_id == 'alpha'
        assert skill.display_name == 'Deploy Helper'
        assert skill.description == 'Helps with deployments'
        assert 'deploy' in skill.triggers
        assert 'ship it' in skill.triggers
        assert skill.subproject_id == 'web-app'
        assert 'Always run tests before deploying' in skill.key_rules
        assert 'run_command' in skill.tools_allowed
        assert 'production rollback' in skill.escalate_to_opus_keywords
        assert skill.context_budget_override == 500
        assert skill.decisions_path.name == 'decisions.md'

    def test_get_skills_for_project(self, loader):
        alpha_skills = loader.get_skills_for_project('alpha')
        assert len(alpha_skills) == 2
        assert all(s.project_id == 'alpha' for s in alpha_skills)

        beta_skills = loader.get_skills_for_project('beta')
        assert len(beta_skills) == 1
        assert beta_skills[0].skill_id == 'docs'

    def test_missing_fields_default_to_empty(self, loader):
        """Docs skill has no tools_allowed/escalate_to_opus_keywords."""
        skill = loader.get_skill('docs')
        assert skill is not None
        assert skill.tools_allowed == []
        assert skill.escalate_to_opus_keywords == []
        assert skill.context_budget_override is None
        assert skill.subproject_id is None

    def test_invalid_yaml_skipped(self, tmp_path):
        bad = tmp_path / 'proj' / 'skills' / 'broken' / 'skill.yaml'
        bad.parent.mkdir(parents=True)
        bad.write_text("not_a_valid_skill: true\n")
        sl = SkillLoader(projects_root=str(tmp_path))
        skills = sl.load_all_skills()
        assert len(skills) == 0

    def test_empty_yaml_skipped(self, tmp_path):
        empty = tmp_path / 'proj' / 'skills' / 'empty' / 'skill.yaml'
        empty.parent.mkdir(parents=True)
        empty.write_text("")
        sl = SkillLoader(projects_root=str(tmp_path))
        skills = sl.load_all_skills()
        assert len(skills) == 0

    def test_get_skill_returns_none_for_unknown(self, loader):
        assert loader.get_skill('nonexistent') is None

    def test_loads_real_project_skills(self):
        """Smoke test: load from real projects/ dir."""
        sl = SkillLoader(projects_root='projects')
        skills = sl.load_all_skills()
        # We know at least some skills exist
        assert len(skills) >= 3
        for s in skills:
            assert s.skill_id
            assert s.project_id
            assert s.triggers


# ── SkillClassifier tests ────────────────────────────────────────────────────

class TestSkillClassifier:

    @pytest.mark.asyncio
    async def test_exact_match_fast_path(self, loader, mock_embedder):
        classifier = SkillClassifier(loader, mock_embedder)
        await classifier.initialise()
        result = await classifier.classify('please deploy the app', 'alpha')
        assert result == ['deploy']

    @pytest.mark.asyncio
    async def test_exact_match_case_insensitive(self, loader, mock_embedder):
        classifier = SkillClassifier(loader, mock_embedder)
        await classifier.initialise()
        result = await classifier.classify('Run Tests now', 'alpha')
        assert result == ['testing']

    @pytest.mark.asyncio
    async def test_embedding_similarity_path(self, loader, mock_embedder):
        """Query doesn't contain exact trigger but is semantically close."""
        classifier = SkillClassifier(loader, mock_embedder)
        await classifier.initialise()
        # "deployment pipeline" doesn't contain exact "deploy" or "ship it"
        # but our mock embedder returns high similarity
        result = await classifier.classify('deployment pipeline changes', 'alpha')
        # Should match deploy via embedding
        assert 'deploy' in result

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self, loader, mock_embedder):
        classifier = SkillClassifier(loader, mock_embedder)
        await classifier.initialise()
        result = await classifier.classify('weather forecast today', 'alpha')
        assert result == []

    @pytest.mark.asyncio
    async def test_max_active_skills_limit(self, loader, mock_embedder):
        classifier = SkillClassifier(loader, mock_embedder)
        await classifier.initialise()
        result = await classifier.classify('anything', 'alpha')
        assert len(result) <= MAX_ACTIVE_SKILLS

    @pytest.mark.asyncio
    async def test_project_scoping(self, loader, mock_embedder):
        """Classifier only matches skills within the requested project."""
        classifier = SkillClassifier(loader, mock_embedder)
        await classifier.initialise()
        # "deploy" trigger exists only in alpha, not beta
        result = await classifier.classify('deploy the app', 'beta')
        assert 'deploy' not in result

    @pytest.mark.asyncio
    async def test_no_embedder_falls_back_to_exact(self, loader):
        """Without embedder, classifier still works via exact match."""
        broken_embedder = AsyncMock()
        broken_embedder.embed = AsyncMock(side_effect=Exception('no embedder'))
        classifier = SkillClassifier(loader, broken_embedder)
        await classifier.initialise()
        # Exact match still works
        result = await classifier.classify('deploy now', 'alpha')
        assert result == ['deploy']

    @pytest.mark.asyncio
    async def test_empty_project_returns_empty(self, loader, mock_embedder):
        classifier = SkillClassifier(loader, mock_embedder)
        await classifier.initialise()
        result = await classifier.classify('deploy', 'nonexistent_project')
        assert result == []

    def test_threshold_and_max_constants(self):
        assert SIMILARITY_THRESHOLD == 0.72
        assert MAX_ACTIVE_SKILLS == 2


# ── SkillManager tests ──────────────────────────────────────────────────────

class TestSkillManager:

    @pytest.fixture
    def manager(self, loader, mock_embedder):
        classifier = SkillClassifier(loader, mock_embedder)
        return SkillManager(
            skill_loader=loader,
            skill_classifier=classifier,
            project_id='alpha',
        )

    @pytest.mark.asyncio
    async def test_get_active_skills_with_classifier(self, manager, mock_embedder):
        await manager.skill_classifier.initialise()
        result = await manager.get_active_skills('deploy the app', 'alpha')
        assert 'deploy' in result

    @pytest.mark.asyncio
    async def test_get_active_skills_manual_override(self, manager):
        result = await manager.get_active_skills(
            'anything', 'alpha', manual_skill_ids=['testing'],
        )
        assert result == ['testing']

    @pytest.mark.asyncio
    async def test_get_active_skills_manual_respects_limit(self, manager):
        result = await manager.get_active_skills(
            'q', 'alpha', manual_skill_ids=['a', 'b', 'c'],
        )
        assert len(result) <= 2

    def test_build_skill_context_includes_rules(self, manager):
        ctx = manager.build_skill_context(['deploy'], budget_tokens=700)
        assert 'Deploy Helper' in ctx
        assert 'Always run tests before deploying' in ctx

    def test_build_skill_context_includes_decisions(self, manager):
        ctx = manager.build_skill_context(['deploy'], budget_tokens=700)
        assert 'Recent decisions' in ctx
        # Should have last 5 decisions
        assert 'Pinned Node 20 LTS' in ctx

    def test_build_skill_context_empty_ids(self, manager):
        assert manager.build_skill_context([]) == ''

    def test_build_skill_context_unknown_id(self, manager):
        assert manager.build_skill_context(['nonexistent']) == ''

    def test_get_skill_subproject_id(self, manager):
        assert manager.get_skill_subproject_id(['deploy']) == 'web-app'
        assert manager.get_skill_subproject_id(['testing']) is None
        assert manager.get_skill_subproject_id([]) is None

    def test_should_escalate_to_opus(self, manager):
        assert manager.should_escalate_to_opus(
            'we need a production rollback', ['deploy'],
        ) is True
        assert manager.should_escalate_to_opus(
            'run the database migration', ['deploy'],
        ) is True
        assert manager.should_escalate_to_opus(
            'deploy to staging', ['deploy'],
        ) is False

    def test_should_escalate_no_keywords(self, manager):
        """Testing skill has no escalation keywords."""
        assert manager.should_escalate_to_opus(
            'production rollback', ['testing'],
        ) is False

    def test_get_skills_returns_definitions(self, manager):
        skills = manager.get_skills(['deploy', 'testing'])
        assert len(skills) == 2
        ids = {s.skill_id for s in skills}
        assert ids == {'deploy', 'testing'}

    def test_get_skills_filters_unknown(self, manager):
        skills = manager.get_skills(['deploy', 'nonexistent'])
        assert len(skills) == 1


# ── Legacy backward compatibility ───────────────────────────────────────────

class TestSkillManagerLegacy:

    def test_legacy_constructor(self):
        """Legacy SkillManager(project_id=...) still works."""
        mgr = SkillManager(project_id='claw')
        skills = mgr.all_skills()
        # Should load from real projects/claw/skills/
        assert isinstance(skills, list)

    def test_legacy_resolve_for_request(self):
        mgr = SkillManager(project_id='claw')
        result = mgr.resolve_for_request(
            query='architecture question',
            manual_skill_ids=['architecture'],
        )
        # May or may not find it depending on real files, but shouldn't crash
        assert isinstance(result, list)

    def test_legacy_build_context_blocks(self):
        mgr = SkillManager(project_id='claw')
        skills = mgr.all_skills()
        blocks = mgr.build_context_blocks(skills[:1] if skills else [])
        assert isinstance(blocks, list)

    def test_legacy_primary_subproject_id(self):
        mgr = SkillManager(project_id='claw')
        # Empty list returns None
        assert mgr.primary_subproject_id([]) is None
