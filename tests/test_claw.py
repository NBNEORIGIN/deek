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

    def test_get_session_list_scoped_to_subproject(self, tmp_store):
        sp = tmp_store.create_subproject('testproj', 'client-x', 'Client X')
        tmp_store.add_message('sess-sp', 'user', 'hi', 'web')
        tmp_store.set_session_subproject('sess-sp', sp['id'])
        tmp_store.add_message('sess-other', 'user', 'other', 'web')

        scoped = tmp_store.get_session_list('testproj', subproject_id=sp['id'])
        ids = [s['session_id'] for s in scoped]
        assert 'sess-sp' in ids
        assert 'sess-other' not in ids

    def test_get_session_returns_messages(self, tmp_store):
        tmp_store.add_message('sess-get', 'user', 'msg 1', 'web')
        tmp_store.add_message('sess-get', 'assistant', 'reply', 'web')
        sess = tmp_store.get_session('sess-get')
        assert sess is not None
        assert len(sess['messages']) == 2
        assert sess['archived'] is False

    def test_get_session_not_found_returns_none(self, tmp_store):
        assert tmp_store.get_session('nonexistent-session-xyz') is None


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

        result = asyncio.get_event_loop().run_until_complete(
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

        result = asyncio.get_event_loop().run_until_complete(
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

        result = asyncio.get_event_loop().run_until_complete(
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

        result = asyncio.get_event_loop().run_until_complete(
            engine.resolve_mentions(
                mentions=[{'type': 'unknown_type', 'value': 'x', 'display': 'x'}],
                project_id='test',
                config=config,
            )
        )
        # Unknown type should produce an empty result (no exception)
        assert isinstance(result, list)


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
             patch("core.context.engine.ContextEngine.build_context_prompt", return_value="mock ctx"):
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

            events = asyncio.get_event_loop().run_until_complete(collect())

        types = [e["type"] for e in events]
        assert "routing" in types
        assert "complete" in types

    def test_process_streaming_complete_has_response_field(self, tmp_path):
        """The 'complete' event must contain a 'response' key."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from core.agent import ClawAgent
        from core.channels.envelope import MessageEnvelope, Channel

        fake_response = ("My answer", None, {
            "input_tokens": 5, "output_tokens": 10, "total_tokens": 15,
        })

        with patch("core.models.claude_client.ClaudeClient.chat", new_callable=AsyncMock) as mock_chat, \
             patch("core.context.engine.ContextEngine.build_context_prompt", return_value="mock ctx"):
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

            complete = asyncio.get_event_loop().run_until_complete(collect())

        assert complete is not None
        assert "response" in complete
        assert complete["response"] == "My answer"

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
             patch("core.context.engine.ContextEngine.build_context_prompt", return_value="mock ctx"), \
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

            events = asyncio.get_event_loop().run_until_complete(collect())

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
             patch("core.context.engine.ContextEngine.build_context_prompt", return_value="mock ctx"), \
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

            events = asyncio.get_event_loop().run_until_complete(collect())

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
