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
import json
import os
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
         patch("core.context.engine.ContextEngine.build_context_prompt", return_value="mock system prompt"):
        mock_chat.return_value = fake_response
        from api.main import app
        yield TestClient(app), auth_headers


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

    def test_check3_hallucinated_file_fails(self):
        from core.models.output_validator import validate
        r = validate("See core/nonexistent_module.py for details.",
                     files_in_context=[], project_root='D:/claw')
        assert any('CHECK 3' in f for f in r.failures)

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
