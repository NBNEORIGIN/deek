import os
import uuid
from .channels.envelope import MessageEnvelope, AgentResponse
from .context.engine import ContextEngine
from .models.router import route, estimate_tokens, ModelChoice
from .models.ollama_client import OllamaClient
from .models.claude_client import ClaudeClient
from .tools.registry import ToolRegistry, RiskLevel
from .tools.diff_tools import generate_unified_diff, generate_create_diff
from .memory.store import MemoryStore


class ClawAgent:
    """
    Core agent orchestrator.
    Receives normalised MessageEnvelope from any channel.
    Returns AgentResponse to any channel.
    All channel-specific logic lives in channel handlers.
    """

    def __init__(self, project_id: str, config: dict):
        self.project_id = project_id
        self.config = config

        self.context = ContextEngine(
            project_id=project_id,
            db_url=os.getenv('DATABASE_URL', ''),
        )
        self.memory = MemoryStore(
            project_id=project_id,
            data_dir=os.getenv('CLAW_DATA_DIR', './data'),
        )
        self.ollama = OllamaClient(
            base_url=os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434'),
            model=os.getenv('OLLAMA_MODEL', 'qwen2.5-coder:7b'),
        )
        self.claude = ClaudeClient(
            api_key=os.getenv('ANTHROPIC_API_KEY', ''),
        )
        self.tools = ToolRegistry()
        self._register_tools()

        # Cache project root for tool execution
        self._project_root = config.get('codebase_path', '.')

    async def process(self, envelope: MessageEnvelope) -> AgentResponse:
        """Main entry point. Process a message and return a response."""
        session_id = envelope.session_id

        self.memory.add_message(
            session_id=session_id,
            role='user',
            content=envelope.content,
            channel=envelope.channel.value,
        )

        # Handle tool approval response
        if envelope.tool_approval:
            return await self._handle_tool_approval(envelope)

        # Build context prompt
        context_prompt = self.context.build_context_prompt(
            task=envelope.content,
            embedding_fn=self._embed,
        )

        # Append active file from VS Code if provided
        if envelope.active_file:
            try:
                file_content = self.context.load_tier3(envelope.active_file)
                context_prompt += (
                    f"\n\n# CURRENTLY OPEN FILE\n"
                    f"## {envelope.active_file}\n"
                    f"```\n{file_content}\n```\n"
                )
            except (FileNotFoundError, PermissionError):
                pass

        if envelope.selected_text:
            context_prompt += (
                f"\n\n# SELECTED CODE\n"
                f"```\n{envelope.selected_text}\n```\n"
            )

        history = self.memory.get_recent_history(session_id, limit=10)

        context_tokens = estimate_tokens(context_prompt + envelope.content)
        model_choice = route(
            task=envelope.content,
            context_tokens=context_tokens,
            project_config=self.config,
        )

        available_tools = self.tools.describe_for_model(self.config)

        if model_choice == ModelChoice.LOCAL:
            response_text, tool_call, usage = await self.ollama.chat(
                system=context_prompt,
                history=history,
                message=envelope.content,
                tools=available_tools,
            )
            model_used = os.getenv('OLLAMA_MODEL', 'qwen2.5-coder:7b')
            cost_usd = 0.0
        else:
            response_text, tool_call, usage = await self.claude.chat(
                system=context_prompt,
                history=history,
                message=envelope.content,
                tools=available_tools,
            )
            model_used = 'claude-sonnet-4-5'
            cost_usd = self._calculate_cost(usage)

        self.memory.add_message(
            session_id=session_id,
            role='assistant',
            content=response_text or '',
            channel=envelope.channel.value,
            model_used=model_used,
            tokens_used=usage.get('total_tokens', 0),
            cost_usd=cost_usd,
        )

        pending_tool_call = None
        if tool_call:
            tool = self.tools.get(tool_call['name'])
            if tool:
                diff_preview = self._generate_diff_preview(tool_call)

                pending_tool_call = {
                    'tool_call_id': str(uuid.uuid4()),
                    'tool_name': tool_call['name'],
                    'description': self._describe_tool_call(tool_call),
                    'diff_preview': diff_preview,
                    'input': tool_call['input'],
                    'risk_level': tool.risk_level.value,
                    'auto_approve': tool.risk_level == RiskLevel.SAFE,
                }

                # Safe tools (read-only) execute immediately without approval
                if pending_tool_call['auto_approve']:
                    result = await self._execute_tool(tool_call)
                    return await self._continue_with_tool_result(
                        envelope, context_prompt, history,
                        tool_call, result, model_choice,
                        model_used, cost_usd,
                    )

        return AgentResponse(
            content=response_text or '',
            session_id=session_id,
            project_id=self.project_id,
            pending_tool_call=pending_tool_call,
            model_used=model_used,
            tokens_used=usage.get('total_tokens', 0),
            cost_usd=cost_usd,
        )

    async def _handle_tool_approval(
        self, envelope: MessageEnvelope
    ) -> AgentResponse:
        """
        Process the user's approve/reject decision for a pending tool call.
        If approved, execute the tool and feed the result back to the model.
        """
        approval = envelope.tool_approval
        if not approval.get('approved'):
            self.memory.add_message(
                session_id=envelope.session_id,
                role='assistant',
                content='Tool call rejected by user.',
                channel=envelope.channel.value,
            )
            return AgentResponse(
                content='Understood — change discarded.',
                session_id=envelope.session_id,
                project_id=self.project_id,
            )

        # Re-execute the approved tool from stored input
        tool_input = approval.get('modified_input') or approval.get('input', {})
        tool_name = approval.get('tool_name', '')
        tool_call = {'name': tool_name, 'input': tool_input}
        result = await self._execute_tool(tool_call)

        # Record the edit in memory
        if tool_name in ('edit_file', 'create_file'):
            self.memory.record_file_edit(
                session_id=envelope.session_id,
                file_path=tool_input.get('file_path', ''),
                edit_type=tool_name,
                reason=tool_input.get('reason', ''),
                approved_by='user',
            )

        # Brief confirmation back to user
        return AgentResponse(
            content=f"Done. {result}",
            session_id=envelope.session_id,
            project_id=self.project_id,
        )

    async def _continue_with_tool_result(
        self,
        envelope: MessageEnvelope,
        context_prompt: str,
        history: list[dict],
        tool_call: dict,
        result: str,
        model_choice: ModelChoice,
        model_used: str,
        prior_cost: float,
    ) -> AgentResponse:
        """
        Feed a tool result back to the model for a final response.
        Used for auto-approved (safe) tools like read_file and search_code.
        """
        tool_result_message = (
            f"Tool result for {tool_call['name']}:\n{result}"
        )

        if model_choice == ModelChoice.LOCAL:
            response_text, _, usage = await self.ollama.chat(
                system=context_prompt,
                history=history,
                message=f"{envelope.content}\n\n{tool_result_message}",
                tools=None,
            )
            cost_usd = 0.0
        else:
            response_text, _, usage = await self.claude.chat(
                system=context_prompt,
                history=history,
                message=f"{envelope.content}\n\n{tool_result_message}",
                tools=None,
            )
            cost_usd = self._calculate_cost(usage)

        self.memory.add_message(
            session_id=envelope.session_id,
            role='assistant',
            content=response_text or '',
            channel=envelope.channel.value,
            model_used=model_used,
            tokens_used=usage.get('total_tokens', 0),
            cost_usd=cost_usd,
        )

        return AgentResponse(
            content=response_text or '',
            session_id=envelope.session_id,
            project_id=self.project_id,
            model_used=model_used,
            cost_usd=prior_cost + cost_usd,
        )

    async def _execute_tool(self, tool_call: dict) -> str:
        """Execute a tool synchronously and return the result string."""
        tool = self.tools.get(tool_call['name'])
        if not tool:
            return f"ERROR: Unknown tool: {tool_call['name']}"

        inp = tool_call.get('input', {})
        try:
            # All tool functions take project_root as first positional arg
            return tool.fn(self._project_root, **inp)
        except Exception as e:
            return f"ERROR: Tool execution failed: {e}"

    def _generate_diff_preview(self, tool_call: dict) -> str:
        name = tool_call['name']
        inp = tool_call.get('input', {})
        if name == 'edit_file':
            return generate_unified_diff(
                inp.get('file_path', ''),
                inp.get('old_str', ''),
                inp.get('new_str', ''),
            )
        if name == 'create_file':
            return generate_create_diff(
                inp.get('file_path', ''),
                inp.get('content', ''),
            )
        return ''

    def _describe_tool_call(self, tool_call: dict) -> str:
        name = tool_call['name']
        inp = tool_call.get('input', {})
        descriptions = {
            'read_file': f"Read file: {inp.get('file_path')}",
            'edit_file': (
                f"Edit {inp.get('file_path')}: "
                f"{inp.get('reason', 'no reason given')}"
            ),
            'create_file': f"Create file: {inp.get('file_path')}",
            'search_code': f"Search codebase: {inp.get('query')}",
            'run_tests': f"Run tests: {inp.get('test_path', 'all')}",
            'run_command': f"Run command: {inp.get('command')}",
            'run_migration': (
                f"Run migration: {inp.get('app_name', 'all')}"
            ),
        }
        return descriptions.get(name, f"Call tool: {name}")

    def _embed(self, text: str) -> list[float]:
        """Embedding function for Tier 2 context retrieval."""
        import httpx
        response = httpx.post(
            f"{os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')}"
            f"/api/embeddings",
            json={'model': 'nomic-embed-text', 'prompt': text},
            timeout=30,
        )
        return response.json()['embedding']

    def _calculate_cost(self, usage: dict) -> float:
        """Estimate Claude API cost from token usage."""
        # Claude Sonnet pricing: $3/M input, $15/M output
        return (
            usage.get('input_tokens', 0) * 3
            + usage.get('output_tokens', 0) * 15
        ) / 1_000_000

    def _register_tools(self):
        from .tools.file_tools import (
            read_file_tool, edit_file_tool, create_file_tool,
        )
        from .tools.search_tools import search_code_tool
        from .tools.exec_tools import (
            run_tests_tool, run_command_tool, run_migration_tool,
        )
        for tool in [
            read_file_tool, edit_file_tool, create_file_tool,
            search_code_tool, run_tests_tool,
            run_command_tool, run_migration_tool,
        ]:
            self.tools.register(tool)
