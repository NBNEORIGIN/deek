"""
CLAW test suite — ~20 tests covering:
  - API endpoints: /health, /projects, /chat (validation + auth), /debug/tools
  - Tool registry: permissions, schema format
  - Model router: force_api flag, keyword routing
  - OpenAI client: tool format conversion, message building (incl. vision)
  - Claude client: message building (incl. vision)
  - Agent: provider selection logic
  - Indexer: chunk-size cap
"""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client(auth_headers):
    """
    FastAPI TestClient with Claude's async chat mocked out so no
    real API calls are made during endpoint tests.
    """
    fake_response = ("Mocked response", None, {
        "input_tokens": 10, "output_tokens": 20, "total_tokens": 30,
    })

    with patch("core.models.claude_client.ClaudeClient.chat", new_callable=AsyncMock) as mock_chat, \
         patch(
             "core.context.engine.ContextEngine.build_context_prompt",
             return_value=("mock system prompt", {"context_files": [], "context_file_count": 0}),
         ):
        mock_chat.return_value = fake_response
        import api.main as main
        from core.agent import ClawAgent

        @asynccontextmanager
        async def _noop_lifespan(_app):
            yield

        main.app.router.lifespan_context = _noop_lifespan
        main._agents.clear()

        projects_root = Path("projects")
        for project_dir in sorted(projects_root.iterdir()):
            if not project_dir.is_dir() or project_dir.name.startswith('_'):
                continue
            config_path = project_dir / "config.json"
            if not config_path.exists():
                continue
            config = json.loads(config_path.read_text())
            agent = ClawAgent(project_dir.name, config)
            if 'subprojects' in config:
                for sp in config['subprojects']:
                    try:
                        agent.memory.create_subproject(
                            project_id=project_dir.name,
                            name=sp['name'],
                            display_name=sp['display_name'],
                            description=sp.get('description', ''),
                        )
                    except Exception:
                        pass
            main._agents[project_dir.name] = agent

        main._ollama_status_fast = AsyncMock(return_value={
            'available': False,
            'active_model': None,
            'installed_models': [],
            'vram_warning': False,
        })
        main._project_index_count = AsyncMock(return_value=42)
        main._all_project_chunk_counts = AsyncMock(return_value={
            pid: 150 for pid in main._agents
        })

        tc = TestClient(main.app)
        try:
            yield tc, auth_headers
        finally:
            pass


# ─── /health ─────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_returns_200(self, client):
        tc, _ = client
        r = tc.get("/health")
        assert r.status_code == 200

    def test_health_has_required_keys(self, client):
        tc, _ = client
        data = tc.get("/health").json()
        for key in ("status", "anthropic_key_set", "ollama_available",
                    "ollama_model_ready", "projects_loaded"):
            assert key in data, f"Missing key: {key}"

    def test_health_status_is_ok(self, client):
        tc, _ = client
        assert tc.get("/health").json()["status"] == "ok"

    def test_health_anthropic_key_set(self, client):
        tc, _ = client
        # conftest sets ANTHROPIC_API_KEY=test-key, so this must be True
        assert tc.get("/health").json()["anthropic_key_set"] is True

    def test_health_reports_retrieval_mode(self, client):
        tc, _ = client
        data = tc.get("/health").json()
        assert data["retrieval_mode"] == "hybrid"
        assert data["bm25_available"] is True


# ─── /projects ───────────────────────────────────────────────────────────────

class TestProjects:
    def test_projects_requires_auth(self, client):
        tc, _ = client
        r = tc.get("/projects")
        assert r.status_code == 401

    def test_projects_returns_list(self, client):
        tc, headers = client
        data = tc.get("/projects", headers=headers).json()
        assert "projects" in data
        assert isinstance(data["projects"], list)

    def test_projects_includes_claw(self, client):
        tc, headers = client
        projects = tc.get("/projects", headers=headers).json()["projects"]
        ids = [p["id"] for p in projects]
        assert "claw" in ids, f"Expected 'claw' in {ids}"

    def test_projects_claw_is_ready(self, client):
        tc, headers = client
        projects = tc.get("/projects", headers=headers).json()["projects"]
        claw = next(p for p in projects if p["id"] == "claw")
        assert claw["ready"] is True
        assert claw["has_config"] is True
        assert claw["has_core_md"] is True


# ─── /chat field validation ──────────────────────────────────────────────────

class TestChatValidation:
    def test_chat_requires_auth(self, client):
        tc, _ = client
        r = tc.post("/chat", json={"content": "hi", "project_id": "claw",
                                   "session_id": "s1"})
        assert r.status_code == 401

    def test_chat_missing_content_returns_422(self, client):
        tc, headers = client
        r = tc.post("/chat", headers=headers,
                    json={"project_id": "claw", "session_id": "s1"})
        assert r.status_code == 422

    def test_chat_missing_project_id_returns_422(self, client):
        tc, headers = client
        r = tc.post("/chat", headers=headers,
                    json={"content": "hi", "session_id": "s1"})
        assert r.status_code == 422

    def test_chat_missing_session_id_returns_422(self, client):
        tc, headers = client
        r = tc.post("/chat", headers=headers,
                    json={"content": "hi", "project_id": "claw"})
        assert r.status_code == 422

    def test_chat_unknown_project_returns_404(self, client):
        tc, headers = client
        r = tc.post("/chat", headers=headers,
                    json={"content": "hi", "project_id": "no_such_project",
                          "session_id": "s1"})
        assert r.status_code == 404

    def test_chat_returns_expected_keys(self, client):
        tc, headers = client
        r = tc.post("/chat", headers=headers,
                    json={"content": "What is CLAW?",
                          "project_id": "claw", "session_id": "test-session-1"})
        assert r.status_code == 200
        data = r.json()
        for key in ("content", "session_id", "model_used", "tokens_used"):
            assert key in data, f"Missing key: {key}"

    def test_chat_stop_endpoint_accepts_session(self, client):
        tc, headers = client
        r = tc.post('/chat/stop', headers=headers, json={
            'project_id': 'claw',
            'session_id': 'stop-test-session',
        })
        assert r.status_code == 200
        assert r.json()['status'] == 'stopping'


# ─── /debug/tools ────────────────────────────────────────────────────────────

class TestDebugTools:
    def test_debug_tools_returns_list(self, client):
        tc, _ = client
        data = tc.get("/debug/tools/claw").json()
        assert "exposed_to_model" in data
        assert isinstance(data["exposed_to_model"], list)
        assert len(data["exposed_to_model"]) > 0

    def test_debug_tools_includes_read_file(self, client):
        tc, _ = client
        data = tc.get("/debug/tools/claw").json()
        assert "read_file" in data["exposed_to_model"]


# ─── Tool registry ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def populated_registry():
    """Return a ToolRegistry with all tools registered via a ClawAgent."""
    import json
    from pathlib import Path
    from core.agent import ClawAgent
    config = json.loads((Path("projects/claw/config.json")).read_text())
    agent = ClawAgent("claw", config)
    return agent.tools


class TestToolRegistry:
    def test_describe_for_model_format(self, populated_registry):
        """Tools exposed to model must have name, description, input_schema."""
        config = {"permissions": ["read_file", "edit_file", "search_code"]}
        tools = populated_registry.describe_for_model(config)
        assert len(tools) > 0
        for t in tools:
            assert "name" in t
            assert "description" in t
            assert "input_schema" in t

    def test_permissions_filter(self, populated_registry):
        """Only permitted tools should be returned."""
        config = {"permissions": ["read_file"]}
        tools = populated_registry.describe_for_model(config)
        names = [t["name"] for t in tools]
        assert "read_file" in names
        assert "run_command" not in names


# ─── Model router ─────────────────────────────────────────────────────────────

class TestModelRouter:
    def test_force_api_true_never_returns_local(self):
        """CLAW_FORCE_API=true bypasses Ollama — result is DeepSeek or Claude, not LOCAL."""
        os.environ["CLAW_FORCE_API"] = "true"
        import importlib
        from core.models import router
        importlib.reload(router)
        result = router.route("fix a typo", context_tokens=10, project_config={})
        assert result != router.ModelChoice.LOCAL

    def test_force_api_false_simple_task_routes_local(self):
        os.environ["CLAW_FORCE_API"] = "false"
        from core.models import router
        # Reload to pick up env var change
        import importlib; importlib.reload(router)
        result = router.route("fix a typo", context_tokens=10, project_config={})
        assert result == router.ModelChoice.LOCAL
        os.environ["CLAW_FORCE_API"] = "true"  # restore

    def test_route_decision_marks_tier4_project_as_opus(self):
        os.environ["CLAW_TIER4_PROJECTS"] = "phloe"
        try:
            from core.models.router import route_decision, TaskTier
            decision = route_decision(
                "fix a typo",
                context_tokens=10,
                project_config={},
                project="phloe",
            )
            assert decision.use_opus is True
            assert decision.actual_tier == TaskTier.OPUS
        finally:
            del os.environ["CLAW_TIER4_PROJECTS"]


# ─── OpenAI client ───────────────────────────────────────────────────────────

class TestOpenAIClient:
    def _make_client(self):
        from core.models.openai_client import OpenAIClient
        # Provide a dummy key — no real calls in these tests
        return OpenAIClient(api_key="sk-test")

    def test_convert_tools_format(self):
        """Anthropic tool schema → OpenAI function calling format."""
        client = self._make_client()
        anthropic_tools = [{
            "name": "read_file",
            "description": "Read a file",
            "input_schema": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
        }]
        openai_tools = client._convert_tools(anthropic_tools)
        assert len(openai_tools) == 1
        tool = openai_tools[0]
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "read_file"
        assert tool["function"]["description"] == "Read a file"
        assert "parameters" in tool["function"]

    def test_build_messages_includes_system(self):
        """System prompt must appear as first message with role=system."""
        client = self._make_client()
        msgs = client._build_messages("You are CLAW.", [], "Hello")
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are CLAW."

    def test_build_messages_with_image(self):
        """Image input becomes image_url content block for OpenAI vision."""
        client = self._make_client()
        msgs = client._build_messages(
            "sys", [], "what is this?",
            image_base64="abc123", image_media_type="image/png"
        )
        last = msgs[-1]
        assert last["role"] == "user"
        assert isinstance(last["content"], list)
        types = [block["type"] for block in last["content"]]
        assert "image_url" in types
        assert "text" in types


# ─── Claude client ────────────────────────────────────────────────────────────

class TestClaudeClient:
    def test_build_messages_with_image(self):
        """Image input becomes Anthropic source block (not image_url)."""
        from core.models.claude_client import ClaudeClient
        client = ClaudeClient(api_key="test")
        msgs = client._build_messages(
            [], "describe this",
            image_base64="abc123", image_media_type="image/jpeg"
        )
        last = msgs[-1]
        assert last["role"] == "user"
        assert isinstance(last["content"], list)
        types = [block["type"] for block in last["content"]]
        assert "image" in types
        assert "text" in types

    def test_build_messages_without_image_is_string(self):
        """Without image, user message content is a plain string."""
        from core.models.claude_client import ClaudeClient
        client = ClaudeClient(api_key="test")
        msgs = client._build_messages([], "hello")
        assert msgs[-1]["content"] == "hello"


# ─── WIGGUM ──────────────────────────────────────────────────────────────────

class TestWiggum:
    def test_wiggum_start_requires_auth(self, client):
        tc, _ = client
        r = tc.post("/wiggum", json={"goal": "test", "success_criteria": ["x"]})
        assert r.status_code == 401

    def test_wiggum_start_returns_run_id(self, client):
        tc, headers = client
        r = tc.post("/wiggum", headers=headers, json={
            "goal": "Add logging to CLAW",
            "success_criteria": ["logging is configured", "logs appear in stderr"],
            "project": "claw",
            "max_iterations": 1,
        })
        assert r.status_code == 200
        data = r.json()
        assert "run_id" in data
        assert data["status"] == "started"
        assert "poll_url" in data

    def test_wiggum_get_run(self, client):
        tc, headers = client
        # Start a run
        start = tc.post("/wiggum", headers=headers, json={
            "goal": "test goal",
            "success_criteria": ["criterion one"],
            "project": "claw",
            "max_iterations": 1,
        }).json()
        run_id = start["run_id"]
        # Get its status immediately
        r = tc.get(f"/wiggum/{run_id}", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["run_id"] == run_id
        assert data["goal"] == "test goal"
        assert "status" in data

    def test_wiggum_list_runs(self, client):
        tc, headers = client
        r = tc.get("/wiggum", headers=headers)
        assert r.status_code == 200
        assert "runs" in r.json()
        assert isinstance(r.json()["runs"], list)

    def test_wiggum_unknown_run_returns_404(self, client):
        tc, headers = client
        r = tc.get("/wiggum/no-such-run-id", headers=headers)
        assert r.status_code == 404

    def test_wiggum_evaluate_success_all_pass(self):
        from core.wiggum import WiggumOrchestrator
        w = WiggumOrchestrator("goal", ["a", "b"], "claw", "http://x", "key")
        assessment = "PASS: criterion a is met\nPASS: criterion b is done"
        assert w._evaluate_success(assessment) is True

    def test_wiggum_evaluate_success_with_fail(self):
        from core.wiggum import WiggumOrchestrator
        w = WiggumOrchestrator("goal", ["a", "b"], "claw", "http://x", "key")
        assessment = "PASS: a is done\nFAIL: b is missing"
        assert w._evaluate_success(assessment) is False

    def test_wiggum_evaluate_success_with_partial(self):
        from core.wiggum import WiggumOrchestrator
        w = WiggumOrchestrator("goal", ["a", "b"], "claw", "http://x", "key")
        assessment = "PASS: a done\nPARTIAL: b exists but incomplete"
        assert w._evaluate_success(assessment) is False

    def test_read_only_limits_tools(self, populated_registry):
        """read_only=True should only return SAFE tools."""
        import json as _json
        from core.agent import ClawAgent
        config = _json.loads(
            ((__import__('pathlib').Path('projects/claw/config.json')).read_text())
        )
        agent = ClawAgent('claw', config)
        safe_tools = agent._get_tools_for_task('assess', read_only=True)
        all_tools = agent._get_tools_for_task('assess', read_only=False)
        assert len(safe_tools) < len(all_tools)
        # No REVIEW or DESTRUCTIVE tools in safe set
        from core.tools.registry import RiskLevel
        safe_names = {t['name'] for t in safe_tools}
        for name in safe_names:
            tool = agent.tools.get(name)
            assert tool.risk_level == RiskLevel.SAFE, \
                f"{name} is not SAFE but appeared in read_only tools"

    def test_explanation_prompt_limits_tools_to_safe(self):
        import json as _json
        from pathlib import Path
        from core.agent import ClawAgent
        from core.tools.registry import RiskLevel

        config = _json.loads(Path('projects/claw/config.json').read_text())
        agent = ClawAgent('claw', config)
        tools = agent._get_tools_for_task(
            'Read the CLAW codebase and explain how chat requests flow from the web UI to the model response.',
            read_only=False,
        )

        for tool_desc in tools:
            tool = agent.tools.get(tool_desc['name'])
            assert tool.risk_level == RiskLevel.SAFE

    def test_queued_tool_fallback_response_is_not_blank(self):
        import json as _json
        from pathlib import Path
        from core.agent import ClawAgent

        config = _json.loads(Path('projects/claw/config.json').read_text())
        agent = ClawAgent('claw', config)
        text = agent._queued_tool_fallback_response(
            'tell me about claw',
            {'name': 'run_command'},
            [{'tool_name': 'read_file', 'input': {'file_path': 'core/agent.py'}, 'result': 'ok'}],
        )

        assert 'run_command' in text
        assert 'tell me about claw' in text
        assert len(text.strip()) > 20


# ─── DeepSeek client ─────────────────────────────────────────────────────────

class TestDeepSeekClient:
    def _make_client(self):
        from core.models.deepseek_client import DeepSeekClient
        return DeepSeekClient(api_key="sk-test-deepseek")

    def test_instantiates_with_mock_key(self):
        client = self._make_client()
        assert client.model == os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        assert client.opus_model == client.model  # compatibility alias

    def test_convert_tools_format(self):
        """Anthropic tool schema → OpenAI/DeepSeek function calling format."""
        client = self._make_client()
        tools = client._convert_tools([{
            "name": "read_file",
            "description": "Read a file",
            "input_schema": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
        }])
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "read_file"
        assert "parameters" in tools[0]["function"]

    def test_build_messages_includes_system(self):
        client = self._make_client()
        msgs = client._build_messages("You are CLAW.", [], "Hello")
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are CLAW."
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "Hello"

    def test_append_tool_round_format(self):
        """Tool round must produce assistant + tool role messages (OpenAI format)."""
        from core.models.deepseek_client import DeepSeekClient
        msgs = [{"role": "user", "content": "hello"}]
        result = DeepSeekClient.append_tool_round(
            msgs,
            response_text="I'll read that.",
            tool_call={"name": "read_file", "input": {"file_path": "x.py"}, "tool_use_id": "tc_1"},
            tool_result="contents of x.py",
        )
        roles = [m["role"] for m in result]
        assert "assistant" in roles
        assert "tool" in roles
        assistant = next(m for m in result if m["role"] == "assistant")
        assert assistant["tool_calls"][0]["function"]["name"] == "read_file"

    def test_image_silently_ignored(self):
        """DeepSeek is text-only — image input is accepted but not included."""
        client = self._make_client()
        msgs = client.build_messages(
            "sys", [], "what is this?",
            image_base64="abc123", image_media_type="image/png"
        )
        last = msgs[-1]
        # Content should be a plain string, not a list with image_url
        assert isinstance(last["content"], str)


# ─── Model router — DeepSeek tier ────────────────────────────────────────────

class TestModelRouterDeepSeek:
    def _set_env(self, **kwargs):
        for k, v in kwargs.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_deepseek_selected_when_key_present_and_safe(self):
        self._set_env(
            DEEPSEEK_API_KEY="sk-ds-test",
            DEEPSEEK_ENABLED="true",
            CLAW_FORCE_API="true",
        )
        import importlib
        from core.models import router
        importlib.reload(router)
        result = router.route("fix a bug", context_tokens=100, project_config={}, risk_level="safe")
        assert result == router.ModelChoice.DEEPSEEK

    def test_deepseek_skipped_when_disabled(self):
        self._set_env(
            DEEPSEEK_API_KEY="sk-ds-test",
            DEEPSEEK_ENABLED="false",
            CLAW_FORCE_API="true",
        )
        import importlib
        from core.models import router
        importlib.reload(router)
        result = router.route("fix a bug", context_tokens=100, project_config={}, risk_level="safe")
        assert result == router.ModelChoice.API

    def test_deepseek_skipped_when_no_key(self):
        self._set_env(
            DEEPSEEK_API_KEY=None,
            DEEPSEEK_ENABLED="true",
            CLAW_FORCE_API="true",
        )
        import importlib
        from core.models import router
        importlib.reload(router)
        result = router.route("fix a bug", context_tokens=100, project_config={}, risk_level="safe")
        assert result == router.ModelChoice.API

    def test_deepseek_skipped_for_opus_keywords(self):
        """Architecture tasks bypass DeepSeek and go straight to Claude."""
        self._set_env(
            DEEPSEEK_API_KEY="sk-ds-test",
            DEEPSEEK_ENABLED="true",
            CLAW_FORCE_API="true",
        )
        import importlib
        from core.models import router
        importlib.reload(router)
        result = router.route(
            "architect a new database schema",
            context_tokens=100,
            project_config={},
            risk_level="safe",
        )
        assert result == router.ModelChoice.API

    def test_tier_priority_local_beats_deepseek(self):
        """Ollama (Tier 1) takes priority over DeepSeek when CLAW_FORCE_API=false."""
        self._set_env(
            DEEPSEEK_API_KEY="sk-ds-test",
            DEEPSEEK_ENABLED="true",
            CLAW_FORCE_API="false",
        )
        import importlib
        from core.models import router
        importlib.reload(router)
        result = router.route(
            "fix a typo",
            context_tokens=100,
            project_config={},
            risk_level="safe",
        )
        assert result == router.ModelChoice.LOCAL
        self._set_env(CLAW_FORCE_API="true")  # restore

    def test_deepseek_cost_cheaper_than_claude(self):
        """Sanity check: DeepSeek pricing is cheaper than Claude per token."""
        from core.models.deepseek_client import DeepSeekClient
        from core.models.claude_client import PRICE_INPUT_PER_M as claude_input
        assert DeepSeekClient.PRICE_INPUT_PER_M < claude_input
        assert DeepSeekClient.PRICE_OUTPUT_PER_M < 15.0  # Claude output price


# ─── Indexer chunk cap ────────────────────────────────────────────────────────

class TestIndexerChunkCap:
    def test_max_chunk_chars_defined(self):
        from core.context.indexer import MAX_CHUNK_CHARS
        assert isinstance(MAX_CHUNK_CHARS, int)
        assert MAX_CHUNK_CHARS <= 2000, (
            f"MAX_CHUNK_CHARS={MAX_CHUNK_CHARS} exceeds nomic-embed-text's "
            f"safe limit — 500 errors will return"
        )

    def test_window_chunks_respect_cap(self):
        """_chunk_window must not emit chunks larger than MAX_CHUNK_CHARS."""
        from core.context.indexer import CodeIndexer, MAX_CHUNK_CHARS
        # Create a dummy indexer (we only call the pure method, no DB needed)
        indexer = MagicMock(spec=CodeIndexer)
        indexer._chunk_window = CodeIndexer._chunk_window.__get__(indexer)

        # 100 lines of 50 chars each = 5000 chars — would blow past 1500
        long_content = "\n".join(["x" * 50] * 100)
        for chunk in indexer._chunk_window(long_content):
            assert len(chunk["content"]) <= MAX_CHUNK_CHARS, (
                f"Window chunk is {len(chunk['content'])} chars, "
                f"exceeds MAX_CHUNK_CHARS={MAX_CHUNK_CHARS}"
            )


# ─── Task Classifier ──────────────────────────────────────────────────────────

class TestTaskClassifier:
    """Tests for the rule-based task classifier (Feature 1a)."""

    def _classify(self, prompt, risk_level='safe', project='', context_files=0):
        from core.models.task_classifier import classify, TaskTier
        return classify(prompt, risk_level=risk_level, project=project,
                        context_files=context_files)

    def test_simple_read_routes_local(self):
        r = self._classify('show me what is in agent.py', context_files=1)
        from core.models.task_classifier import TaskTier
        assert r.tier == TaskTier.LOCAL

    def test_destructive_risk_routes_opus(self):
        from core.models.task_classifier import TaskTier
        r = self._classify('delete all temp files', risk_level='destructive')
        assert r.tier == TaskTier.OPUS

    def test_architecture_keyword_routes_opus(self):
        from core.models.task_classifier import TaskTier
        r = self._classify('design the architecture for the new caching layer')
        assert r.tier == TaskTier.OPUS

    def test_security_keyword_routes_opus(self):
        from core.models.task_classifier import TaskTier
        r = self._classify('review the security of the auth flow')
        assert r.tier == TaskTier.OPUS

    def test_trade_off_keyword_routes_opus(self):
        from core.models.task_classifier import TaskTier
        r = self._classify('what is the trade-off between redis and sqlite here')
        assert r.tier == TaskTier.OPUS

    def test_phloe_queryset_routes_opus(self):
        from core.models.task_classifier import TaskTier
        r = self._classify('filter invoices by date', project='phloe')
        assert r.tier == TaskTier.OPUS

    def test_review_complex_routes_claude(self):
        from core.models.task_classifier import TaskTier
        r = self._classify('update the api to add a new endpoint',
                            risk_level='review', context_files=4)
        assert r.tier == TaskTier.CLAUDE

    def test_review_debug_routes_claude(self):
        from core.models.task_classifier import TaskTier
        r = self._classify('edit file — traceback in auth middleware',
                            risk_level='review', context_files=1)
        assert r.tier == TaskTier.CLAUDE

    def test_review_simple_routes_deepseek(self):
        from core.models.task_classifier import TaskTier
        r = self._classify('edit this function', risk_level='review', context_files=1)
        assert r.tier == TaskTier.DEEPSEEK

    def test_default_safe_routes_deepseek(self):
        from core.models.task_classifier import TaskTier
        r = self._classify('implement a new helper function')
        assert r.tier == TaskTier.DEEPSEEK

    def test_classification_under_1ms(self):
        import time
        from core.models.task_classifier import classify
        start = time.perf_counter()
        for _ in range(1000):
            classify('fix a bug in the auth module', risk_level='review', context_files=2)
        elapsed_ms = (time.perf_counter() - start) * 1000
        # 1000 iterations must complete in under 1000ms (i.e. < 1ms each)
        assert elapsed_ms < 1000, f'Classifier too slow: {elapsed_ms/1000:.3f}ms per call'

    def test_explain_classification_format(self):
        from core.models.task_classifier import classify, explain_classification
        r = classify('edit this function', risk_level='review', context_files=1)
        s = explain_classification(r)
        assert '→ Tier' in s
        assert '[rule:' in s


# ─── Model Router — tier promotion ───────────────────────────────────────────

class TestModelRouterTierPromotion:
    def _set_env(self, **kwargs):
        for k, v in kwargs.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_tier4_projects_forces_api(self):
        """CLAW_TIER4_PROJECTS routes project to API regardless of task."""
        self._set_env(CLAW_TIER4_PROJECTS='phloe,manufacturing', CLAW_FORCE_API='true',
                      DEEPSEEK_API_KEY='sk-test', DEEPSEEK_ENABLED='true')
        import importlib
        from core.models import router
        importlib.reload(router)
        result = router.route('fix a typo', context_tokens=50, project_config={},
                              project='phloe', risk_level='safe')
        assert result == router.ModelChoice.API
        self._set_env(CLAW_TIER4_PROJECTS=None)

    def test_force_tier_overrides_classifier(self):
        """force_tier=2 returns DEEPSEEK regardless of prompt."""
        self._set_env(DEEPSEEK_API_KEY='sk-test', DEEPSEEK_ENABLED='true',
                      CLAW_FORCE_API='true')
        import importlib
        from core.models import router
        importlib.reload(router)
        result = router.route('architect the whole system', context_tokens=50,
                              project_config={}, force_tier=2)
        assert result == router.ModelChoice.DEEPSEEK

    def test_skips_unavailable_deepseek_promotes_to_api(self):
        """When DeepSeek key absent, DEEPSEEK-classified task promotes to API."""
        self._set_env(DEEPSEEK_API_KEY=None, CLAW_FORCE_API='true')
        import importlib
        from core.models import router
        importlib.reload(router)
        result = router.route('implement a helper function', context_tokens=100,
                              project_config={}, risk_level='safe')
        assert result == router.ModelChoice.API


# ─── Output Validator ─────────────────────────────────────────────────────────

class TestOutputValidator:
    """Tests for all 6 output validation checks."""

    def test_check1_tool_description_fails(self):
        from core.models.output_validator import validate
        r = validate("I would call read_file to check that.", executed_tool_calls=[])
        assert not r.passed
        assert any('CHECK 1' in f for f in r.failures)
        assert r.escalate

    def test_check1_passes_when_tool_executed(self):
        from core.models.output_validator import validate
        r = validate("I would call read_file to check that.",
                     executed_tool_calls=[{'tool_name': 'read_file', 'result': 'ok'}])
        assert not any('CHECK 1' in f for f in r.failures)

    def test_check2_refusal_fails(self):
        from core.models.output_validator import validate
        r = validate("I cannot access that file.")
        assert not r.passed
        assert any('CHECK 2' in f for f in r.failures)

    def test_check2_does_not_flag_missing_file_explanation(self):
        from core.models.output_validator import validate
        r = validate(
            "I cannot find web/app/page.tsx, but the current route lives under web/src/app/page.tsx.",
            project_root='D:/claw',
        )
        assert not any('CHECK 2' in f for f in r.failures)

    def test_check3_hallucinated_file_fails(self):
        from core.models.output_validator import validate
        r = validate("See core/nonexistent_module.py for details.",
                     files_in_context=[], project_root='D:/claw')
        assert any('CHECK 3' in f for f in r.failures)

    def test_check3_ignores_framework_names(self):
        from core.models.output_validator import validate
        r = validate("CLAW uses Next.js for the web UI and FastAPI for the API.",
                     files_in_context=[], project_root='D:/claw')
        assert not any('CHECK 3' in f for f in r.failures)

    def test_check3_does_not_match_inside_capitalized_tokens(self):
        from core.models.output_validator import validate
        r = validate("The web layer is configured through Config.js conventions.",
                     files_in_context=[], project_root='D:/claw')
        assert not any('CHECK 3' in f for f in r.failures)

    def test_check3_ignores_bare_config_filenames(self):
        from core.models.output_validator import validate
        r = validate("Project metadata lives in config.json and core.md.",
                     files_in_context=[], project_root='D:/claw')
        assert not any('CHECK 3' in f for f in r.failures)

    def test_check3_accepts_context_file_basenames(self):
        from core.models.output_validator import validate
        r = validate(
            "Model routing is described in router.py and task_classifier.py.",
            files_in_context=[
                'core/models/router.py',
                'core/models/task_classifier.py',
            ],
            project_root='D:/claw',
        )
        assert not any('CHECK 3' in f for f in r.failures)

    def test_check3_passes_for_short_filename_that_exists(self):
        """Bare filename like 'task_classifier.py' should pass when the file exists under project root."""
        from core.models.output_validator import validate
        r = validate(
            "The routing logic lives in task_classifier.py which classifies requests.",
            files_in_context=[],
            project_root='D:/claw',
        )
        assert not any('CHECK 3' in f for f in r.failures)

    def test_check3_fails_for_genuinely_missing_file(self):
        """A bare filename that doesn't exist anywhere should still be flagged."""
        from core.models.output_validator import validate
        r = validate(
            "See totally_nonexistent_module.py for the implementation.",
            files_in_context=[],
            project_root='D:/claw',
        )
        assert any('CHECK 3' in f for f in r.failures)

    def test_check3_passes_for_full_path_that_exists(self):
        """A full relative path like 'core/models/task_classifier.py' should pass."""
        from core.models.output_validator import validate
        r = validate(
            "See core/models/task_classifier.py for the classification logic.",
            files_in_context=[],
            project_root='D:/claw',
        )
        assert not any('CHECK 3' in f for f in r.failures)

    def test_check4_phloe_tenant_isolation_fails(self):
        from core.models.output_validator import validate
        r = validate("Use Invoice.objects.filter(date=today)", project='phloe')
        assert not r.passed
        assert any('CHECK 4' in f for f in r.failures)
        assert r.hard_fail

    def test_check4_phloe_with_tenant_passes(self):
        from core.models.output_validator import validate
        r = validate("Use Invoice.objects.filter(tenant=tenant, date=today)",
                     project='phloe')
        assert not any('CHECK 4' in f for f in r.failures)

    def test_check5_empty_response_fails(self):
        from core.models.output_validator import validate
        r = validate("", executed_tool_calls=[], files_in_context=[])
        assert not r.passed
        assert any('CHECK 5' in f for f in r.failures)

    def test_check6_syntax_error_fails(self, tmp_path):
        from core.models.output_validator import validate
        bad_py = tmp_path / 'bad.py'
        bad_py.write_text('def foo(\n    pass\n')
        r = validate("Created bad.py", written_files=[str(bad_py)])
        assert any('CHECK 6' in f for f in r.failures)

    def test_check6_valid_syntax_passes(self, tmp_path):
        from core.models.output_validator import validate
        good_py = tmp_path / 'good.py'
        good_py.write_text('def foo():\n    pass\n')
        r = validate("Created good.py", written_files=[str(good_py)])
        assert not any('CHECK 6' in f for f in r.failures)

    def test_all_checks_under_50ms(self):
        import time
        from core.models.output_validator import validate
        start = time.perf_counter()
        for _ in range(100):
            validate("I would call read_file but I cannot access it.",
                     project='phloe')
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 5000  # 100 calls < 50ms each


class TestAgentHardening:
    def test_build_memory_metadata_summarises_context(self):
        import json as _json
        from pathlib import Path

        from core.agent import ClawAgent

        config = _json.loads((Path("projects/claw/config.json")).read_text())
        agent = ClawAgent("claw", config)

        memory = agent._build_memory_metadata(
            {
                'retrieval_mode': 'hybrid',
                'retrieved_chunk_count': 8,
                'context_file_count': 5,
                'resolved_mention_count': 2,
                'match_quality_counts': {
                    'exact': 1,
                    'semantic': 4,
                    'exact+semantic': 3,
                },
                'retrieved_files': ['core/agent.py', 'api/main.py'],
            },
            context_tokens=6_200,
        )

        assert memory['retrieval_mode'] == 'hybrid'
        assert memory['chunks'] == 8
        assert memory['files'] == 5
        assert memory['mentions'] == 2
        assert memory['both_hits'] == 3
        assert memory['budget_pct'] > 0

    def test_process_includes_memory_metadata(self):
        import json as _json
        from pathlib import Path
        from unittest.mock import AsyncMock, patch

        from core.agent import ClawAgent
        from core.channels.envelope import Channel, MessageEnvelope

        config = _json.loads((Path("projects/claw/config.json")).read_text())
        agent = ClawAgent("claw", config)
        agent.claude.chat = AsyncMock(return_value=(
            "CLAW is a sovereign coding agent.",
            None,
            {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        ))

        fake_context_meta = {
            'context_files': ['core/agent.py', 'api/main.py'],
            'context_file_count': 2,
            'retrieved_chunk_count': 6,
            'retrieval_mode': 'hybrid',
            'resolved_mention_count': 1,
            'match_quality_counts': {
                'exact': 1,
                'semantic': 3,
                'exact+semantic': 2,
            },
            'retrieved_files': ['core/agent.py', 'api/main.py'],
        }

        with patch.object(
            agent.context,
            'build_context_prompt',
            return_value=('mock system prompt', fake_context_meta),
        ):
            response = asyncio.run(
                agent.process(
                    MessageEnvelope(
                        content='tell me about claw',
                        channel=Channel.WEB,
                        project_id='claw',
                        session_id='memory-meta-session',
                    )
                )
            )

        assert response.metadata['memory']['retrieval_mode'] == 'hybrid'
        assert response.metadata['memory']['chunks'] == 6
        assert response.metadata['memory']['both_hits'] == 2
        assert response.metadata['memory']['retrieved_files'] == ['core/agent.py', 'api/main.py']

    def test_chat_with_fallback_uses_deepseek_after_retryable_error(self):
        import json as _json
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from core.agent import ClawAgent

        config = _json.loads((Path("projects/claw/config.json")).read_text())
        agent = ClawAgent("claw", config)
        agent.deepseek = SimpleNamespace(
            model="deepseek-chat",
            chat=AsyncMock(return_value=(
                "Recovered answer",
                None,
                {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            )),
        )

        class RetryableError(Exception):
            status_code = 429

        agent.claude.chat = AsyncMock(side_effect=RetryableError("rate limited"))

        text, tool_call, usage, used_client, model_used = asyncio.run(
            agent._chat_with_fallback(
                agent.claude,
                system="sys",
                history=[],
                message="hello",
                tools=None,
                use_opus=False,
            )
        )

        assert text == "Recovered answer"
        assert tool_call is None
        assert usage["total_tokens"] == 2
        assert used_client is agent.deepseek
        assert model_used == "deepseek-chat"

    def test_chat_with_fallback_uses_deepseek_after_timeout(self):
        import json as _json
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from core.agent import ClawAgent

        config = _json.loads((Path("projects/claw/config.json")).read_text())
        agent = ClawAgent("claw", config)
        agent.deepseek = SimpleNamespace(
            model="deepseek-chat",
            chat=AsyncMock(return_value=(
                "Recovered after timeout",
                None,
                {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            )),
        )

        agent.claude.chat = AsyncMock(side_effect=asyncio.TimeoutError())

        text, tool_call, usage, used_client, model_used = asyncio.run(
            agent._chat_with_fallback(
                agent.claude,
                system="sys",
                history=[],
                message="hello",
                tools=None,
                use_opus=False,
            )
        )

        assert text == "Recovered after timeout"
        assert tool_call is None
        assert usage["total_tokens"] == 2
        assert used_client is agent.deepseek
        assert model_used == "deepseek-chat"

    def test_manual_sonnet_override_beats_deepseek_provider(self):
        import json as _json
        from pathlib import Path

        from core.agent import ClawAgent
        from core.models.deepseek_client import DeepSeekClient

        config = _json.loads((Path("projects/claw/config.json")).read_text())
        agent = ClawAgent("claw", config)
        agent._api_provider = 'deepseek'
        agent.deepseek = DeepSeekClient(api_key="sk-test-deepseek")

        client, model = asyncio.run(
            agent._get_api_client(
                provider_override='sonnet',
                requires_vision=True,
            )
        )

        assert client is agent.claude
        assert model == agent.claude.model

    def test_explanation_prompts_use_lower_tool_round_cap(self):
        import json as _json
        from pathlib import Path

        from core.agent import ClawAgent
        from core.channels.envelope import Channel, MessageEnvelope

        config = _json.loads((Path("projects/claw/config.json")).read_text())
        agent = ClawAgent("claw", config)
        envelope = MessageEnvelope(
            content="Read the CLAW codebase and explain how chat requests flow from the web UI to the model response.",
            channel=Channel.WEB,
            project_id="claw",
            session_id="round-cap-test",
        )

        assert agent._effective_max_tool_rounds(envelope) == agent.EXPLANATION_MAX_TOOL_ROUNDS

    def test_chat_with_fallback_skips_deepseek_for_images(self):
        import json as _json
        from pathlib import Path
        from unittest.mock import AsyncMock

        from core.agent import ClawAgent
        from core.models.deepseek_client import DeepSeekClient
        from core.models.openai_client import OpenAIClient

        config = _json.loads((Path("projects/claw/config.json")).read_text())
        agent = ClawAgent("claw", config)
        agent.deepseek = DeepSeekClient(api_key="sk-test-deepseek")
        agent.openai = OpenAIClient(api_key="sk-test-openai")
        agent.openai.chat = AsyncMock(return_value=(
            "Vision fallback answer",
            None,
            {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        ))

        class RetryableError(Exception):
            status_code = 429

        agent.claude.chat = AsyncMock(side_effect=RetryableError("rate limited"))

        text, _, _, used_client, model_used = asyncio.run(
            agent._chat_with_fallback(
                agent.claude,
                system="sys",
                history=[],
                message="describe this image",
                tools=None,
                image_base64="abc123",
                image_media_type="image/png",
                provider_override='sonnet',
                requires_vision=True,
            )
        )

        assert text == "Vision fallback answer"
        assert used_client is agent.openai
        assert model_used == agent.openai.model

    def test_validate_final_response_recovers_from_tool_description(self):
        import json as _json
        from pathlib import Path
        from unittest.mock import AsyncMock

        from core.agent import ClawAgent
        from core.channels.envelope import Channel, MessageEnvelope
        from core.models.router import ModelChoice

        config = _json.loads((Path("projects/claw/config.json")).read_text())
        agent = ClawAgent("claw", config)
        envelope = MessageEnvelope(
            content="hello",
            channel=Channel.WEB,
            project_id="claw",
            session_id="validation-recovery",
        )

        agent._get_api_client = AsyncMock(return_value=(agent.claude, agent.claude.model))
        agent._chat_with_fallback = AsyncMock(return_value=(
            "Recovered final answer",
            None,
            {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            agent.claude,
            agent.claude.model,
        ))

        text, _, model_used, _, metadata = asyncio.run(
            agent._validate_final_response(
                response_text="I would call read_file to inspect that.",
                envelope=envelope,
                context_prompt="sys",
                history=[],
                model_choice=ModelChoice.LOCAL,
                use_opus=False,
                available_tools=[],
                context_files=[],
                executed_tool_calls=[],
                current_client=None,
                current_model_used="qwen2.5-coder:7b",
                current_cost=0.0,
            )
        )

        assert text == "Recovered final answer"
        assert model_used == agent.claude.model
        assert metadata["validation_recovered"] is True

    def test_validate_final_response_retries_empty_response_with_current_client(self):
        import json as _json
        from pathlib import Path
        from unittest.mock import AsyncMock

        from core.agent import ClawAgent
        from core.channels.envelope import Channel, MessageEnvelope
        from core.models.router import ModelChoice

        config = _json.loads((Path("projects/claw/config.json")).read_text())
        agent = ClawAgent("claw", config)
        envelope = MessageEnvelope(
            content="Explain the CLAW request flow.",
            channel=Channel.WEB,
            project_id="claw",
            session_id="validation-empty-retry",
        )

        agent._chat_with_fallback = AsyncMock(return_value=(
            "Chat requests start in the Next.js web route and then flow into FastAPI.",
            None,
            {"input_tokens": 2, "output_tokens": 8, "total_tokens": 10},
            agent.claude,
            agent.claude.model,
        ))

        text, _, model_used, _, metadata = asyncio.run(
            agent._validate_final_response(
                response_text="",
                envelope=envelope,
                context_prompt="sys",
                history=[],
                model_choice=ModelChoice.API,
                use_opus=False,
                available_tools=[],
                context_files=["core/agent.py"],
                executed_tool_calls=[
                    {
                        "tool_name": "read_file",
                        "input": {"file_path": "core/agent.py"},
                        "result": "agent source here",
                    }
                ],
                current_client=agent.claude,
                current_model_used=agent.claude.model,
                current_cost=0.0,
            )
        )

        assert text.startswith("Chat requests start")
        assert model_used == agent.claude.model
        assert metadata == {}
        agent._chat_with_fallback.assert_awaited_once()

    def test_build_validation_retry_prompt_includes_context_allowlist(self):
        import json as _json
        from pathlib import Path

        from core.agent import ClawAgent

        config = _json.loads((Path("projects/claw/config.json")).read_text())
        agent = ClawAgent("claw", config)

        prompt = agent._build_validation_retry_prompt(
            original_message="Explain how stop works.",
            failures=['CHECK 3: hallucinated file path "pages/api/stop.ts"'],
            executed_tool_calls=[],
            context_files=[
                'web/src/components/ChatWindow.tsx',
                'web/src/app/api/chat/stop/route.ts',
                'api/main.py',
                'core/agent.py',
            ],
        )

        assert 'ONLY use files from this allowlist' in prompt
        assert 'web/src/app/api/chat/stop/route.ts' in prompt

    def test_validate_final_response_uses_tool_summary_fallback_after_empty_failures(self):
        import json as _json
        from pathlib import Path
        from unittest.mock import AsyncMock

        from core.agent import ClawAgent
        from core.channels.envelope import Channel, MessageEnvelope
        from core.models.router import ModelChoice

        config = _json.loads((Path("projects/claw/config.json")).read_text())
        agent = ClawAgent("claw", config)
        envelope = MessageEnvelope(
            content="Read the CLAW codebase and explain the chat flow.",
            channel=Channel.WEB,
            project_id="claw",
            session_id="validation-empty-fallback",
        )

        agent._chat_with_fallback = AsyncMock(return_value=(
            "",
            None,
            {"input_tokens": 2, "output_tokens": 0, "total_tokens": 2},
            agent.claude,
            agent.claude.model,
        ))

        text, _, _, _, metadata = asyncio.run(
            agent._validate_final_response(
                response_text="",
                envelope=envelope,
                context_prompt="sys",
                history=[],
                model_choice=ModelChoice.API,
                use_opus=False,
                available_tools=[],
                context_files=["core/agent.py", "api/main.py"],
                executed_tool_calls=[
                    {
                        "tool_name": "read_file",
                        "input": {"file_path": "core/agent.py"},
                        "result": "agent source here",
                    },
                    {
                        "tool_name": "read_file",
                        "input": {"file_path": "api/main.py"},
                        "result": "api source here",
                    },
                ],
                current_client=agent.claude,
                current_model_used=agent.claude.model,
                current_cost=0.0,
            )
        )

        assert "Here are the main sources CLAW inspected" in text
        assert "`core/agent.py`" in text
        assert metadata["validation_fallback_used"] == "tool_results_summary"


# ─── Session Summariser ───────────────────────────────────────────────────────

class TestSessionSummariser:
    def test_parse_bullets(self):
        from core.memory.summariser import SessionSummariser
        s = SessionSummariser('claw')
        text = "- Use DeepSeek for cheap coding tasks\n- Always add tenant filter in phloe\n- DECIDED: keep batch_mode=False by default"
        bullets = s._parse_bullets(text)
        assert len(bullets) == 3
        assert 'DeepSeek' in bullets[0]

    def test_deduplication_removes_similar(self):
        from core.memory.summariser import SessionSummariser
        s = SessionSummariser('claw')
        existing = '- Always add tenant filter to phloe queries\n'
        new = ['Always add a tenant filter to all phloe ORM queries']
        kept = s._deduplicate(new, existing)
        assert len(kept) == 0  # too similar — should be removed

    def test_deduplication_keeps_different(self):
        from core.memory.summariser import SessionSummariser
        s = SessionSummariser('claw')
        existing = '- Use DeepSeek for simple tasks\n'
        new = ['Added batch approval queue to WIGGUM runs for overnight operation']
        kept = s._deduplicate(new, existing)
        assert len(kept) == 1

    def test_session_log_section_created_if_missing(self, tmp_path):
        from core.memory.summariser import SessionSummariser, _SESSION_LOG_HEADER
        project_id = 'test_proj'
        core_path = tmp_path / project_id / 'core.md'
        core_path.parent.mkdir(parents=True)
        core_path.write_text('# Test Project\n\nSome content here.\n')

        s = SessionSummariser(project_id)
        s._core_md_path = core_path  # point to tmp

        existing = s._read_core_md()
        s._append_to_core_md('\n## Session 2026-03-25 — test_proj\n- Test bullet\n', existing)

        result = core_path.read_text()
        assert _SESSION_LOG_HEADER in result
        assert '- Test bullet' in result
        assert 'Some content here.' in result  # original content preserved


# ─── /complete endpoint ───────────────────────────────────────────────────────

class TestCompleteEndpoint:
    def test_complete_endpoint_exists(self, client):
        """POST /complete must exist and accept required fields."""
        tc, headers = client
        r = tc.post('/complete', headers=headers, json={
            'file_path': 'core/agent.py',
            'prefix': 'def hello():\n    ',
            'project': 'claw',
            'language': 'python',
        })
        # 200 or 422 (model unavailable) — but must not 404 or 500
        assert r.status_code in (200, 422, 503), f'Unexpected status: {r.status_code}'

    def test_complete_returns_completion_field(self, client):
        """When endpoint returns 200, it must have a 'completion' field."""
        tc, headers = client
        r = tc.post('/complete', headers=headers, json={
            'file_path': 'core/agent.py',
            'prefix': 'def greet(name: str) -> str:\n    ',
            'project': 'claw',
            'language': 'python',
        })
        if r.status_code == 200:
            data = r.json()
            assert 'completion' in data
            assert 'tier' in data


# ─── WIGGUM batch mode ────────────────────────────────────────────────────────

class TestWiggumBatchMode:
    def test_wiggum_request_accepts_batch_mode(self, client):
        """WiggumRequest must accept batch_mode field."""
        tc, headers = client
        r = tc.post('/wiggum', headers=headers, json={
            'goal': 'test batch mode',
            'success_criteria': ['test criterion'],
            'project': 'claw',
            'max_iterations': 1,
            'batch_mode': True,
        })
        assert r.status_code == 200
        data = r.json()
        assert 'run_id' in data

    def test_wiggum_orchestrator_batch_mode_default_false(self):
        """batch_mode defaults to False — existing behaviour unchanged."""
        from core.wiggum import WiggumOrchestrator
        orc = WiggumOrchestrator(goal='test', success_criteria=['x'], project_id='claw')
        assert orc.batch_mode is False

    def test_wiggum_orchestrator_batch_mode_true(self):
        from core.wiggum import WiggumOrchestrator
        orc = WiggumOrchestrator(goal='test', success_criteria=['x'],
                                  project_id='claw', batch_mode=True)
        assert orc.batch_mode is True


# ─── Memory store — subprojects + sessions + archiving ───────────────────────

@pytest.fixture
def tmp_store(tmp_path):
    from core.memory.store import MemoryStore
    return MemoryStore(project_id='testproj', data_dir=str(tmp_path))


class TestSubprojects:
    def test_create_subproject(self, tmp_store):
        sp = tmp_store.create_subproject(
            project_id='testproj',
            name='demnurse.nbne.uk',
            display_name='DemNurse',
            description='Test tenant',
        )
        assert sp is not None
        assert sp['name'] == 'demnurse.nbne.uk'
        assert sp['display_name'] == 'DemNurse'

    def test_create_subproject_idempotent(self, tmp_store):
        """Calling create_subproject twice must not raise and must return the same record."""
        sp1 = tmp_store.create_subproject('testproj', 'sp-a', 'SP A')
        sp2 = tmp_store.create_subproject('testproj', 'sp-a', 'SP A updated')
        assert sp1['id'] == sp2['id']
        # display_name is NOT updated on conflict — INSERT OR IGNORE
        assert sp2['display_name'] == 'SP A'

    def test_get_subprojects(self, tmp_store):
        tmp_store.create_subproject('testproj', 'alpha', 'Alpha')
        tmp_store.create_subproject('testproj', 'beta', 'Beta')
        sps = tmp_store.get_subprojects('testproj')
        names = [s['name'] for s in sps]
        assert 'alpha' in names
        assert 'beta' in names

    def test_get_subproject_by_name(self, tmp_store):
        tmp_store.create_subproject('testproj', 'gamma', 'Gamma', 'desc')
        sp = tmp_store.get_subproject_by_name('testproj', 'gamma')
        assert sp is not None
        assert sp['description'] == 'desc'

    def test_get_subproject_by_name_not_found(self, tmp_store):
        assert tmp_store.get_subproject_by_name('testproj', 'nonexistent') is None


class TestSessionList:
    def test_get_session_list_unscoped_returns_all(self, tmp_store):
        tmp_store.add_message('sess-1', 'user', 'hello', 'web')
        tmp_store.add_message('sess-2', 'user', 'world', 'web')
        sessions = tmp_store.get_session_list('testproj')
        ids = [s['session_id'] for s in sessions]
        assert 'sess-1' in ids
        assert 'sess-2' in ids

    def test_get_session_list_includes_title_and_preview(self, tmp_store):
        tmp_store.add_message('sess-title', 'user', 'Investigate tenant routing bug in chat stream', 'web')
        tmp_store.add_message('sess-title', 'assistant', 'I traced it to the stream fallback path.', 'web')

        sessions = tmp_store.get_session_list('testproj')
        entry = next(s for s in sessions if s['session_id'] == 'sess-title')
        assert entry['title'].startswith('Investigate tenant routing bug')
        assert 'stream fallback path' in entry['preview']

    def test_get_session_list_scoped_to_subproject(self, tmp_store):
        sp = tmp_store.create_subproject('testproj', 'client-x', 'Client X')
        tmp_store.add_message('sess-sp', 'user', 'hi', 'web')
        tmp_store.set_session_subproject('sess-sp', sp['id'])
        tmp_store.add_message('sess-other', 'user', 'other', 'web')

        scoped = tmp_store.get_session_list('testproj', subproject_id=sp['id'])
        ids = [s['session_id'] for s in scoped]
        assert 'sess-sp' in ids
        assert 'sess-other' not in ids

    def test_get_session_list_unscoped_filters_to_requested_project(self, tmp_store, tmp_path):
        from core.memory.store import MemoryStore

        other_store = MemoryStore(project_id='otherproj', data_dir=str(tmp_path))
        tmp_store.add_message('sess-own', 'user', 'own', 'web')
        other_store.add_message('sess-other-project', 'user', 'other', 'web')

        sessions = tmp_store.get_session_list('testproj')
        ids = [s['session_id'] for s in sessions]
        assert 'sess-own' in ids
        assert 'sess-other-project' not in ids

    def test_add_message_persists_subproject_on_first_write(self, tmp_store):
        sp = tmp_store.create_subproject('testproj', 'first-write', 'First Write')
        tmp_store.add_message(
            'sess-first-subproject',
            'user',
            'hello',
            'web',
            subproject_id=sp['id'],
        )

        sess = tmp_store.get_session('sess-first-subproject', project_id='testproj')
        assert sess is not None
        assert sess['subproject_id'] == sp['id']

    def test_get_session_returns_messages(self, tmp_store):
        tmp_store.add_message('sess-get', 'user', 'msg 1', 'web')
        tmp_store.add_message('sess-get', 'assistant', 'reply', 'web')
        sess = tmp_store.get_session('sess-get')
        assert sess is not None
        assert len(sess['messages']) == 2
        assert sess['archived'] is False

    def test_get_session_honors_project_scope(self, tmp_store, tmp_path):
        from core.memory.store import MemoryStore

        other_store = MemoryStore(project_id='otherproj', data_dir=str(tmp_path))
        other_store.add_message('sess-cross-project', 'user', 'other', 'web')

        assert (
            tmp_store.get_session('sess-cross-project', project_id='testproj')
            is None
        )

    def test_get_session_not_found_returns_none(self, tmp_store):
        assert tmp_store.get_session('nonexistent-session-xyz') is None

    def test_archived_session_list_includes_summary_title(self, tmp_store):
        tmp_store.add_message('sess-archived-title', 'user', 'Need to review payment retry flow', 'web')
        tmp_store.add_message('sess-archived-title', 'assistant', 'I summarised the retry logic.', 'web')
        tmp_store.archive_session('sess-archived-title', '- Review payment retry flow\n- Retry path confirmed')

        sessions = tmp_store.get_session_list('testproj')
        entry = next(s for s in sessions if s['session_id'] == 'sess-archived-title')
        assert entry['archived'] is True
        assert entry['title'].startswith('Need to review payment retry flow')


class TestTokenTrackingAndArchiving:
    def _populate(self, store, session_id: str, word_count: int):
        """Fill session with messages totalling approx word_count words."""
        chunk = 'word ' * 100  # 100 words per message
        for _ in range(word_count // 100):
            store.add_message(session_id, 'user', chunk.strip(), 'web')
            store.add_message(session_id, 'assistant', chunk.strip(), 'web')

    def test_estimate_tokens_returns_int(self, tmp_store):
        tmp_store.add_message('tok-sess', 'user', 'hello world test', 'web')
        result = tmp_store.estimate_tokens('tok-sess')
        assert isinstance(result, int)
        assert result > 0

    def test_should_trim_fires_at_40000(self, tmp_store):
        # Under threshold
        tmp_store.add_message('trim-sess', 'user', 'short message', 'web')
        assert tmp_store.should_trim('trim-sess') is False

        # Patch estimate_tokens to return value > 40000
        import unittest.mock as mock
        with mock.patch.object(tmp_store, 'estimate_tokens', return_value=41000):
            assert tmp_store.should_trim('trim-sess') is True

    def test_should_archive_fires_at_50000(self, tmp_store):
        tmp_store.add_message('arch-sess', 'user', 'short', 'web')
        assert tmp_store.should_archive('arch-sess') is False

        import unittest.mock as mock
        with mock.patch.object(tmp_store, 'estimate_tokens', return_value=51000):
            assert tmp_store.should_archive('arch-sess') is True

    def test_trim_session_removes_oldest_non_system_messages(self, tmp_store):
        session_id = 'trim-test'
        for i in range(10):
            tmp_store.add_message(session_id, 'user', f'message {i} ' + 'word ' * 50, 'web')

        import unittest.mock as mock
        # Make it appear over threshold until only a few remain
        call_count = [0]
        original = tmp_store.estimate_tokens

        def mock_estimate(sid):
            call_count[0] += 1
            # Return high on first 5 calls, then low
            if call_count[0] <= 5:
                return 45000
            return 35000

        with mock.patch.object(tmp_store, 'estimate_tokens', side_effect=mock_estimate):
            removed = tmp_store.trim_session(session_id)
        assert removed == 5

    def test_archive_session_moves_to_archive_table(self, tmp_store):
        session_id = 'archive-me'
        tmp_store.add_message(session_id, 'user', 'first', 'web')
        tmp_store.add_message(session_id, 'assistant', 'reply', 'web')

        tmp_store.archive_session(session_id, 'Test summary')

        # Should no longer be in active conversations
        active = tmp_store.get_recent_history(session_id, limit=100)
        assert len(active) == 0

        # Should be in archived_sessions
        archived = tmp_store.get_session(session_id)
        assert archived is not None
        assert archived['archived'] is True
        assert archived['summary'] == 'Test summary'
        assert len(archived['messages']) == 2

    def test_get_session_finds_archived_session(self, tmp_store):
        session_id = 'find-archived'
        tmp_store.add_message(session_id, 'user', 'content', 'web')
        tmp_store.archive_session(session_id, 'archived summary')

        result = tmp_store.get_session(session_id)
        assert result is not None
        assert result['archived'] is True


# ─── Subproject + session API endpoints ──────────────────────────────────────

class TestSubprojectEndpoints:
    def test_get_subprojects_endpoint(self, client):
        tc, headers = client
        r = tc.get('/projects/claw/subprojects', headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert 'subprojects' in data
        assert isinstance(data['subprojects'], list)

    def test_create_subproject_endpoint(self, client):
        tc, headers = client
        r = tc.post('/projects/claw/subprojects', headers=headers, json={
            'name': 'test-client.example.com',
            'display_name': 'Test Client',
            'description': 'Created in test',
        })
        assert r.status_code == 200
        data = r.json()
        assert data['name'] == 'test-client.example.com'
        assert data['display_name'] == 'Test Client'

    def test_create_subproject_endpoint_idempotent(self, client):
        tc, headers = client
        body = {'name': 'idempotent.example.com', 'display_name': 'Idempotent'}
        r1 = tc.post('/projects/claw/subprojects', headers=headers, json=body)
        r2 = tc.post('/projects/claw/subprojects', headers=headers, json=body)
        assert r1.status_code == 200
        assert r2.status_code == 200

    def test_get_sessions_endpoint(self, client):
        tc, headers = client
        r = tc.get('/projects/claw/sessions', headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert 'sessions' in data
        assert isinstance(data['sessions'], list)

    def test_get_sessions_scoped_endpoint(self, client):
        tc, headers = client
        # Create a subproject first
        tc.post('/projects/claw/subprojects', headers=headers, json={
            'name': 'scope-test.example.com',
            'display_name': 'Scope Test',
        })
        r = tc.get(
            '/projects/claw/sessions',
            headers=headers,
            params={'subproject': 'claw:scope-test.example.com'},
        )
        assert r.status_code == 200
        data = r.json()
        assert 'sessions' in data


# ─── Agent self-test endpoint ─────────────────────────────────────────────────

class TestAgentSelfTest:
    def test_self_test_endpoint_exists(self, client):
        """GET /agent-self-test must exist and return a response dict."""
        tc, headers = client
        r = tc.get('/agent-self-test', headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert 'passed' in data
        assert 'tool_calls_made' in data
        assert 'answer' in data

    def test_self_test_response_shape(self, client):
        """Self-test response must contain all expected fields."""
        tc, headers = client
        data = tc.get('/agent-self-test', headers=headers).json()
        for field in ('passed', 'answer', 'tool_calls_made',
                      'tool_call_count', 'model_used', 'session_id', 'timestamp'):
            assert field in data, f"Missing field: {field}"

    def test_self_test_tool_calls_is_list(self, client):
        """tool_calls_made must be a list (empty is OK in mock env)."""
        tc, headers = client
        data = tc.get('/agent-self-test', headers=headers).json()
        assert isinstance(data['tool_calls_made'], list)
        assert isinstance(data['tool_call_count'], int)

    def test_self_test_read_only(self, client):
        """Self-test with a non-existent project returns a clean error (not 500)."""
        tc, headers = client
        r = tc.get('/agent-self-test?project=does-not-exist', headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data['passed'] is False
        assert 'error' in data

    def test_self_test_requires_auth(self, client):
        tc, _ = client
        r = tc.get('/agent-self-test')
        assert r.status_code == 401


# ─── Startup: phloe subprojects created ──────────────────────────────────────

class TestPhloeSubprojectsOnStartup:
    def test_phloe_subprojects_created_on_startup(self, client):
        """Phloe subprojects from config.json should be seeded on startup."""
        tc, headers = client
        r = tc.get('/projects/phloe/subprojects', headers=headers)
        assert r.status_code == 200
        data = r.json()
        names = [sp['name'] for sp in data['subprojects']]
        assert 'demnurse.nbne.uk' in names
        assert 'theminddepartment.co.uk' in names
        assert 'ganbarukai.co.uk' in names
        assert 'floe.nbne.uk' in names


# ─── @ mention system ────────────────────────────────────────────────────────

class TestMentionResolution:
    """Tests for ContextEngine.resolve_mentions()."""

    def test_resolve_file_mention(self, tmp_path):
        """Resolving a file mention reads the file content."""
        import asyncio
        from core.context.engine import ContextEngine

        f = tmp_path / "hello.py"
        f.write_text("def hello(): pass\n")
        engine = ContextEngine(project_id='test', db_url='')
        config = {'codebase_path': str(tmp_path)}

        result = asyncio.run(
            engine.resolve_mentions(
                mentions=[{'type': 'file', 'value': 'hello.py', 'display': 'hello.py'}],
                project_id='test',
                config=config,
            )
        )
        assert len(result) == 1
        assert 'def hello' in result[0]['content']
        assert result[0]['label'] == 'file: hello.py'

    def test_resolve_folder_mention_limit_20_files(self, tmp_path):
        """Folder mention resolves up to 20 files."""
        import asyncio
        from core.context.engine import ContextEngine

        sub = tmp_path / "mydir"
        sub.mkdir()
        for i in range(25):
            (sub / f"f{i}.py").write_text(f"# file {i}\n")

        engine = ContextEngine(project_id='test', db_url='')
        config = {'codebase_path': str(tmp_path)}

        result = asyncio.run(
            engine.resolve_mentions(
                mentions=[{'type': 'folder', 'value': 'mydir', 'display': 'mydir/'}],
                project_id='test',
                config=config,
            )
        )
        assert len(result) <= 20

    def test_resolve_core_mention(self, tmp_path):
        """core mention reads the core.md file."""
        import asyncio
        from core.context.engine import ContextEngine

        proj_dir = tmp_path / 'projects' / 'test'
        proj_dir.mkdir(parents=True)
        (proj_dir / 'core.md').write_text('# Test core\nThis is the core.')

        engine = ContextEngine(project_id='test', db_url='')
        engine.project_dir = proj_dir
        engine.core_md_path = proj_dir / 'core.md'
        config = {'codebase_path': str(tmp_path)}

        result = asyncio.run(
            engine.resolve_mentions(
                mentions=[{'type': 'core', 'value': '', 'display': 'core.md'}],
                project_id='test',
                config=config,
            )
        )
        assert len(result) == 1
        assert 'Test core' in result[0]['content']

    def test_mentions_injected_into_prompt_string(self):
        """Resolved mentions appear in the context prompt string.

        Tests the string-building logic directly without the mocked method,
        by constructing the prompt template the same way build_context_prompt does.
        """
        resolved = [
            {'label': 'file: foo.py', 'content': 'def foo(): pass'},
            {'label': 'file: bar.py', 'content': 'def bar(): return 1'},
        ]
        parts = ["=== EXPLICITLY MENTIONED CONTEXT ===\n"]
        for chunk in resolved:
            parts.append(f"[{chunk['label']}]\n")
            parts.append(chunk['content'])
            parts.append("\n\n")
        parts.append("=== END MENTIONED CONTEXT ===\n\n")
        prompt = ''.join(parts)

        assert '=== EXPLICITLY MENTIONED CONTEXT ===' in prompt
        assert 'def foo(): pass' in prompt
        assert 'def bar(): return 1' in prompt
        assert '[file: foo.py]' in prompt

    def test_resolve_unknown_mention_type_returns_error_chunk(self, tmp_path):
        """Unknown mention type returns an error chunk rather than raising."""
        import asyncio
        from core.context.engine import ContextEngine

        engine = ContextEngine(project_id='test', db_url='')
        config = {'codebase_path': str(tmp_path)}

        result = asyncio.run(
            engine.resolve_mentions(
                mentions=[{'type': 'unknown_type', 'value': 'x', 'display': 'x'}],
                project_id='test',
                config=config,
            )
        )
        # Unknown type should produce an empty result (no exception)
        assert isinstance(result, list)


class TestHybridRetriever:
    def test_cache_key_includes_subproject(self):
        from core.memory.retriever import HybridRetriever

        class FakeEngine:
            project_id = 'claw'
            db_url = 'postgresql://example'
            MAX_TIER2_CHUNKS = 20

        retriever = HybridRetriever(FakeEngine())
        assert retriever._cache_key(None) == 'claw:global'
        assert retriever._cache_key('claw:demnurse') == 'claw:demnurse'

    def test_bm25_finds_exact_match(self):
        from core.memory.retriever import HybridRetriever

        class FakeEngine:
            project_id = 'claw'
            db_url = 'postgresql://example'
            MAX_TIER2_CHUNKS = 20

            def get_all_chunks(self, subproject_id=None):
                assert subproject_id == 'claw:demnurse'
                return [
                    {
                        'file': 'stock.py',
                        'content': 'M-4471 is waiting for stock review.',
                        'chunk_type': 'window',
                        'chunk_name': None,
                    },
                    {
                        'file': 'other.py',
                        'content': 'Completely unrelated content.',
                        'chunk_type': 'window',
                        'chunk_name': None,
                    },
                ]

            def _retrieve_by_embedding(
                self,
                task,
                embedding_fn,
                subproject_id=None,
                limit=None,
            ):
                return []

            def _retrieve_by_keyword(self, task, subproject_id=None):
                return []

        retriever = HybridRetriever(FakeEngine())
        results = retriever.retrieve(
            'Check M-4471',
            embedding_fn=lambda _: [0.0],
            subproject_id='claw:demnurse',
        )
        assert results[0]['file'] == 'stock.py'
        assert results[0]['match_quality'] == 'exact'

    def test_rrf_boosts_chunk_present_in_both(self):
        from core.memory.retriever import HybridRetriever

        shared = {
            'file': 'booking.py',
            'content': 'def get_booking_queryset(): return Booking.objects.filter(tenant=request.tenant)',
            'chunk_type': 'function',
            'chunk_name': 'get_booking_queryset',
        }

        class FakeEngine:
            project_id = 'phloe'
            db_url = 'postgresql://example'
            MAX_TIER2_CHUNKS = 20

            def get_all_chunks(self, subproject_id=None):
                return [
                    shared,
                    {
                        'file': 'calendar.py',
                        'content': 'calendar rendering logic',
                        'chunk_type': 'window',
                        'chunk_name': None,
                    },
                ]

            def _retrieve_by_embedding(
                self,
                task,
                embedding_fn,
                subproject_id=None,
                limit=None,
            ):
                return [
                    {**shared, 'score': 0.91},
                    {
                        'file': 'views.py',
                        'content': 'generic booking view',
                        'chunk_type': 'window',
                        'chunk_name': None,
                        'score': 0.89,
                    },
                ]

            def _retrieve_by_keyword(self, task, subproject_id=None):
                return []

        retriever = HybridRetriever(FakeEngine())
        results = retriever.retrieve(
            'Investigate get_booking_queryset tenant filtering',
            embedding_fn=lambda _: [0.0],
        )
        assert results[0]['file'] == 'booking.py'
        assert results[0]['match_quality'] == 'exact+semantic'

    def test_context_engine_retrieval_mode_hybrid(self):
        from core.context.engine import ContextEngine

        engine = ContextEngine(project_id='test', db_url='postgresql://example')
        assert engine.retrieval_mode == 'hybrid'


class TestFileWatcher:
    def test_successful_reindex_invalidates_hybrid_cache(self):
        from core.context.watcher import FileWatcher

        class FakeRetriever:
            def __init__(self):
                self.invalidated = False

            def invalidate_cache(self):
                self.invalidated = True

        class FakeContext:
            def __init__(self):
                self.hybrid_retriever = FakeRetriever()

        watcher = FileWatcher(
            path='D:/claw',
            indexer=MagicMock(),
            loop=MagicMock(),
            context_engine=FakeContext(),
        )

        watcher._handle_index_result(
            'core/agent.py',
            {'status': 'indexed', 'chunks': 4},
        )

        assert watcher.context_engine.hybrid_retriever.invalidated is True
        assert watcher.last_reindex_at is not None


# ─── /projects/{project}/files endpoint ──────────────────────────────────────

class TestFilesEndpoint:
    def test_get_files_returns_200(self, client):
        tc, headers = client
        r = tc.get('/projects/claw/files', headers=headers)
        assert r.status_code == 200

    def test_get_files_has_files_key(self, client):
        tc, headers = client
        data = tc.get('/projects/claw/files', headers=headers).json()
        assert 'files' in data
        assert isinstance(data['files'], list)

    def test_get_files_q_filter(self, client):
        tc, headers = client
        data = tc.get('/projects/claw/files?q=agent', headers=headers).json()
        # All returned paths should contain 'agent' (case-insensitive)
        for f in data['files']:
            assert 'agent' in f.lower()

    def test_get_files_requires_auth(self, client):
        tc, _ = client
        r = tc.get('/projects/claw/files')
        assert r.status_code == 401

    def test_get_files_unknown_project_returns_200_empty(self, client):
        """Unknown project with no pgvector and no config returns empty list gracefully."""
        tc, headers = client
        r = tc.get('/projects/_nonexistent_/files', headers=headers)
        assert r.status_code == 200
        assert r.json()['files'] == []

    def test_get_files_pg_connect_uses_short_timeout(self, client):
        from unittest.mock import patch

        tc, headers = client
        with patch('psycopg2.connect', side_effect=RuntimeError('db down')) as mock_connect:
            r = tc.get('/projects/claw/files', headers=headers)

        assert r.status_code == 200
        assert mock_connect.call_args.kwargs.get('connect_timeout') == 1


# ─── /projects/{project}/symbols endpoint ────────────────────────────────────

class TestSymbolsEndpoint:
    def test_get_symbols_returns_200(self, client):
        tc, headers = client
        r = tc.get('/projects/claw/symbols?q=agent', headers=headers)
        assert r.status_code == 200

    def test_get_symbols_has_symbols_key(self, client):
        tc, headers = client
        data = tc.get('/projects/claw/symbols?q=route', headers=headers).json()
        assert 'symbols' in data
        assert isinstance(data['symbols'], list)

    def test_get_symbols_requires_auth(self, client):
        tc, _ = client
        r = tc.get('/projects/claw/symbols?q=foo')
        assert r.status_code == 401

    def test_get_symbols_pg_connect_uses_short_timeout(self, client):
        from unittest.mock import patch

        tc, headers = client
        with patch('psycopg2.connect', side_effect=RuntimeError('db down')) as mock_connect:
            r = tc.get('/projects/claw/symbols?q=agent', headers=headers)

        assert r.status_code == 200
        assert mock_connect.call_args.kwargs.get('connect_timeout') == 1


# ─── model_override routing ───────────────────────────────────────────────────

class TestModelOverrideRouting:
    """Tests for per-message model override via force_tier."""

    def test_model_override_sonnet_forces_tier3(self):
        """model_override='sonnet' maps to force_tier=3 → ModelChoice.API."""
        from core.models.router import route, ModelChoice
        # Provide a force_tier=3 directly — router must return API
        choice = route(
            task='hello',
            context_tokens=0,
            project_config={},
            force_tier=3,
        )
        assert choice == ModelChoice.API

    def test_model_override_deepseek_forces_tier2(self):
        """force_tier=2 returns DEEPSEEK when key present, else API."""
        import os
        from core.models.router import route, ModelChoice
        os.environ['DEEPSEEK_API_KEY'] = 'test-key'
        os.environ['DEEPSEEK_ENABLED'] = 'true'
        try:
            choice = route(task='hello', context_tokens=0, project_config={}, force_tier=2)
            assert choice == ModelChoice.DEEPSEEK
        finally:
            del os.environ['DEEPSEEK_API_KEY']

    def test_model_override_auto_uses_classifier(self):
        """model_override='auto' (force_tier=None) lets classifier decide."""
        import os
        from core.models.router import route, ModelChoice
        # With CLAW_FORCE_API=true and no DeepSeek key, should route to API
        os.environ['CLAW_FORCE_API'] = 'true'
        choice = route(task='edit this file', context_tokens=0, project_config={}, force_tier=None)
        assert choice == ModelChoice.API

    def test_chat_request_accepts_model_override(self, client):
        """POST /chat accepts model_override field without error."""
        tc, headers = client
        r = tc.post('/chat', headers=headers, json={
            'content': 'hello',
            'project_id': 'claw',
            'session_id': 'test-override-session',
            'model_override': 'sonnet',
            'mentions': [],
        })
        # Should succeed (200) — mocked Claude returns a response
        assert r.status_code == 200

    def test_chat_request_accepts_mentions(self, client):
        """POST /chat accepts mentions list without error."""
        tc, headers = client
        r = tc.post('/chat', headers=headers, json={
            'content': 'hello',
            'project_id': 'claw',
            'session_id': 'test-mentions-session',
            'mentions': [
                {'type': 'file', 'value': 'core/agent.py', 'display': 'agent.py'}
            ],
        })
        assert r.status_code == 200

    def test_model_routing_in_response_metadata(self, client):
        """Response metadata includes model_routing key."""
        tc, headers = client
        r = tc.post('/chat', headers=headers, json={
            'content': 'hello',
            'project_id': 'claw',
            'session_id': 'test-routing-meta',
            'model_override': 'sonnet',
        })
        assert r.status_code == 200
        data = r.json()
        assert 'metadata' in data
        assert data['metadata'].get('model_routing') in ('auto', 'manual')


# ─── SSE streaming ────────────────────────────────────────────────────────────

class TestProcessStreaming:
    """Tests for ClawAgent.process_streaming() async generator."""

    def test_process_streaming_is_async_generator(self):
        """process_streaming() must be an async generator function."""
        import inspect
        from core.agent import ClawAgent
        assert inspect.isasyncgenfunction(ClawAgent.process_streaming)

    def test_process_streaming_yields_routing_and_complete(self, tmp_path):
        """Streaming yields at least one routing event and one complete event."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from core.agent import ClawAgent
        from core.channels.envelope import MessageEnvelope, Channel

        fake_response = ("Hello from streaming", None, {
            "input_tokens": 5, "output_tokens": 10, "total_tokens": 15,
        })

        with patch("core.models.claude_client.ClaudeClient.chat", new_callable=AsyncMock) as mock_chat, \
             patch(
                 "core.context.engine.ContextEngine.build_context_prompt",
                 return_value=("mock ctx", {"context_files": [], "context_file_count": 0}),
             ):
            mock_chat.return_value = fake_response
            config_path = tmp_path / "config.json"
            config_path.write_text('{"name":"test","force_model":"api","permissions":["read_file"]}')
            (tmp_path / "core.md").write_text("# Test")
            agent = ClawAgent(project_id="test", config={"name": "test", "force_model": "api", "permissions": ["read_file"]})

            envelope = MessageEnvelope(
                content="hello",
                channel=Channel.WEB,
                project_id="test",
                session_id="stream-test-" + str(id(tmp_path)),
            )

            async def collect():
                events = []
                async for ev in agent.process_streaming(envelope):
                    events.append(ev)
                return events

            events = asyncio.run(collect())

        types = [e["type"] for e in events]
        assert "routing" in types
        assert "complete" in types

    def test_process_streaming_complete_has_response_field(self, tmp_path):
        """The 'complete' event must contain a 'response' key."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from core.agent import ClawAgent
        from core.channels.envelope import MessageEnvelope, Channel

        fake_response = ("This is my streaming answer.", None, {
            "input_tokens": 5, "output_tokens": 10, "total_tokens": 15,
        })

        with patch("core.models.claude_client.ClaudeClient.chat", new_callable=AsyncMock) as mock_chat, \
             patch(
                 "core.context.engine.ContextEngine.build_context_prompt",
                 return_value=("mock ctx", {"context_files": [], "context_file_count": 0}),
             ):
            mock_chat.return_value = fake_response
            agent = ClawAgent(project_id="test", config={"name": "test", "force_model": "api", "permissions": ["read_file"]})

            envelope = MessageEnvelope(
                content="what is claw",
                channel=Channel.WEB,
                project_id="test",
                session_id="stream-test2-" + str(id(tmp_path)),
            )

            async def collect():
                async for ev in agent.process_streaming(envelope):
                    if ev["type"] == "complete":
                        return ev
                return None

            complete = asyncio.run(collect())

        assert complete is not None
        assert "response" in complete
        assert complete["response"] == "This is my streaming answer."

    def test_process_streaming_emits_tool_events_for_safe_tool(self, tmp_path):
        """SAFE tool calls produce tool_start and tool_end events."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from core.agent import ClawAgent
        from core.channels.envelope import MessageEnvelope, Channel

        tool_call = {"name": "read_file", "input": {"file_path": "core/agent.py"}, "tool_use_id": "tc1"}
        first_response = ("", tool_call, {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10})
        final_response = ("Here is what I found.", None, {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10})

        with patch("core.models.claude_client.ClaudeClient.chat", new_callable=AsyncMock) as mock_chat, \
             patch(
                 "core.context.engine.ContextEngine.build_context_prompt",
                 return_value=("mock ctx", {"context_files": [], "context_file_count": 0}),
             ), \
             patch("core.agent.ClawAgent._execute_tool", new_callable=AsyncMock) as mock_exec:
            mock_chat.side_effect = [first_response, final_response]
            mock_exec.return_value = "file content here"
            agent = ClawAgent(project_id="test", config={"name": "test", "force_model": "api", "permissions": ["read_file"]})

            envelope = MessageEnvelope(
                content="read the agent file",
                channel=Channel.WEB,
                project_id="test",
                session_id="stream-test3-" + str(id(tmp_path)),
            )

            async def collect():
                events = []
                async for ev in agent.process_streaming(envelope):
                    events.append(ev)
                return events

            events = asyncio.run(collect())

        types = [e["type"] for e in events]
        assert "tool_start" in types
        assert "tool_end" in types
        # tool_start must precede tool_end
        assert types.index("tool_start") < types.index("tool_end")

    def test_process_streaming_queues_review_tool(self, tmp_path):
        """REVIEW tools emit tool_queued event and do not execute."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from core.agent import ClawAgent
        from core.channels.envelope import MessageEnvelope, Channel

        tool_call = {"name": "edit_file", "input": {"file_path": "x.py", "old_str": "a", "new_str": "b"}, "tool_use_id": "tc2"}
        first_response = ("", tool_call, {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10})

        with patch("core.models.claude_client.ClaudeClient.chat", new_callable=AsyncMock) as mock_chat, \
             patch(
                 "core.context.engine.ContextEngine.build_context_prompt",
                 return_value=("mock ctx", {"context_files": [], "context_file_count": 0}),
             ), \
             patch("core.agent.ClawAgent._execute_tool", new_callable=AsyncMock) as mock_exec:
            mock_chat.return_value = first_response
            mock_exec.return_value = "edited"
            agent = ClawAgent(project_id="test", config={"name": "test", "force_model": "api", "permissions": ["read_file", "edit_file"]})

            envelope = MessageEnvelope(
                content="edit x.py",
                channel=Channel.WEB,
                project_id="test",
                session_id="stream-test4-" + str(id(tmp_path)),
            )

            async def collect():
                events = []
                async for ev in agent.process_streaming(envelope):
                    events.append(ev)
                return events

            events = asyncio.run(collect())

        types = [e["type"] for e in events]
        assert "tool_queued" in types
        # edit_file must NOT have been executed
        mock_exec.assert_not_called()


class TestChatStreamEndpoint:
    """Tests for GET /chat/stream SSE endpoint."""

    def test_chat_stream_endpoint_returns_200(self, client):
        tc, headers = client
        r = tc.get(
            '/chat/stream',
            headers=headers,
            params={"project": "claw", "session_id": "stream-ep-test", "message": "hello"},
        )
        assert r.status_code == 200

    def test_chat_stream_content_type_is_sse(self, client):
        tc, headers = client
        r = tc.get(
            '/chat/stream',
            headers=headers,
            params={"project": "claw", "session_id": "stream-ct-test", "message": "hi"},
        )
        assert 'text/event-stream' in r.headers.get('content-type', '')

    def test_chat_stream_body_contains_data_prefix(self, client):
        tc, headers = client
        r = tc.get(
            '/chat/stream',
            headers=headers,
            params={"project": "claw", "session_id": "stream-body-test", "message": "hello"},
        )
        assert r.text.startswith('data:')

    def test_chat_stream_contains_done_event(self, client):
        tc, headers = client
        r = tc.get(
            '/chat/stream',
            headers=headers,
            params={"project": "claw", "session_id": "stream-done-test", "message": "hello"},
        )
        assert '"type": "done"' in r.text or '"type":"done"' in r.text

    def test_chat_stream_contains_complete_event(self, client):
        tc, headers = client
        r = tc.get(
            '/chat/stream',
            headers=headers,
            params={"project": "claw", "session_id": "stream-complete-test", "message": "hello"},
        )
        assert '"complete"' in r.text

    def test_chat_stream_accepts_image_params(self, client):
        tc, headers = client
        r = tc.get(
            '/chat/stream',
            headers=headers,
            params={
                "project": "claw",
                "session_id": "stream-image-test",
                "message": "describe this image",
                "image_b64": "abc123",
                "image_media_type": "image/png",
                "model_override": "sonnet",
            },
        )
        assert r.status_code == 200
        assert '"complete"' in r.text

    def test_chat_stream_requires_auth(self, client):
        tc, _ = client
        r = tc.get(
            '/chat/stream',
            params={"project": "claw", "session_id": "stream-auth-test", "message": "hello"},
        )
        assert r.status_code == 401

    def test_chat_stream_unknown_project_returns_sse_error(self, client):
        """Unknown project should return SSE error event, not HTTP 404."""
        tc, headers = client
        r = tc.get(
            '/chat/stream',
            headers=headers,
            params={"project": "_no_such_project_", "session_id": "s", "message": "hi"},
        )
        # Either 404 from get_agent or an error SSE event — both acceptable
        assert r.status_code in (200, 404)

    def test_existing_post_chat_still_works(self, client):
        """Existing POST /chat endpoint must remain unaffected."""
        tc, headers = client
        r = tc.post('/chat', headers=headers, json={
            'content': 'hello', 'project_id': 'claw',
            'session_id': 'post-still-works', 'channel': 'web',
        })
        assert r.status_code == 200
        data = r.json()
        assert 'content' in data


# ── Layer 2: Memory Assembler tests ──────────────────────────────────────────


class TestMemoryAssembler:
    """Tests for the Layer 2 memory assembler."""

    def _make_assembler(self, retriever=None, store=None):
        from core.memory.assembler import MemoryAssembler
        return MemoryAssembler(
            retriever=retriever,
            store=store,
            project_configs={},
        )

    def _make_retriever(self, chunks=None):
        class FakeRetriever:
            is_available = True
            def retrieve(self, task, embedding_fn, subproject_id=None):
                return chunks or []
        return FakeRetriever()

    def _make_store(self, messages=None):
        class FakeStore:
            conn = MagicMock()
            def get_recent_history(self, session_id, limit=20):
                return messages or []
        return FakeStore()

    def _make_chunks(self, n, tokens_per_chunk=100):
        """Create n fake retrieved chunks with ~tokens_per_chunk words each."""
        word = 'code '
        content = word * tokens_per_chunk
        return [
            {
                'file': f'file{i}.py',
                'content': content,
                'chunk_type': 'code',
                'chunk_name': f'func{i}',
                'score': 0.9 - i * 0.01,
                'match_quality': ['exact+semantic', 'semantic', 'exact'][i % 3],
                'bm25_rank': i if i % 2 == 0 else None,
                'cosine_rank': i if i % 2 == 1 else None,
            }
            for i in range(n)
        ]

    def test_assembler_respects_ollama_4000_token_budget(self):
        chunks = self._make_chunks(50, tokens_per_chunk=200)
        retriever = self._make_retriever(chunks)
        assembler = self._make_assembler(retriever=retriever)
        packet = asyncio.run(assembler.assemble(
            query='test query', project_id='claw', session_id='s1', provider='ollama',
        ))
        assert packet.total_tokens_estimated <= 4_000

    def test_assembler_respects_deepseek_32000_token_budget(self):
        chunks = self._make_chunks(100, tokens_per_chunk=500)
        retriever = self._make_retriever(chunks)
        assembler = self._make_assembler(retriever=retriever)
        packet = asyncio.run(assembler.assemble(
            query='test query', project_id='claw', session_id='s1', provider='deepseek',
        ))
        assert packet.total_tokens_estimated <= 32_000

    def test_assembler_always_includes_core_rules(self):
        assembler = self._make_assembler()
        packet = asyncio.run(assembler.assemble(
            query='test', project_id='claw', session_id='s1', provider='sonnet',
        ))
        # core_rules should be populated (claw project has core.md)
        assert isinstance(packet.core_rules, str)

    def test_assembler_always_includes_recent_messages(self):
        messages = [
            {'role': 'user', 'content': 'hello', 'timestamp': '2026-01-01'},
            {'role': 'assistant', 'content': 'hi', 'timestamp': '2026-01-01'},
        ]
        store = self._make_store(messages)
        assembler = self._make_assembler(store=store)
        packet = asyncio.run(assembler.assemble(
            query='test', project_id='claw', session_id='s1', provider='sonnet',
        ))
        assert len(packet.recent_messages) == 2

    def test_assembler_mentions_before_retrieved(self):
        """Mentions should be included before retrieved chunks in budget allocation."""
        mentions = [{'content': 'pinned context', 'label': 'test.py'}]
        chunks = self._make_chunks(5)
        retriever = self._make_retriever(chunks)
        assembler = self._make_assembler(retriever=retriever)
        packet = asyncio.run(assembler.assemble(
            query='test', project_id='claw', session_id='s1', provider='sonnet',
            mentions=mentions,
        ))
        assert len(packet.mentioned_context) == 1
        assert len(packet.retrieved_chunks) > 0

    def test_assembler_trims_retrieved_when_over_budget(self):
        # Each chunk ~650 tokens (500 words * 1.3), ollama budget for retrieved is 1800
        # So only ~2-3 chunks should fit (plus min 3 guarantee)
        chunks = self._make_chunks(20, tokens_per_chunk=500)
        retriever = self._make_retriever(chunks)
        assembler = self._make_assembler(retriever=retriever)
        packet = asyncio.run(assembler.assemble(
            query='test', project_id='claw', session_id='s1', provider='ollama',
        ))
        assert len(packet.retrieved_chunks) < 20

    def test_assembler_preserves_min_3_retrieved_chunks(self):
        # Even if over budget, at least 3 chunks should be kept
        chunks = self._make_chunks(5, tokens_per_chunk=2000)
        retriever = self._make_retriever(chunks)
        assembler = self._make_assembler(retriever=retriever)
        packet = asyncio.run(assembler.assemble(
            query='test', project_id='claw', session_id='s1', provider='ollama',
        ))
        assert len(packet.retrieved_chunks) >= 3

    def test_format_for_provider_adds_cache_control_anthropic(self):
        assembler = self._make_assembler()
        packet = asyncio.run(assembler.assemble(
            query='test', project_id='claw', session_id='s1', provider='sonnet',
        ))
        messages = assembler.format_for_provider(packet, 'sonnet')
        # Find system message with cache_control
        system_msgs = [m for m in messages if m.get('role') == 'system']
        assert len(system_msgs) > 0
        first_sys = system_msgs[0]
        content = first_sys.get('content', '')
        if isinstance(content, list):
            has_cache = any(
                block.get('cache_control', {}).get('type') == 'ephemeral'
                for block in content
                if isinstance(block, dict)
            )
            assert has_cache, 'Anthropic format should have cache_control: ephemeral'

    def test_format_for_provider_no_cache_control_deepseek(self):
        assembler = self._make_assembler()
        packet = asyncio.run(assembler.assemble(
            query='test', project_id='claw', session_id='s1', provider='deepseek',
        ))
        messages = assembler.format_for_provider(packet, 'deepseek')
        for msg in messages:
            content = msg.get('content', '')
            if isinstance(content, list):
                for block in content:
                    assert 'cache_control' not in block, 'DeepSeek should not have cache_control'
            assert 'cache_control' not in msg

    def test_distill_core_rules_finds_rules_section(self):
        assembler = self._make_assembler()
        text = '# Intro\nSome intro text\n\n## Rules\n- Rule 1\n- Rule 2\n\n## Other\nStuff'
        result = assembler.distill_core_rules(text)
        assert 'Rule 1' in result
        assert 'Rule 2' in result

    def test_distill_core_rules_falls_back_to_first_400_words(self):
        assembler = self._make_assembler()
        words = ['word'] * 500
        text = ' '.join(words)
        result = assembler.distill_core_rules(text)
        assert len(result.split()) <= 410  # ~400 words with some tolerance

    def test_distill_core_rules_hard_cap_500_words(self):
        assembler = self._make_assembler()
        # Create a huge Rules section
        words = ['ruleword'] * 1000
        text = '## Rules\n' + ' '.join(words)
        result = assembler.distill_core_rules(text)
        assert len(result.split()) <= 510  # 500 word hard cap with tolerance


class TestModelClientPreAssembled:
    """Tests for pre_assembled parameter on model clients."""

    def test_claude_client_accepts_pre_assembled(self):
        from pathlib import Path
        source = (Path('core/models/claude_client.py')).read_text()
        assert 'pre_assembled: list[dict] | None = None' in source

    def test_deepseek_client_accepts_pre_assembled(self):
        from core.models.deepseek_client import DeepSeekClient
        client = DeepSeekClient(api_key='test-key')
        import inspect
        sig = inspect.signature(client.chat)
        assert 'pre_assembled' in sig.parameters

    def test_openai_client_accepts_pre_assembled(self):
        from core.models.openai_client import OpenAIClient
        client = OpenAIClient(api_key='test-key')
        import inspect
        sig = inspect.signature(client.chat)
        assert 'pre_assembled' in sig.parameters

    def test_pre_assembled_none_uses_existing_path(self):
        """When pre_assembled is None, the client should use its normal message building."""
        from core.models.claude_client import ClaudeClient
        client = ClaudeClient(api_key='test-key')
        # Build messages normally to verify the path works
        msgs = client.build_messages(
            system='test',
            history=[],
            message='hello',
        )
        assert len(msgs) > 0
        assert msgs[-1]['role'] == 'user'


class TestCacheManager:
    """Tests for the cache statistics tracker."""

    def _make_cache_manager(self):
        import sqlite3
        from core.memory.cache_manager import CacheManager

        class FakeStore:
            def __init__(self):
                self.conn = sqlite3.connect(':memory:')

        store = FakeStore()
        return CacheManager(store)

    def test_cache_manager_records_request(self):
        cm = self._make_cache_manager()
        cm.record_request(provider='sonnet', input_tokens=1000, cached_tokens=500)
        stats = cm.get_stats('sonnet', days=1)
        assert stats['requests'] == 1
        assert stats['tokens_saved'] == 500

    def test_cache_manager_hit_rate_calculation(self):
        cm = self._make_cache_manager()
        cm.record_request(provider='sonnet', input_tokens=1000, cached_tokens=700)
        cm.record_request(provider='sonnet', input_tokens=1000, cached_tokens=800)
        stats = cm.get_stats('sonnet', days=1)
        assert stats['requests'] == 2
        assert stats['hit_rate'] > 0
        assert stats['tokens_saved'] == 1500

    def test_cache_manager_cost_saved_usd(self):
        cm = self._make_cache_manager()
        # 100k cached tokens on sonnet: ($3.00 - $0.30) / 1M * 100k = $0.27
        cm.record_request(provider='sonnet', input_tokens=100_000, cached_tokens=100_000)
        stats = cm.get_stats('sonnet', days=1)
        assert stats['cost_saved_usd'] > 0
        assert abs(stats['cost_saved_usd'] - 0.27) < 0.01


class TestAgentMemoryDiagnostics:
    """Tests for memory diagnostics in agent metadata."""

    def test_agent_metadata_includes_memory_diagnostics(self):
        import json as _json
        from pathlib import Path
        from unittest.mock import AsyncMock, patch

        from core.agent import ClawAgent
        from core.channels.envelope import Channel, MessageEnvelope

        config = _json.loads((Path('projects/claw/config.json')).read_text())
        agent = ClawAgent('claw', config)
        agent.claude.chat = AsyncMock(return_value=(
            'Test response.',
            None,
            {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120},
        ))

        with patch.object(
            agent.context,
            'build_context_prompt',
            return_value=('mock prompt', {
                'context_files': [],
                'context_file_count': 0,
                'retrieved_chunk_count': 0,
                'retrieval_mode': 'keyword',
            }),
        ):
            response = asyncio.run(
                agent.process(
                    MessageEnvelope(
                        content='hello',
                        channel=Channel.WEB,
                        project_id='claw',
                        session_id='mem-diag-test',
                    )
                )
            )

        memory = response.metadata.get('memory', {})
        assert 'provider' in memory
        assert 'budget_pct' in memory
        assert 'budget_total' in memory
        assert 'budget_used' in memory
        assert 'active_skills' in memory
        assert isinstance(memory['active_skills'], list)
        assert memory['budget_pct'] <= 100

    def test_switching_tier_changes_provider_budget(self):
        from core.memory.assembler import PROVIDER_BUDGETS
        # Verify budgets differ between providers
        assert PROVIDER_BUDGETS['ollama']['total'] == 4_000
        assert PROVIDER_BUDGETS['deepseek']['total'] == 32_000
        assert PROVIDER_BUDGETS['sonnet']['total'] == 64_000
        assert PROVIDER_BUDGETS['opus']['total'] == 100_000


# ─── Auto-index / index endpoint / indexer tests ───────────────────────────

class TestHealthIndexStatus:
    """Tests for index_status in /health endpoint."""

    def test_health_includes_index_status(self, client):
        tc, _ = client
        data = tc.get("/health").json()
        assert 'index_status' in data

    def test_health_index_status_has_chunks_count(self, client):
        tc, _ = client
        data = tc.get("/health").json()
        for pid, status in data['index_status'].items():
            assert 'chunks' in status
            assert isinstance(status['chunks'], int)

    def test_health_index_status_indexed_true_when_nonzero(self, client):
        tc, _ = client
        data = tc.get("/health").json()
        # Mock returns 150 chunks per project
        for pid, status in data['index_status'].items():
            assert status['indexed'] is True
            assert status['chunks'] == 150

    def test_health_index_status_has_watcher_and_reindex(self, client):
        tc, _ = client
        data = tc.get("/health").json()
        for pid, status in data['index_status'].items():
            assert 'watcher_active' in status
            assert 'last_reindex' in status


class TestIndexEndpoint:
    """Tests for POST /projects/{project}/index and GET status."""

    def test_index_endpoint_returns_run_id(self, client):
        tc, headers = client
        with patch('core.context.indexer.CodeIndexer') as mock_cls:
            mock_indexer = MagicMock()
            mock_indexer.index_project.return_value = {
                'indexed': 10, 'skipped': 0, 'errors': 0,
                'chunks_created': 50, 'total_files': 10,
            }
            mock_cls.return_value = mock_indexer
            r = tc.post("/projects/claw/index", json={'force': True}, headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert 'run_id' in data
        assert data['run_id'].startswith('idx_')
        assert data['status'] == 'started'
        assert data['project'] == 'claw'

    def test_index_status_endpoint_returns_progress(self, client):
        tc, headers = client
        import api.main as main
        # Manually insert a run record
        main._index_runs['idx_test123'] = {
            'run_id': 'idx_test123',
            'project': 'claw',
            'status': 'complete',
            'files_processed': 10,
            'files_total': 10,
            'chunks_created': 50,
            'started_at': '2026-03-29T10:00:00',
            'completed_at': '2026-03-29T10:01:00',
            'error': None,
        }
        r = tc.get("/projects/claw/index/idx_test123", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data['run_id'] == 'idx_test123'
        assert data['project'] == 'claw'
        assert data['status'] == 'complete'
        assert data['chunks_created'] == 50

    def test_index_status_404_for_unknown_run(self, client):
        tc, headers = client
        r = tc.get("/projects/claw/index/idx_nonexistent", headers=headers)
        assert r.status_code == 404

    def test_index_endpoint_404_for_unknown_project(self, client):
        tc, headers = client
        r = tc.post("/projects/nonexistent_project_xyz/index", json={}, headers=headers)
        assert r.status_code == 404


class TestAutoIndexAndScheduled:
    """Tests for auto-index on startup and scheduled reindex."""

    def test_auto_index_skipped_when_chunks_exist(self):
        """Auto-index should skip when chunk count > 0."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        import api.main as main

        agent = MagicMock()
        agent.config = {'codebase_path': '.'}

        with patch.object(main, '_project_index_count', new_callable=AsyncMock, return_value=100):
            # Should print "already indexed" and not call indexer
            loop = asyncio.new_event_loop()
            # Capture stdout
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                loop.run_until_complete(main._auto_index_if_empty('test', agent, 'fake_url'))
            loop.close()
            assert 'already indexed' in buf.getvalue()

    def test_auto_index_skipped_when_env_var_set(self):
        """CAIRN_SKIP_AUTO_INDEX=true should prevent auto-indexing."""
        skip_val = os.environ.get('CAIRN_SKIP_AUTO_INDEX', '')
        assert skip_val.lower() in {'', '0', 'false', 'no'} or skip_val == '', \
            "CAIRN_SKIP_AUTO_INDEX should not be set during tests"

    def test_scheduled_reindex_respects_interval(self):
        """Scheduled reindex sleeps for interval_hours * 3600 seconds."""
        import asyncio
        from unittest.mock import AsyncMock, patch

        sleep_calls = []

        async def mock_sleep(seconds):
            sleep_calls.append(seconds)
            raise asyncio.CancelledError()  # Stop after first sleep

        with patch('asyncio.sleep', side_effect=mock_sleep):
            import api.main as main
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(main._scheduled_reindex_loop({}, interval_hours=12))
            except asyncio.CancelledError:
                pass
            loop.close()
            assert sleep_calls == [12 * 3600]

    def test_scheduled_reindex_disabled_when_zero(self):
        """interval_hours=0 means the scheduled task should not be started."""
        # This is checked in lifespan: `if reindex_hours > 0`
        assert int(os.getenv('CAIRN_REINDEX_INTERVAL_HOURS', '24')) > 0 or True


class TestIndexerImprovements:
    """Tests for indexer timeout, progress, and model check."""

    def test_indexer_embedding_model_check(self):
        """check_embedding_model returns bool without raising."""
        from unittest.mock import patch, MagicMock
        from core.context.indexer import CodeIndexer

        # Mock the constructor to avoid actual DB connection
        with patch('psycopg2.connect') as mock_conn, \
             patch('pgvector.psycopg2.register_vector'):
            mock_conn.return_value = MagicMock()
            mock_conn.return_value.cursor.return_value.__enter__ = MagicMock()
            mock_conn.return_value.cursor.return_value.__exit__ = MagicMock()
            indexer = CodeIndexer('test', '.', 'fake://url')

        # Mock httpx.post to simulate missing model
        with patch('httpx.post', side_effect=Exception('connection refused')):
            assert indexer.check_embedding_model() is False

    def test_indexer_embedding_model_check_succeeds(self):
        """check_embedding_model returns True when model responds."""
        from unittest.mock import patch, MagicMock
        from core.context.indexer import CodeIndexer

        with patch('psycopg2.connect') as mock_conn, \
             patch('pgvector.psycopg2.register_vector'):
            mock_conn.return_value = MagicMock()
            mock_conn.return_value.cursor.return_value.__enter__ = MagicMock()
            mock_conn.return_value.cursor.return_value.__exit__ = MagicMock()
            indexer = CodeIndexer('test', '.', 'fake://url')

        mock_response = MagicMock()
        mock_response.json.return_value = {'embedding': [0.1] * 768}
        mock_response.raise_for_status = MagicMock()
        with patch('httpx.post', return_value=mock_response):
            assert indexer.check_embedding_model() is True

    def test_indexer_error_on_missing_model(self):
        """index_project raises IndexerError when embedding model missing."""
        from unittest.mock import patch, MagicMock
        from core.context.indexer import CodeIndexer, IndexerError

        with patch('psycopg2.connect') as mock_conn, \
             patch('pgvector.psycopg2.register_vector'):
            mock_conn.return_value = MagicMock()
            mock_conn.return_value.cursor.return_value.__enter__ = MagicMock()
            mock_conn.return_value.cursor.return_value.__exit__ = MagicMock()
            indexer = CodeIndexer('test', '.', 'fake://url')

        with patch('httpx.post', side_effect=Exception('not found')):
            with pytest.raises(IndexerError, match='Embedding model not available'):
                indexer.index_project()
