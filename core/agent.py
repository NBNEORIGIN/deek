import logging
import os
import time
import uuid
from typing import AsyncGenerator
from .channels.envelope import MessageEnvelope, AgentResponse
from .context.engine import ContextEngine
from .models.router import route, estimate_tokens, ModelChoice, _deepseek_available
from .models.ollama_client import OllamaClient
from .models.claude_client import ClaudeClient
from .models.openai_client import OpenAIClient
from .models.deepseek_client import DeepSeekClient
from .tools.registry import ToolRegistry, RiskLevel
from .tools.diff_tools import generate_unified_diff, generate_create_diff
from .memory.store import MemoryStore
from .memory.summariser import SessionSummariser

logger = logging.getLogger(__name__)


class ClawAgent:
    """
    Core agent orchestrator.
    Receives normalised MessageEnvelope from any channel.
    Returns AgentResponse to any channel.
    All channel-specific logic lives in channel handlers.

    Tool loop: SAFE tools (read_file, search_code, etc.) are executed
    automatically in a loop of up to MAX_TOOL_ROUNDS, letting the model
    read files, search code, and reason across multiple sources before
    giving a final answer — similar to how Cursor/Claude.ai work.
    REVIEW/DESTRUCTIVE tools still pause for user approval as before.
    """

    MAX_TOOL_ROUNDS = 12  # Max agentic tool-call iterations per request

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
        # Store preferred/fallback for async resolution at first use
        self._ollama_preferred = os.getenv('OLLAMA_MODEL_PREFERRED', 'deepseek-coder-v2:16b')
        self._ollama_fallback = os.getenv('OLLAMA_MODEL', 'qwen2.5-coder:7b')
        self.claude = ClaudeClient(
            api_key=os.getenv('ANTHROPIC_API_KEY', ''),
        )
        openai_key = os.getenv('OPENAI_API_KEY', '')
        self.openai = OpenAIClient(api_key=openai_key) if openai_key else None
        deepseek_key = os.getenv('DEEPSEEK_API_KEY', '')
        self.deepseek = DeepSeekClient(api_key=deepseek_key) if deepseek_key else None
        # API_PROVIDER = 'claude' | 'openai' | 'deepseek' | 'auto'
        # auto: use tier routing; fall back on rate-limit errors
        self._api_provider = os.getenv('API_PROVIDER', 'auto').lower()
        self.tools = ToolRegistry()
        self._register_tools()
        self.summariser = SessionSummariser(project_id=project_id)

        # Cache project root for tool execution
        self._project_root = config.get('codebase_path', '.')

    async def process(self, envelope: MessageEnvelope) -> AgentResponse:
        """Main entry point. Process a message and return a response."""
        session_id = envelope.session_id

        # Bind subproject to this session on first set
        if envelope.subproject_id:
            self.memory.set_session_subproject(session_id, envelope.subproject_id)

        self.memory.add_message(
            session_id=session_id,
            role='user',
            content=envelope.content,
            channel=envelope.channel.value,
        )

        # Handle tool approval response
        if envelope.tool_approval:
            return await self._handle_tool_approval(envelope)

        # Resolve @ mentions before building context prompt
        resolved_mentions: list[dict] = []
        if envelope.mentions:
            try:
                resolved_mentions = await self.context.resolve_mentions(
                    mentions=envelope.mentions,
                    project_id=self.project_id,
                    config=self.config,
                )
            except Exception as exc:
                logger.warning(f"[CLAW] mention resolution failed: {exc}")

        # Build context prompt (scoped to subproject when set)
        context_prompt = self.context.build_context_prompt(
            task=envelope.content,
            embedding_fn=self._embed,
            subproject_id=envelope.subproject_id,
            resolved_mentions=resolved_mentions or None,
        )

        # System instruction prepended to every prompt
        context_prompt = (
            "You are CLAW, a sovereign AI coding agent.\n"
            "Rules:\n"
            "1. Use your provided tools to answer requests — do not describe "
            "what you would do, just do it.\n"
            "2. NEVER invent or hallucinate file contents, tool results, or "
            "command output. If a tool returns an error, report it exactly.\n"
            "3. Tool parameter names are exact — use the schema. "
            "read_file takes 'file_path', edit_file takes 'file_path'/'old_str'/"
            "'new_str'. Do not use 'path', 'parameters', or other variants.\n"
            "4. If you are unsure what a file contains, call read_file to find "
            "out — do not guess.\n\n"
        ) + context_prompt

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
        # read_only passes are assessment/plan — treat as SAFE for routing
        routing_risk = 'safe' if envelope.read_only else 'review'

        # Map per-message model_override to a force_tier value
        _OVERRIDE_MAP = {
            'auto':     None,
            'local':    1,
            'deepseek': 2,
            'sonnet':   3,
            'opus':     4,
        }
        force_tier = _OVERRIDE_MAP.get(envelope.model_override or '', None)

        model_choice = route(
            task=envelope.content,
            context_tokens=context_tokens,
            project_config=self.config,
            risk_level=routing_risk,
            force_tier=force_tier,
        )

        # Track whether this routing was manually overridden for the response
        _model_was_manual = envelope.model_override not in (None, 'auto', '')

        available_tools = self._get_tools_for_task(
            envelope.content, read_only=envelope.read_only
        )



        if model_choice == ModelChoice.LOCAL:
            # Resolve preferred vs fallback model on first local call
            await self.ollama.resolve_active_model(
                self._ollama_preferred, self._ollama_fallback
            )
            response_text, tool_call, usage = await self.ollama.chat(
                system=context_prompt,
                history=history,
                message=envelope.content,
                tools=available_tools,
            )
            model_used = self.ollama.model
            cost_usd = 0.0
            client = None
        else:
            use_opus = self._should_use_opus(envelope.content)
            client, model_used = await self._get_api_client(
                use_opus=use_opus,
                prefer_deepseek=(model_choice == ModelChoice.DEEPSEEK),
            )
            response_text, tool_call, usage = await client.chat(
                system=context_prompt,
                history=history,
                message=envelope.content,
                tools=available_tools,
                use_opus=use_opus,
                image_base64=envelope.image_base64,
                image_media_type=envelope.image_media_type,
            )
            cost_usd = self._calculate_cost(usage, client)


        # --- Tool handling ---
        if tool_call:
            tool = self.tools.get(tool_call['name'])
            if tool and tool.risk_level == RiskLevel.SAFE:
                # SAFE tool: enter the multi-round tool loop
                return await self._run_tool_loop(
                    envelope=envelope,
                    context_prompt=context_prompt,
                    history=history,
                    first_tool_call=tool_call,
                    first_response_text=response_text,
                    model_choice=model_choice,
                    model_used=model_used,
                    prior_cost=cost_usd,
                    client=client if model_choice != ModelChoice.LOCAL else None,
                    available_tools=available_tools,
                    model_was_manual=_model_was_manual,
                )
            elif tool:
                # REVIEW / DESTRUCTIVE tool: surface for approval (unchanged)
                self.memory.add_message(
                    session_id=session_id,
                    role='assistant',
                    content=response_text or '',
                    channel=envelope.channel.value,
                    model_used=model_used,
                    tokens_used=usage.get('total_tokens', 0),
                    cost_usd=cost_usd,
                )
                pending_tool_call = {
                    'tool_call_id': str(uuid.uuid4()),
                    'tool_name': tool_call['name'],
                    'description': self._describe_tool_call(tool_call),
                    'diff_preview': self._generate_diff_preview(tool_call),
                    'input': tool_call['input'],
                    'risk_level': tool.risk_level.value,
                    'auto_approve': False,
                }
                return AgentResponse(
                    content=response_text or '',
                    session_id=session_id,
                    project_id=self.project_id,
                    pending_tool_call=pending_tool_call,
                    model_used=model_used,
                    tokens_used=usage.get('total_tokens', 0),
                    cost_usd=cost_usd,
                )

        # No tool call — plain text response
        self.memory.add_message(
            session_id=session_id,
            role='assistant',
            content=response_text or '',
            channel=envelope.channel.value,
            model_used=model_used,
            tokens_used=usage.get('total_tokens', 0),
            cost_usd=cost_usd,
        )

        metadata = await self._check_trim_archive(session_id)
        metadata['model_routing'] = 'manual' if _model_was_manual else 'auto'

        return AgentResponse(
            content=response_text or '',
            session_id=session_id,
            project_id=self.project_id,
            model_used=model_used,
            tokens_used=usage.get('total_tokens', 0),
            cost_usd=cost_usd,
            metadata=metadata,
        )

    async def process_streaming(
        self,
        envelope: MessageEnvelope,
    ) -> AsyncGenerator[dict, None]:
        """
        Async generator version of process() for the SSE /chat/stream endpoint.

        Yields SSE event dicts as the agent works:
          routing       — model tier selected
          tokens        — context token estimate
          tool_start    — SAFE tool about to execute
          tool_end      — SAFE tool finished (duration + chars)
          tool_queued   — REVIEW/DESTRUCTIVE tool encountered (not executed)
          complete      — final response ready (mirrors AgentResponse fields)
          error         — unhandled exception

        REVIEW tool approvals remain on the POST /chat path.
        The streaming endpoint executes SAFE tools and stops at REVIEW tools.
        """
        session_id = envelope.session_id

        try:
            # ── Memory ───────────────────────────────────────────────────────
            if envelope.subproject_id:
                self.memory.set_session_subproject(session_id, envelope.subproject_id)
            self.memory.add_message(
                session_id=session_id,
                role='user',
                content=envelope.content,
                channel=envelope.channel.value,
            )

            # Tool approvals are not streamed — delegate and yield complete
            if envelope.tool_approval:
                response = await self._handle_tool_approval(envelope)
                yield {
                    'type': 'complete',
                    'response': response.content,
                    'cost_usd': response.cost_usd,
                    'model_used': response.model_used,
                    'metadata': response.metadata,
                    'executed_tool_calls': response.executed_tool_calls,
                    'pending_tool_call': response.pending_tool_call,
                }
                return

            # ── @ mention resolution ─────────────────────────────────────────
            resolved_mentions: list[dict] = []
            if envelope.mentions:
                try:
                    resolved_mentions = await self.context.resolve_mentions(
                        mentions=envelope.mentions,
                        project_id=self.project_id,
                        config=self.config,
                    )
                except Exception as exc:
                    logger.warning(f'[stream] mention resolution failed: {exc}')

            # ── Context ───────────────────────────────────────────────────────
            context_prompt = self.context.build_context_prompt(
                task=envelope.content,
                embedding_fn=self._embed,
                subproject_id=envelope.subproject_id,
                resolved_mentions=resolved_mentions or None,
            )
            context_prompt = (
                "You are CLAW, a sovereign AI coding agent.\n"
                "Rules:\n"
                "1. Use your provided tools to answer requests — do not describe "
                "what you would do, just do it.\n"
                "2. NEVER invent or hallucinate file contents, tool results, or "
                "command output. If a tool returns an error, report it exactly.\n"
                "3. Tool parameter names are exact — use the schema. "
                "read_file takes 'file_path', edit_file takes 'file_path'/'old_str'/"
                "'new_str'. Do not use 'path', 'parameters', or other variants.\n"
                "4. If you are unsure what a file contains, call read_file to find "
                "out — do not guess.\n\n"
            ) + context_prompt

            if envelope.active_file:
                try:
                    fc = self.context.load_tier3(envelope.active_file)
                    context_prompt += (
                        f'\n\n# CURRENTLY OPEN FILE\n## {envelope.active_file}\n'
                        f'```\n{fc}\n```\n'
                    )
                except (FileNotFoundError, PermissionError):
                    pass

            if envelope.selected_text:
                context_prompt += f'\n\n# SELECTED CODE\n```\n{envelope.selected_text}\n```\n'

            history = self.memory.get_recent_history(session_id, limit=10)
            context_tokens = estimate_tokens(context_prompt + envelope.content)

            # ── Routing ───────────────────────────────────────────────────────
            routing_risk = 'safe' if envelope.read_only else 'review'
            _OVERRIDE_MAP = {'auto': None, 'local': 1, 'deepseek': 2, 'sonnet': 3, 'opus': 4}
            force_tier = _OVERRIDE_MAP.get(envelope.model_override or '', None)
            model_choice = route(
                task=envelope.content,
                context_tokens=context_tokens,
                project_config=self.config,
                risk_level=routing_risk,
                force_tier=force_tier,
            )
            _model_was_manual = envelope.model_override not in (None, 'auto', '')
            _tier_map = {ModelChoice.LOCAL: 1, ModelChoice.DEEPSEEK: 2, ModelChoice.API: 3}

            yield {
                'type': 'routing',
                'tier': _tier_map.get(model_choice, 3),
                'manual': _model_was_manual,
            }
            yield {'type': 'tokens', 'estimated': context_tokens, 'limit': 40_000}

            available_tools = self._get_tools_for_task(
                envelope.content, read_only=envelope.read_only
            )

            # ── First model call ──────────────────────────────────────────────
            if model_choice == ModelChoice.LOCAL:
                await self.ollama.resolve_active_model(
                    self._ollama_preferred, self._ollama_fallback
                )
                response_text, tool_call, usage = await self.ollama.chat(
                    system=context_prompt,
                    history=history,
                    message=envelope.content,
                    tools=available_tools,
                )
                model_used = self.ollama.model
                total_cost = 0.0
                client = None
            else:
                use_opus = self._should_use_opus(envelope.content)
                client, model_used = await self._get_api_client(
                    use_opus=use_opus,
                    prefer_deepseek=(model_choice == ModelChoice.DEEPSEEK),
                )
                response_text, tool_call, usage = await client.chat(
                    system=context_prompt,
                    history=history,
                    message=envelope.content,
                    tools=available_tools,
                    use_opus=use_opus,
                    image_base64=envelope.image_base64,
                    image_media_type=envelope.image_media_type,
                )
                total_cost = self._calculate_cost(usage, client)

            yield {
                'type': 'routing',
                'tier': _tier_map.get(model_choice, 3),
                'model': model_used,
                'manual': _model_was_manual,
            }

            executed_tool_calls: list[dict] = []
            _seen_read_paths: set[str] = set()
            max_rounds = getattr(envelope, 'max_tool_rounds', None) or self.MAX_TOOL_ROUNDS

            raw_messages = (
                client.build_messages(
                    context_prompt, history, envelope.content,
                    getattr(envelope, 'image_base64', None),
                    getattr(envelope, 'image_media_type', 'image/png'),
                )
                if client else []
            )

            current_text = response_text
            current_tool_call = tool_call

            # ── Streaming tool loop ───────────────────────────────────────────
            for round_num in range(max_rounds):
                if not current_tool_call:
                    break

                tool_name = current_tool_call['name']
                tool = self.tools.get(tool_name)

                if not tool:
                    break

                auto_approve_review = getattr(envelope, 'auto_approve_review', False)
                if (
                    tool.risk_level != RiskLevel.SAFE
                    and not (auto_approve_review and tool.risk_level == RiskLevel.REVIEW)
                ):
                    # REVIEW/DESTRUCTIVE — emit queued event, stop loop
                    yield {
                        'type': 'tool_queued',
                        'tool': tool_name,
                        'params': current_tool_call['input'],
                        'diff': self._generate_diff_preview(current_tool_call),
                        'risk': tool.risk_level.value,
                    }
                    # Save current text and surface pending_tool_call
                    self.memory.add_message(
                        session_id=session_id,
                        role='assistant',
                        content=current_text or '',
                        channel=envelope.channel.value,
                        model_used=model_used,
                        tokens_used=0,
                        cost_usd=total_cost,
                    )
                    pending = {
                        'tool_call_id': str(uuid.uuid4()),
                        'tool_name': tool_name,
                        'description': self._describe_tool_call(current_tool_call),
                        'diff_preview': self._generate_diff_preview(current_tool_call),
                        'input': current_tool_call['input'],
                        'risk_level': tool.risk_level.value,
                        'auto_approve': False,
                    }
                    yield {
                        'type': 'complete',
                        'response': current_text or '',
                        'cost_usd': total_cost,
                        'model_used': model_used,
                        'metadata': {'model_routing': 'manual' if _model_was_manual else 'auto'},
                        'executed_tool_calls': executed_tool_calls,
                        'pending_tool_call': pending,
                    }
                    return

                # SAFE tool — emit start, execute, emit end
                yield {
                    'type': 'tool_start',
                    'tool': tool_name,
                    'params': current_tool_call['input'],
                    'risk': 'safe',
                }
                t0 = time.time()

                # Dedup read_file
                fp = current_tool_call.get('input', {}).get('file_path', '')
                if tool_name == 'read_file' and fp in _seen_read_paths:
                    result = f'[already read {fp} — contents available above]'
                else:
                    if tool_name == 'read_file' and fp:
                        _seen_read_paths.add(fp)
                    try:
                        result = await self._execute_tool(current_tool_call)
                    except Exception as exc:
                        result = f'Tool error: {exc}'

                duration_ms = int((time.time() - t0) * 1000)
                yield {
                    'type': 'tool_end',
                    'tool': tool_name,
                    'duration_ms': duration_ms,
                    'result_chars': len(result),
                }
                executed_tool_calls.append({'tool_name': tool_name, 'result': result})

                if client:
                    raw_messages = client.append_tool_round(
                        raw_messages, current_text, current_tool_call, result
                    )

                # Next model call
                force_final = (round_num == max_rounds - 1)
                if client:
                    next_text, next_tool_call, cont_usage = await client.chat(
                        system=context_prompt,
                        history=history,
                        message=envelope.content,
                        tools=available_tools if not force_final else None,
                        use_opus=self._should_use_opus(envelope.content),
                        raw_messages=raw_messages,
                    )
                    total_cost += self._calculate_cost(cont_usage, client)
                else:
                    next_text, next_tool_call, _ = await self.ollama.chat(
                        system=context_prompt,
                        history=history,
                        message=f"{envelope.content}\n\nTool result ({tool_name}):\n{result}",
                        tools=available_tools if not force_final else None,
                    )

                current_text = next_text
                current_tool_call = next_tool_call

            # ── Synthesis fallback ────────────────────────────────────────────
            if not (current_text or '').strip():
                logger.info('[stream] tool loop produced empty response — synthesising')
                synthesis_msg = (
                    f'Based on the information you just retrieved, please answer '
                    f'the original question: {envelope.content}'
                )
                if client:
                    synth_text, _, synth_usage = await client.chat(
                        system=context_prompt,
                        history=history,
                        message=synthesis_msg,
                        tools=None,
                        use_opus=self._should_use_opus(envelope.content),
                        raw_messages=raw_messages,
                    )
                    total_cost += self._calculate_cost(synth_usage, client)
                    current_text = synth_text

            # ── Save to memory ────────────────────────────────────────────────
            self.memory.add_message(
                session_id=session_id,
                role='assistant',
                content=current_text or '',
                channel=envelope.channel.value,
                model_used=model_used,
                tokens_used=0,
                cost_usd=total_cost,
            )
            metadata = await self._check_trim_archive(session_id)
            metadata['model_routing'] = 'manual' if _model_was_manual else 'auto'

            yield {
                'type': 'complete',
                'response': current_text or '',
                'cost_usd': total_cost,
                'model_used': model_used,
                'metadata': metadata,
                'executed_tool_calls': executed_tool_calls,
                'pending_tool_call': None,
            }

        except Exception as exc:
            logger.exception(f'[stream] unhandled error: {exc}')
            yield {'type': 'error', 'message': str(exc)}

    async def _run_tool_loop(
        self,
        envelope: MessageEnvelope,
        context_prompt: str,
        history: list[dict],
        first_tool_call: dict,
        first_response_text: str,
        model_choice: ModelChoice,
        model_used: str,
        prior_cost: float,
        client,
        available_tools: list[dict],
        model_was_manual: bool = False,
    ) -> AgentResponse:
        """
        Agentic tool loop for SAFE tools.

        Executes read_file / search_code / etc. repeatedly — up to
        MAX_TOOL_ROUNDS — feeding each result back to the model in the
        correct native format (Anthropic tool_result / OpenAI tool role).

        Stops when:
          - The model gives a plain text answer (no more tool calls)
          - A REVIEW/DESTRUCTIVE tool is requested → surface for approval
          - MAX_TOOL_ROUNDS is reached → force a final text answer
        """
        session_id = envelope.session_id
        executed_tool_calls: list[dict] = []
        total_cost = prior_cost
        # Track read_file calls to skip duplicates and free up rounds
        _seen_read_paths: set[str] = set()

        # Build the initial messages list once, then extend it each round
        raw_messages = client.build_messages(
            context_prompt,
            history,
            envelope.content,
            getattr(envelope, 'image_base64', None),
            getattr(envelope, 'image_media_type', 'image/png'),
        )

        current_tool_call = first_tool_call
        current_text = first_response_text

        # Honour per-request round cap (used by WiggumOrchestrator assess/plan)
        max_rounds = getattr(envelope, 'max_tool_rounds', None) or self.MAX_TOOL_ROUNDS

        for round_num in range(max_rounds):
            tool_name = current_tool_call['name']
            tool = self.tools.get(tool_name)

            # Unknown tool — abort
            if not tool:
                break

            # Non-safe tool reached mid-loop — surface for approval unless
            # auto_approve_review is set (WIGGUM execute passes) and the
            # tool is only REVIEW (never auto-approve DESTRUCTIVE).
            auto_approve_review = getattr(envelope, 'auto_approve_review', False)
            if (
                tool.risk_level != RiskLevel.SAFE
                and not (auto_approve_review and tool.risk_level == RiskLevel.REVIEW)
            ):
                self.memory.add_message(
                    session_id=session_id, role='assistant',
                    content=current_text or '',
                    channel=envelope.channel.value,
                    model_used=model_used,
                    tokens_used=0, cost_usd=total_cost,
                )
                pending = {
                    'tool_call_id': str(uuid.uuid4()),
                    'tool_name': tool_name,
                    'description': self._describe_tool_call(current_tool_call),
                    'diff_preview': self._generate_diff_preview(current_tool_call),
                    'input': current_tool_call['input'],
                    'risk_level': tool.risk_level.value,
                    'auto_approve': False,
                }
                return AgentResponse(
                    content=current_text or '',
                    session_id=session_id,
                    project_id=self.project_id,
                    pending_tool_call=pending,
                    model_used=model_used,
                    cost_usd=total_cost,
                    executed_tool_calls=executed_tool_calls,
                )

            # Skip re-reading a file already read this session
            if tool_name == 'read_file':
                fp = current_tool_call.get('input', {}).get('file_path', '')
                if fp in _seen_read_paths:
                    result = f"[already read {fp} — contents available above]"
                else:
                    result = await self._execute_tool(current_tool_call)
                    if fp:
                        _seen_read_paths.add(fp)
            else:
                result = await self._execute_tool(current_tool_call)

            executed_tool_calls.append({
                'tool_name': tool_name,
                'result': result,
            })

            # Extend the messages list with this tool round
            raw_messages = client.append_tool_round(
                raw_messages, current_text, current_tool_call, result
            )

            # On the last allowed round, force a text answer (no tools)
            force_final = (round_num == max_rounds - 1)
            next_text, next_tool_call, cont_usage = await client.chat(
                system=context_prompt,
                history=history,
                message=envelope.content,
                tools=available_tools if not force_final else None,
                use_opus=self._should_use_opus(envelope.content),
                raw_messages=raw_messages,
            )
            total_cost += self._calculate_cost(cont_usage, client)

            current_text = next_text
            current_tool_call = next_tool_call  # type: ignore[assignment]

            if not current_tool_call:
                break  # Model gave a final answer

        # If the loop exited without a text answer (model returned empty on
        # force_final, or returned tool markup as text), make one more call
        # explicitly asking for a synthesis. This is the safety net.
        if not (current_text or '').strip():
            logger.info(
                f"[CLAW] tool loop produced empty response — synthesising answer"
            )
            synthesis_msg = (
                f"Based on the information you just retrieved, please answer "
                f"the original question: {envelope.content}"
            )
            synth_text, _, synth_usage = await client.chat(
                system=context_prompt,
                history=history,
                message=synthesis_msg,
                tools=None,
                use_opus=self._should_use_opus(envelope.content),
                raw_messages=raw_messages,
            )
            total_cost += self._calculate_cost(synth_usage, client)
            current_text = synth_text

        # Save the final answer to memory once
        self.memory.add_message(
            session_id=session_id,
            role='assistant',
            content=current_text or '',
            channel=envelope.channel.value,
            model_used=model_used,
            tokens_used=0,
            cost_usd=total_cost,
        )

        metadata = await self._check_trim_archive(session_id)
        metadata['model_routing'] = 'manual' if model_was_manual else 'auto'

        return AgentResponse(
            content=current_text or '',
            session_id=session_id,
            project_id=self.project_id,
            model_used=model_used,
            cost_usd=total_cost,
            executed_tool_calls=executed_tool_calls,
            metadata=metadata,
        )

    async def _check_trim_archive(self, session_id: str) -> dict:
        """
        After each response, check if the session needs trimming or archiving.
        Returns metadata dict to be included in the AgentResponse.
        """
        meta: dict = {}
        try:
            if self.memory.should_trim(session_id):
                removed = self.memory.trim_session(session_id)
                logger.info(
                    f"[CLAW] Trimmed {removed} messages from session {session_id}"
                )
                meta['session_trimmed'] = True
                meta['messages_removed'] = removed

            if self.memory.should_archive(session_id):
                history = self.memory.get_recent_history(session_id, limit=100)
                summary_bullets = await self.summariser.summarise(
                    messages=history,
                    session_id=session_id,
                )
                summary = '\n'.join(f'- {b}' for b in summary_bullets)
                self.memory.archive_session(session_id, summary)
                logger.info(f"[CLAW] Archived session {session_id}")
                meta['session_archived'] = True
                meta['archive_summary'] = summary
        except Exception as exc:
            logger.warning(f"[CLAW] trim/archive check failed: {exc}")
        return meta

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
        executed_tool_calls: list | None = None,
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
            use_opus = self._should_use_opus(envelope.content)
            client, _ = await self._get_api_client(use_opus=use_opus)
            response_text, _, usage = await client.chat(
                system=context_prompt,
                history=history,
                message=f"{envelope.content}\n\n{tool_result_message}",
                tools=None,
                use_opus=use_opus,
            )
            cost_usd = self._calculate_cost(usage, client)

        # If Claude returned nothing, surface the raw tool result so the
        # user always sees something rather than a blank message
        if not response_text:
            response_text = f"**{tool_call['name']} result:**\n\n{result}"

        self.memory.add_message(
            session_id=envelope.session_id,
            role='assistant',
            content=response_text,
            channel=envelope.channel.value,
            model_used=model_used,
            tokens_used=usage.get('total_tokens', 0),
            cost_usd=cost_usd,
        )

        return AgentResponse(
            content=response_text,
            session_id=envelope.session_id,
            project_id=self.project_id,
            model_used=model_used,
            cost_usd=prior_cost + cost_usd,
            executed_tool_calls=executed_tool_calls or [],
        )

    async def _execute_tool(self, tool_call: dict) -> str:
        """Execute a tool and return the result string.
        Long-running tools (e.g. video generation) run in a thread executor
        so they never block the async event loop."""
        tool = self.tools.get(tool_call['name'])
        if not tool:
            return f"ERROR: Unknown tool: {tool_call['name']}"

        inp = tool_call.get('input', {})

        # Tools that are known to be long-running / CPU-bound
        BLOCKING_TOOLS = {'generate_video', 'generate_image', 'run_command', 'run_tests', 'run_migration'}

        try:
            import asyncio, functools
            fn = functools.partial(tool.fn, self._project_root, **inp)
            if tool_call['name'] in BLOCKING_TOOLS:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, fn)
            else:
                result = fn()
            return str(result)
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

    async def _get_api_client(
        self,
        use_opus: bool = False,
        prefer_deepseek: bool = False,
    ) -> tuple[object, str]:
        """
        Return (client, model_name) using the 5-tier routing logic.

        Explicit providers:
          'claude'   — always Claude
          'openai'   — always OpenAI
          'deepseek' — always DeepSeek

        Auto mode (default):
          prefer_deepseek=True  → DeepSeek (if available), else Claude
          prefer_deepseek=False → Claude (Sonnet or Opus)
          Either way: on Claude 429/529 fall back to DeepSeek → OpenAI
        """
        import anthropic

        # ── Explicit provider overrides ──────────────────────────────────────
        if self._api_provider == 'openai':
            if not self.openai:
                raise RuntimeError('API_PROVIDER=openai but OPENAI_API_KEY not set')
            return self.openai, self.openai.model

        if self._api_provider == 'deepseek':
            if not self.deepseek:
                raise RuntimeError('API_PROVIDER=deepseek but DEEPSEEK_API_KEY not set')
            return self.deepseek, self.deepseek.model

        # ── Auto / Claude mode ────────────────────────────────────────────────
        # Tier 2: DeepSeek when router selected it and key is available
        if prefer_deepseek and self.deepseek and not use_opus:
            return self.deepseek, self.deepseek.model

        # Tier 3/4: Claude (Sonnet or Opus)
        # On rate-limit: fall back to DeepSeek → OpenAI
        if self._api_provider in ('auto', 'claude'):
            try:
                model = self.claude.opus_model if use_opus else self.claude.model
                return self.claude, model
            except Exception:
                pass  # shouldn't happen at selection time; errors surface in chat()

        # Tier 5 fallbacks (reached when Claude signals rate-limit during chat)
        if self.deepseek:
            return self.deepseek, self.deepseek.model
        if self.openai:
            return self.openai, self.openai.model

        model = self.claude.opus_model if use_opus else self.claude.model
        return self.claude, model

    async def _chat_with_fallback(
        self,
        client,
        **kwargs,
    ) -> tuple[str, object, dict]:
        """
        Wrap a client.chat() call with automatic fallback on Claude overload.
        Falls back: Claude → DeepSeek → OpenAI.
        """
        import anthropic
        try:
            return await client.chat(**kwargs)
        except (anthropic.RateLimitError, anthropic.APIStatusError) as exc:
            status = getattr(exc, 'status_code', None)
            if status not in (429, 529):
                raise
            # Try DeepSeek first
            if self.deepseek and not isinstance(client, DeepSeekClient):
                fallback = self.deepseek
            elif self.openai and not isinstance(client, OpenAIClient):
                fallback = self.openai
            else:
                raise
            # Strip Claude-only kwargs before retrying
            safe_kwargs = {
                k: v for k, v in kwargs.items()
                if k not in ('use_opus',)
            }
            return await fallback.chat(**safe_kwargs)

    def _calculate_cost(self, usage: dict, client=None) -> float:
        """
        Estimate API cost from token usage.
        Claude Sonnet:  $3/M input,    $15/M output
        Claude Opus:    $15/M input,   $75/M output
        DeepSeek V3:    $0.27/M input, $1.10/M output
        GPT-4o:         $2.50/M input, $10/M output
        """
        if client is None:
            return 0.0
        if isinstance(client, DeepSeekClient):
            return (
                usage.get('input_tokens', 0) * DeepSeekClient.PRICE_INPUT_PER_M
                + usage.get('output_tokens', 0) * DeepSeekClient.PRICE_OUTPUT_PER_M
            ) / 1_000_000
        if isinstance(client, OpenAIClient):
            return (
                usage.get('input_tokens', 0) * 2.5
                + usage.get('output_tokens', 0) * 10.0
            ) / 1_000_000
        # Claude (default — Sonnet pricing; Opus ~5x but we don't differentiate here)
        return (
            usage.get('input_tokens', 0) * 3.0
            + usage.get('output_tokens', 0) * 15.0
        ) / 1_000_000

    def _get_tools_for_task(self, task: str, read_only: bool = False) -> list[dict]:
        """
        Return permitted tools for the current request.
        When read_only=True (WiggumOrchestrator assessment/plan passes),
        only SAFE tools are exposed — no file edits, no commands.
        """
        all_tools = self.tools.describe_for_model(self.config)
        if not read_only:
            return all_tools
        safe_names = {
            name for name, t in self.tools._tools.items()
            if t.risk_level == RiskLevel.SAFE
        }
        return [t for t in all_tools if t['name'] in safe_names]

    def _should_use_opus(self, task: str) -> bool:
        """
        Escalate to Opus for tasks where quality matters most.
        Sonnet handles the majority of coding tasks well.
        Opus reserved for architecture decisions and complex debugging.
        """
        opus_keywords = {
            'architect', 'architecture', 'design decision',
            'security review', 'performance review', 'why is this',
            'root cause', 'fundamentally', 'approach to',
            'best way to structure', 'trade off', 'trade-off',
        }
        task_lower = task.lower()
        return any(kw in task_lower for kw in opus_keywords)

    def _register_tools(self):
        from .tools.file_tools import (
            read_file_tool, edit_file_tool, create_file_tool,
        )
        from .tools.search_tools import search_code_tool
        from .tools.exec_tools import (
            run_tests_tool, run_command_tool,
            run_migration_tool, check_server_tool,
        )
        from .tools.git_tools import (
            git_status_tool, git_diff_tool, git_log_tool,
            git_add_tool, git_commit_tool, git_push_tool,
            git_branch_tool, git_stash_tool,
        )
        from .tools.web_tools import (
            web_fetch_tool, web_check_status_tool, web_search_tool,
        )
        from .tools.video_tools import generate_video_tool
        from .tools.image_tools import generate_image_tool
        for tool in [
            # File
            read_file_tool, edit_file_tool, create_file_tool,
            # Search
            search_code_tool,
            # Exec
            run_tests_tool, run_command_tool,
            run_migration_tool, check_server_tool,
            # Git
            git_status_tool, git_diff_tool, git_log_tool,
            git_add_tool, git_commit_tool, git_push_tool,
            git_branch_tool, git_stash_tool,
            # Web
            web_fetch_tool, web_check_status_tool, web_search_tool,
            # Media
            generate_video_tool, generate_image_tool,
        ]:
            self.tools.register(tool)
