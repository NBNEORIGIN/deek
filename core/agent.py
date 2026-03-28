import asyncio
import logging
import os
import time
import uuid
from typing import AsyncGenerator
from .channels.envelope import MessageEnvelope, AgentResponse
from .context.engine import ContextEngine
from .models.router import route_decision, estimate_tokens, ModelChoice
from .models.output_validator import validate
from .models.ollama_client import OllamaClient
from .models.claude_client import ClaudeClient
from .models.openai_client import OpenAIClient
from .models.deepseek_client import DeepSeekClient
from .tools.registry import ToolRegistry, RiskLevel
from .tools.diff_tools import generate_unified_diff, generate_create_diff
from .memory.assembler import MemoryAssembler, MemoryPacket
from .memory.cache_manager import CacheManager
from .memory.store import MemoryStore
from .memory.summariser import SessionSummariser
from .skills.manager import SkillManager
from .models.message_normaliser import MessageNormaliser

logger = logging.getLogger(__name__)


class GenerationStopped(Exception):
    """Raised when a session has been asked to stop its in-flight work."""


class GenerationTimedOut(Exception):
    """Raised when a request exceeds the end-to-end deadline."""


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
    EXPLANATION_MAX_TOOL_ROUNDS = 6

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
        self.cache_manager = CacheManager(self.memory)
        self.assembler = MemoryAssembler(
            retriever=getattr(self.context, 'hybrid_retriever', None),
            store=self.memory,
            project_configs=config,
        )
        self.skills = SkillManager(project_id=project_id)
        self._stop_requests: set[str] = set()

        # Cache project root for tool execution
        self._project_root = config.get('codebase_path', '.')

    def request_stop(self, session_id: str) -> None:
        self._stop_requests.add(session_id)

    def clear_stop(self, session_id: str) -> None:
        self._stop_requests.discard(session_id)

    def _check_stop(self, session_id: str) -> None:
        if session_id in self._stop_requests:
            raise GenerationStopped(f'Stopped session {session_id}')

    def _system_prompt_prefix(self) -> str:
        return (
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
        )

    def _request_deadline_seconds(self) -> float:
        raw = os.getenv('CLAW_REQUEST_TIMEOUT_SECONDS', '90')
        try:
            return max(15.0, float(raw))
        except (TypeError, ValueError):
            return 90.0

    def _initialise_request_deadline(self, envelope: MessageEnvelope) -> None:
        envelope.metadata['_deadline_at'] = time.monotonic() + self._request_deadline_seconds()

    def _check_request_active(
        self,
        envelope: MessageEnvelope,
        stage: str = '',
    ) -> None:
        self._check_stop(envelope.session_id)
        deadline_at = envelope.metadata.get('_deadline_at')
        if deadline_at is None:
            return
        if time.monotonic() > float(deadline_at):
            raise GenerationTimedOut(
                f"Request timed out while {stage or 'processing the message'}."
            )

    def _provider_name_for_request(
        self,
        model_choice: ModelChoice,
        client,
        use_opus: bool,
    ) -> str:
        if model_choice == ModelChoice.LOCAL:
            return 'local'
        if isinstance(client, DeepSeekClient):
            return 'deepseek'
        if isinstance(client, OpenAIClient):
            return 'openai'
        if use_opus:
            return 'opus'
        return 'claude'

    @staticmethod
    def _provider_name(tier: int) -> str:
        return {
            1: 'ollama',
            2: 'deepseek',
            3: 'sonnet',
            4: 'opus',
        }.get(tier, 'sonnet')

    async def _resolve_request_skills(
        self,
        envelope: MessageEnvelope,
    ) -> tuple[list, list[str], str | None]:
        # Try async classifier first, fall back to legacy sync path
        try:
            active_skill_ids = await self.skills.get_active_skills(
                query=envelope.content,
                project_id=self.project_id,
                manual_skill_ids=envelope.skill_ids,
            )
            active_skills = self.skills.get_skills(active_skill_ids)
            effective_subproject_id = (
                envelope.subproject_id
                or self.skills.get_skill_subproject_id(active_skill_ids)
            )
        except Exception as exc:
            logger.debug('[CLAW] Async skill resolution failed, using legacy: %s', exc)
            active_skills = self.skills.resolve_for_request(
                query=envelope.content,
                subproject_id=envelope.subproject_id,
                manual_skill_ids=envelope.skill_ids,
            )
            active_skill_ids = [skill.skill_id for skill in active_skills]
            effective_subproject_id = (
                envelope.subproject_id
                or self.skills.primary_subproject_id(active_skills)
            )
        return active_skills, active_skill_ids, effective_subproject_id

    def _assemble_context_prompt(
        self,
        base_context_prompt: str,
        history: list[dict],
        provider_name: str,
        skill_blocks: list[str],
    ) -> tuple[str, dict]:
        assembly = self.assembler.assemble_legacy(
            base_context_prompt=base_context_prompt,
            history=history,
            provider=provider_name,
            skill_blocks=skill_blocks,
        )
        return self._system_prompt_prefix() + assembly.prompt, assembly.metadata

    def _chunk_response_text(self, text: str, chunk_size: int = 180) -> list[str]:
        if not text:
            return []
        text = text.replace('\r\n', '\n')
        chunks: list[str] = []
        for paragraph in text.split('\n\n'):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            while len(paragraph) > chunk_size:
                split_at = paragraph.rfind(' ', 0, chunk_size)
                if split_at < 40:
                    split_at = chunk_size
                chunks.append(paragraph[:split_at] + ('\n\n' if '\n' not in paragraph[:split_at] else ''))
                paragraph = paragraph[split_at:].lstrip()
            if paragraph:
                chunks.append(paragraph + '\n\n')
        return chunks

    async def process(self, envelope: MessageEnvelope) -> AgentResponse:
        """Main entry point. Process a message and return a response."""
        session_id = envelope.session_id
        self.clear_stop(session_id)
        self._initialise_request_deadline(envelope)

        active_skills, active_skill_ids, effective_subproject_id = (
            await self._resolve_request_skills(envelope)
        )

        # Check Opus escalation from skill keywords
        if (
            active_skill_ids
            and not envelope.model_override
            and self.skills.should_escalate_to_opus(envelope.content, active_skill_ids)
        ):
            envelope.model_override = 'opus'
            logger.info('[CLAW] Opus escalation from skill keywords')

        # Bind subproject to this session on first set
        if effective_subproject_id:
            self.memory.set_session_subproject(session_id, effective_subproject_id)
        if active_skill_ids:
            self.memory.set_session_skills(session_id, active_skill_ids)

        self.memory.add_message(
            session_id=session_id,
            role='user',
            content=envelope.content,
            channel=envelope.channel.value,
            subproject_id=effective_subproject_id,
        )

        # Handle tool approval response
        if envelope.tool_approval:
            return await self._handle_tool_approval(envelope)

        # Resolve @ mentions before building context prompt
        resolved_mentions: list[dict] = []
        if envelope.mentions:
            try:
                self._check_request_active(envelope, 'resolving mentions')
                resolved_mentions = await self.context.resolve_mentions(
                    mentions=envelope.mentions,
                    project_id=self.project_id,
                    config=self.config,
                )
            except Exception as exc:
                logger.warning(f"[CLAW] mention resolution failed: {exc}")

        # Build context prompt (scoped to subproject when set)
        raw_context_prompt, context_meta = self.context.build_context_prompt(
            task=envelope.content,
            embedding_fn=self._embed,
            subproject_id=effective_subproject_id,
            resolved_mentions=resolved_mentions or None,
            include_metadata=True,
        )
        self._check_request_active(envelope, 'building context')

        # Append active file from VS Code if provided
        history = self.memory.get_recent_history(session_id, limit=10)
        rough_context_tokens = estimate_tokens(
            self._system_prompt_prefix() + raw_context_prompt + envelope.content
        )
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
        requested_model = (envelope.model_override or '').lower()
        has_image = bool(envelope.image_base64)
        force_tier = _OVERRIDE_MAP.get(requested_model, None)
        if has_image and (force_tier is None or force_tier < 3):
            force_tier = 3

        routing_decision = route_decision(
            task=envelope.content,
            context_tokens=rough_context_tokens,
            project_config=self.config,
            risk_level=routing_risk,
            project=self.project_id,
            files_in_context=context_meta.get('context_file_count', 0),
            force_tier=force_tier,
        )
        model_choice = routing_decision.choice
        use_opus = routing_decision.use_opus

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
            provider_name = 'local'
            skill_blocks = self.skills.build_context_blocks(active_skills)
            context_prompt, assembly_meta = self._assemble_context_prompt(
                raw_context_prompt,
                history,
                provider_name,
                skill_blocks,
            )
            if envelope.active_file:
                try:
                    file_content = self.context.load_tier3(envelope.active_file)
                    context_prompt += (
                        f"\n\n# CURRENTLY OPEN FILE\n"
                        f"## {envelope.active_file}\n"
                        f"```\n{file_content}\n```\n"
                    )
                    context_meta['context_files'] = sorted({
                        *context_meta.get('context_files', []),
                        envelope.active_file,
                    })
                    context_meta['context_file_count'] = len(context_meta['context_files'])
                except (FileNotFoundError, PermissionError):
                    pass
            if envelope.selected_text:
                context_prompt += (
                    f"\n\n# SELECTED CODE\n"
                    f"```\n{envelope.selected_text}\n```\n"
                )
            response_text, tool_call, usage = await self.ollama.chat(
                system=context_prompt,
                history=history,
                message=envelope.content,
                tools=available_tools,
            )
            self._check_request_active(envelope, 'waiting for the local model')
            model_used = self.ollama.model
            cost_usd = 0.0
            client = None
        else:
            client, model_used = await self._get_api_client(
                use_opus=use_opus,
                prefer_deepseek=(model_choice == ModelChoice.DEEPSEEK),
                provider_override=requested_model or None,
                requires_vision=has_image,
            )
            provider_name = self._provider_name_for_request(model_choice, client, use_opus)
            skill_blocks = self.skills.build_context_blocks(active_skills)
            context_prompt, assembly_meta = self._assemble_context_prompt(
                raw_context_prompt,
                history,
                provider_name,
                skill_blocks,
            )
            if envelope.active_file:
                try:
                    file_content = self.context.load_tier3(envelope.active_file)
                    context_prompt += (
                        f"\n\n# CURRENTLY OPEN FILE\n"
                        f"## {envelope.active_file}\n"
                        f"```\n{file_content}\n```\n"
                    )
                    context_meta['context_files'] = sorted({
                        *context_meta.get('context_files', []),
                        envelope.active_file,
                    })
                    context_meta['context_file_count'] = len(context_meta['context_files'])
                except (FileNotFoundError, PermissionError):
                    pass
            if envelope.selected_text:
                context_prompt += (
                    f"\n\n# SELECTED CODE\n"
                    f"```\n{envelope.selected_text}\n```\n"
                )
            response_text, tool_call, usage, client, model_used = await self._chat_with_fallback(
                client,
                system=context_prompt,
                history=history,
                message=envelope.content,
                tools=available_tools,
                use_opus=use_opus,
                image_base64=envelope.image_base64,
                image_media_type=envelope.image_media_type,
                provider_override=requested_model or None,
                requires_vision=has_image,
            )
            self._check_request_active(envelope, 'waiting for the API model')
            cost_usd = self._calculate_cost(usage, client)

        # Build MemoryPacket for structured metadata (non-critical)
        memory_packet: MemoryPacket | None = None
        try:
            memory_packet = await self.assembler.assemble(
                query=envelope.content,
                project_id=self.project_id,
                session_id=session_id,
                provider=provider_name,
                subproject_id=effective_subproject_id,
                skill_ids=active_skill_ids,
                mentions=resolved_mentions,
            )
        except Exception as exc:
            logger.warning('[CLAW] assembler.assemble failed: %s', exc)

        context_tokens = estimate_tokens(context_prompt + envelope.content)
        memory_meta = self._build_memory_metadata(
            context_meta=context_meta,
            context_tokens=context_tokens,
            assembly_meta=assembly_meta,
            active_skill_ids=active_skill_ids,
            packet=memory_packet,
        )

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
                    use_opus=use_opus,
                    context_files=context_meta.get('context_files', []),
                    model_was_manual=_model_was_manual,
                    memory_meta=memory_meta,
                )
            elif tool:
                # REVIEW / DESTRUCTIVE tool: surface for approval (unchanged)
                approval_text = response_text or self._queued_tool_fallback_response(
                    envelope.content,
                    tool_call,
                    [],
                )
                self.memory.add_message(
                    session_id=session_id,
                    role='assistant',
                    content=approval_text,
                    channel=envelope.channel.value,
                    model_used=model_used,
                    tokens_used=usage.get('total_tokens', 0),
                    cost_usd=cost_usd,
                    subproject_id=envelope.subproject_id,
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
                    content=approval_text,
                    session_id=session_id,
                    project_id=self.project_id,
                    pending_tool_call=pending_tool_call,
                    model_used=model_used,
                    tokens_used=usage.get('total_tokens', 0),
                    cost_usd=cost_usd,
                    metadata={
                        'model_routing': 'manual' if _model_was_manual else 'auto',
                        'memory': memory_meta,
                    },
                )

        # No tool call — plain text response
        validation_meta: dict = {}
        response_text, client, model_used, cost_usd, validation_meta = (
            await self._validate_final_response(
                response_text=response_text or '',
                envelope=envelope,
                context_prompt=context_prompt,
                history=history,
                model_choice=model_choice,
                use_opus=use_opus,
                available_tools=available_tools,
                context_files=context_meta.get('context_files', []),
                executed_tool_calls=[],
                current_client=client,
                current_model_used=model_used,
                current_cost=cost_usd,
            )
        )

        self.memory.add_message(
            session_id=session_id,
            role='assistant',
            content=response_text or '',
            channel=envelope.channel.value,
            model_used=model_used,
            tokens_used=usage.get('total_tokens', 0),
            cost_usd=cost_usd,
            subproject_id=envelope.subproject_id,
        )

        metadata = await self._check_trim_archive(session_id)
        metadata['model_routing'] = 'manual' if _model_was_manual else 'auto'
        metadata['memory'] = memory_meta
        metadata.update(validation_meta)

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
            self.clear_stop(session_id)
            self._initialise_request_deadline(envelope)
            active_skills, active_skill_ids, effective_subproject_id = (
                await self._resolve_request_skills(envelope)
            )

            # Check Opus escalation from skill keywords
            if (
                active_skill_ids
                and not envelope.model_override
                and self.skills.should_escalate_to_opus(envelope.content, active_skill_ids)
            ):
                envelope.model_override = 'opus'
                logger.info('[stream] Opus escalation from skill keywords')

            # ── Memory ───────────────────────────────────────────────────────
            if effective_subproject_id:
                self.memory.set_session_subproject(session_id, effective_subproject_id)
            if active_skill_ids:
                self.memory.set_session_skills(session_id, active_skill_ids)
            self.memory.add_message(
                session_id=session_id,
                role='user',
                content=envelope.content,
                channel=envelope.channel.value,
                subproject_id=effective_subproject_id,
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
            yield {'type': 'status', 'message': 'Loading project context…'}
            resolved_mentions: list[dict] = []
            if envelope.mentions:
                try:
                    self._check_request_active(envelope, 'resolving mentions')
                    yield {'type': 'status', 'message': 'Resolving pinned context…'}
                    resolved_mentions = await self.context.resolve_mentions(
                        mentions=envelope.mentions,
                        project_id=self.project_id,
                        config=self.config,
                    )
                except Exception as exc:
                    logger.warning(f'[stream] mention resolution failed: {exc}')

            # ── Context ───────────────────────────────────────────────────────
            raw_context_prompt, context_meta = self.context.build_context_prompt(
                task=envelope.content,
                embedding_fn=self._embed,
                subproject_id=effective_subproject_id,
                resolved_mentions=resolved_mentions or None,
                include_metadata=True,
            )
            self._check_request_active(envelope, 'building context')

            retrieved_chunk_count = int(context_meta.get('retrieved_chunk_count', 0) or 0)
            retrieval_mode = str(context_meta.get('retrieval_mode', 'context'))
            context_file_count = int(context_meta.get('context_file_count', 0) or 0)
            yield {
                'type': 'status',
                'message': (
                    f'Loaded {retrieved_chunk_count} {retrieval_mode} chunks '
                    f'across {context_file_count} files'
                ),
            }

            history = self.memory.get_recent_history(session_id, limit=10)
            rough_context_tokens = estimate_tokens(
                self._system_prompt_prefix() + raw_context_prompt + envelope.content
            )

            # ── Routing ───────────────────────────────────────────────────────
            routing_risk = 'safe' if envelope.read_only else 'review'
            _OVERRIDE_MAP = {'auto': None, 'local': 1, 'deepseek': 2, 'sonnet': 3, 'opus': 4}
            requested_model = (envelope.model_override or '').lower()
            has_image = bool(envelope.image_base64)
            force_tier = _OVERRIDE_MAP.get(requested_model, None)
            if has_image and (force_tier is None or force_tier < 3):
                force_tier = 3
            routing_decision = route_decision(
                task=envelope.content,
                context_tokens=rough_context_tokens,
                project_config=self.config,
                risk_level=routing_risk,
                project=self.project_id,
                files_in_context=context_meta.get('context_file_count', 0),
                force_tier=force_tier,
            )
            model_choice = routing_decision.choice
            use_opus = routing_decision.use_opus
            _model_was_manual = envelope.model_override not in (None, 'auto', '')

            yield {
                'type': 'routing',
                'tier': routing_decision.actual_tier.value,
                'manual': _model_was_manual,
            }
            yield {'type': 'tokens', 'estimated': rough_context_tokens, 'limit': 40_000}

            available_tools = self._get_tools_for_task(
                envelope.content, read_only=envelope.read_only
            )
            yield {'type': 'status', 'message': 'Calling model…'}

            # ── First model call ──────────────────────────────────────────────
            if model_choice == ModelChoice.LOCAL:
                await self.ollama.resolve_active_model(
                    self._ollama_preferred, self._ollama_fallback
                )
                provider_name = 'local'
                skill_blocks = self.skills.build_context_blocks(active_skills)
                context_prompt, assembly_meta = self._assemble_context_prompt(
                    raw_context_prompt,
                    history,
                    provider_name,
                    skill_blocks,
                )
                if envelope.active_file:
                    try:
                        fc = self.context.load_tier3(envelope.active_file)
                        context_prompt += (
                            f'\n\n# CURRENTLY OPEN FILE\n## {envelope.active_file}\n'
                            f'```\n{fc}\n```\n'
                        )
                        context_meta['context_files'] = sorted({
                            *context_meta.get('context_files', []),
                            envelope.active_file,
                        })
                        context_meta['context_file_count'] = len(context_meta['context_files'])
                    except (FileNotFoundError, PermissionError):
                        pass
                if envelope.selected_text:
                    context_prompt += f'\n\n# SELECTED CODE\n```\n{envelope.selected_text}\n```\n'
                response_text, tool_call, usage = await self.ollama.chat(
                    system=context_prompt,
                    history=history,
                    message=envelope.content,
                    tools=available_tools,
                )
                self._check_request_active(envelope, 'waiting for the local model')
                model_used = self.ollama.model
                total_cost = 0.0
                client = None
            else:
                client, model_used = await self._get_api_client(
                    use_opus=use_opus,
                    prefer_deepseek=(model_choice == ModelChoice.DEEPSEEK),
                    provider_override=requested_model or None,
                    requires_vision=has_image,
                )
                provider_name = self._provider_name_for_request(model_choice, client, use_opus)
                skill_blocks = self.skills.build_context_blocks(active_skills)
                context_prompt, assembly_meta = self._assemble_context_prompt(
                    raw_context_prompt,
                    history,
                    provider_name,
                    skill_blocks,
                )
                if envelope.active_file:
                    try:
                        fc = self.context.load_tier3(envelope.active_file)
                        context_prompt += (
                            f'\n\n# CURRENTLY OPEN FILE\n## {envelope.active_file}\n'
                            f'```\n{fc}\n```\n'
                        )
                        context_meta['context_files'] = sorted({
                            *context_meta.get('context_files', []),
                            envelope.active_file,
                        })
                        context_meta['context_file_count'] = len(context_meta['context_files'])
                    except (FileNotFoundError, PermissionError):
                        pass
                if envelope.selected_text:
                    context_prompt += f'\n\n# SELECTED CODE\n```\n{envelope.selected_text}\n```\n'
                response_text, tool_call, usage, client, model_used = await self._chat_with_fallback(
                    client,
                    system=context_prompt,
                    history=history,
                    message=envelope.content,
                    tools=available_tools,
                    use_opus=use_opus,
                    image_base64=envelope.image_base64,
                    image_media_type=envelope.image_media_type,
                    provider_override=requested_model or None,
                    requires_vision=has_image,
                )
                self._check_request_active(envelope, 'waiting for the API model')
                total_cost = self._calculate_cost(usage, client)

            yield {
                'type': 'routing',
                'tier': routing_decision.actual_tier.value,
                'model': model_used,
                'manual': _model_was_manual,
            }

            # Build MemoryPacket for structured metadata
            try:
                memory_packet = await self.assembler.assemble(
                    query=envelope.content,
                    project_id=self.project_id,
                    session_id=session_id,
                    provider=provider_name,
                    subproject_id=effective_subproject_id,
                    skill_ids=active_skill_ids,
                    mentions=resolved_mentions,
                )
            except Exception as exc:
                logger.warning('[stream] assembler.assemble failed: %s', exc)
                memory_packet = None

            memory_meta = self._build_memory_metadata(
                context_meta=context_meta,
                context_tokens=estimate_tokens(context_prompt + envelope.content),
                assembly_meta=assembly_meta,
                active_skill_ids=active_skill_ids,
                packet=memory_packet,
            )

            executed_tool_calls: list[dict] = []
            _seen_read_paths: set[str] = set()
            max_rounds = self._effective_max_tool_rounds(envelope)

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
                self._check_request_active(envelope, f'executing tool round {round_num + 1}')

                if not tool:
                    break

                auto_approve_review = getattr(envelope, 'auto_approve_review', False)
                if (
                    tool.risk_level != RiskLevel.SAFE
                    and not (auto_approve_review and tool.risk_level == RiskLevel.REVIEW)
                ):
                    # REVIEW/DESTRUCTIVE — emit queued event, stop loop
                    approval_text = current_text or self._queued_tool_fallback_response(
                        envelope.content,
                        current_tool_call,
                        executed_tool_calls,
                    )
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
                        content=approval_text,
                        channel=envelope.channel.value,
                        model_used=model_used,
                        tokens_used=0,
                        cost_usd=total_cost,
                        subproject_id=envelope.subproject_id,
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
                        'response': approval_text,
                        'cost_usd': total_cost,
                        'model_used': model_used,
                        'metadata': {
                            'model_routing': 'manual' if _model_was_manual else 'auto',
                            'memory': memory_meta,
                        },
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
                yield {
                    'type': 'status',
                    'message': self._describe_tool_call(current_tool_call),
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
                self._check_request_active(envelope, f'finishing tool {tool_name}')

                duration_ms = int((time.time() - t0) * 1000)
                yield {
                    'type': 'tool_end',
                    'tool': tool_name,
                    'duration_ms': duration_ms,
                    'result_chars': len(result),
                }
                executed_tool_calls.append({
                    'tool_name': tool_name,
                    'input': current_tool_call.get('input', {}),
                    'result': result,
                })

                if client:
                    raw_messages = client.append_tool_round(
                        raw_messages, current_text, current_tool_call, result
                    )

                # Next model call
                force_final = (round_num == max_rounds - 1)
                if client:
                    yield {'type': 'status', 'message': 'Thinking about tool results…'}
                    next_text, next_tool_call, cont_usage, client, model_used = await self._chat_with_fallback(
                        client,
                        system=context_prompt,
                        history=history,
                        message=envelope.content,
                        tools=available_tools if not force_final else None,
                        use_opus=use_opus,
                        raw_messages=raw_messages,
                    )
                    self._check_request_active(envelope, 'thinking about tool results')
                    total_cost += self._calculate_cost(cont_usage, client)
                else:
                    next_text, next_tool_call, _ = await self.ollama.chat(
                        system=context_prompt,
                        history=history,
                        message=f"{envelope.content}\n\nTool result ({tool_name}):\n{result}",
                        tools=available_tools if not force_final else None,
                    )
                    self._check_request_active(envelope, 'thinking about tool results')

                current_text = next_text
                current_tool_call = next_tool_call

            # ── Synthesis fallback ────────────────────────────────────────────
            if not (current_text or '').strip():
                logger.info('[stream] tool loop produced empty response — synthesising')
                yield {'type': 'status', 'message': 'Synthesising final answer…'}
                synthesis_msg = (
                    f'Based on the information you just retrieved, please answer '
                    f'the original question: {envelope.content}'
                )
                if client:
                    synth_text, _, synth_usage, client, model_used = await self._chat_with_fallback(
                        client,
                        system=context_prompt,
                        history=history,
                        message=synthesis_msg,
                        tools=None,
                        use_opus=use_opus,
                        raw_messages=raw_messages,
                    )
                    self._check_request_active(envelope, 'synthesising the final answer')
                    total_cost += self._calculate_cost(synth_usage, client)
                    current_text = synth_text

            # ── Save to memory ────────────────────────────────────────────────
            yield {'type': 'status', 'message': 'Validating answer…'}
            validation_meta: dict = {}
            current_text, client, model_used, total_cost, validation_meta = (
                await self._validate_final_response(
                    response_text=current_text or '',
                    envelope=envelope,
                    context_prompt=context_prompt,
                    history=history,
                    model_choice=model_choice,
                    use_opus=use_opus,
                    available_tools=available_tools,
                    context_files=context_meta.get('context_files', []),
                    executed_tool_calls=executed_tool_calls,
                    current_client=client,
                    current_model_used=model_used,
                    current_cost=total_cost,
                )
            )

            self.memory.add_message(
                session_id=session_id,
                role='assistant',
                content=current_text or '',
                channel=envelope.channel.value,
                model_used=model_used,
                tokens_used=0,
                cost_usd=total_cost,
                subproject_id=envelope.subproject_id,
            )
            metadata = await self._check_trim_archive(session_id)
            metadata['model_routing'] = 'manual' if _model_was_manual else 'auto'
            metadata['memory'] = memory_meta
            metadata.update(validation_meta)

            for chunk in self._chunk_response_text(current_text or ''):
                yield {'type': 'response_delta', 'text': chunk}

            yield {
                'type': 'complete',
                'response': current_text or '',
                'cost_usd': total_cost,
                'model_used': model_used,
                'metadata': metadata,
                'executed_tool_calls': executed_tool_calls,
                'pending_tool_call': None,
            }

        except GenerationStopped:
            logger.info('[stream] generation stopped for %s', session_id)
            yield {
                'type': 'complete',
                'response': '',
                'cost_usd': 0.0,
                'model_used': '',
                'metadata': {'stopped': True},
                'executed_tool_calls': [],
                'pending_tool_call': None,
            }
        except GenerationTimedOut as exc:
            logger.warning('[stream] request timed out for %s: %s', session_id, exc)
            yield {
                'type': 'complete',
                'response': str(exc),
                'cost_usd': 0.0,
                'model_used': '',
                'metadata': {'timed_out': True},
                'executed_tool_calls': [],
                'pending_tool_call': None,
            }
        except Exception as exc:
            logger.exception(f'[stream] unhandled error: {exc}')
            yield {'type': 'error', 'message': str(exc)}
        finally:
            self.clear_stop(session_id)

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
        use_opus: bool,
        context_files: list[str] | None = None,
        model_was_manual: bool = False,
        memory_meta: dict | None = None,
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
        context_files = context_files or []

        # Build the initial messages list once, then extend it each round
        raw_messages = None
        if client:
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
        max_rounds = self._effective_max_tool_rounds(envelope)

        for round_num in range(max_rounds):
            self._check_request_active(envelope, f'executing tool round {round_num + 1}')
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
                approval_text = current_text or self._queued_tool_fallback_response(
                    envelope.content,
                    current_tool_call,
                    executed_tool_calls,
                )
                self.memory.add_message(
                    session_id=session_id, role='assistant',
                    content=approval_text,
                    channel=envelope.channel.value,
                    model_used=model_used,
                    tokens_used=0, cost_usd=total_cost,
                    subproject_id=envelope.subproject_id,
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
                    content=approval_text,
                    session_id=session_id,
                    project_id=self.project_id,
                    pending_tool_call=pending,
                    model_used=model_used,
                    cost_usd=total_cost,
                    executed_tool_calls=executed_tool_calls,
                    metadata={
                        'model_routing': 'manual' if model_was_manual else 'auto',
                        'memory': memory_meta or {},
                    },
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
            self._check_request_active(envelope, f'finishing tool {tool_name}')

            executed_tool_calls.append({
                'tool_name': tool_name,
                'input': current_tool_call.get('input', {}),
                'result': result,
            })

            # Extend the messages list with this tool round
            if client and raw_messages is not None:
                raw_messages = client.append_tool_round(
                    raw_messages, current_text, current_tool_call, result
                )

            # On the last allowed round, force a text answer (no tools)
            force_final = (round_num == max_rounds - 1)
            if client:
                next_text, next_tool_call, cont_usage, client, model_used = await self._chat_with_fallback(
                    client,
                    system=context_prompt,
                    history=history,
                    message=envelope.content,
                    tools=available_tools if not force_final else None,
                    use_opus=use_opus,
                    raw_messages=raw_messages,
                )
                self._check_request_active(envelope, 'thinking about tool results')
                total_cost += self._calculate_cost(cont_usage, client)
            else:
                next_text, next_tool_call, _ = await self.ollama.chat(
                    system=context_prompt,
                    history=history,
                    message=f"{envelope.content}\n\nTool result ({tool_name}):\n{result}",
                    tools=available_tools if not force_final else None,
                )
                self._check_request_active(envelope, 'thinking about tool results')

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
            if client:
                synth_text, _, synth_usage, client, model_used = await self._chat_with_fallback(
                    client,
                    system=context_prompt,
                    history=history,
                    message=synthesis_msg,
                    tools=None,
                    use_opus=use_opus,
                    raw_messages=raw_messages,
                )
                self._check_request_active(envelope, 'synthesising the final answer')
                total_cost += self._calculate_cost(synth_usage, client)
                current_text = synth_text
            else:
                synth_text, _, _ = await self.ollama.chat(
                    system=context_prompt,
                    history=history,
                    message=synthesis_msg,
                    tools=None,
                )
                self._check_request_active(envelope, 'synthesising the final answer')
                current_text = synth_text

        validation_meta: dict = {}
        current_text, client, model_used, total_cost, validation_meta = (
            await self._validate_final_response(
                response_text=current_text or '',
                envelope=envelope,
                context_prompt=context_prompt,
                history=history,
                model_choice=model_choice,
                use_opus=use_opus,
                available_tools=available_tools,
                context_files=context_files,
                executed_tool_calls=executed_tool_calls,
                current_client=client,
                current_model_used=model_used,
                current_cost=total_cost,
            )
        )

        # Save the final answer to memory once
        self.memory.add_message(
            session_id=session_id,
            role='assistant',
            content=current_text or '',
            channel=envelope.channel.value,
            model_used=model_used,
            tokens_used=0,
            cost_usd=total_cost,
            subproject_id=envelope.subproject_id,
        )

        metadata = await self._check_trim_archive(session_id)
        metadata['model_routing'] = 'manual' if model_was_manual else 'auto'
        metadata['memory'] = memory_meta or {}
        metadata.update(validation_meta)

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
                active_skill_ids = self.memory.get_session_skills(session_id)
                summary_bullets = await self.summariser.summarise(
                    messages=history,
                    session_id=session_id,
                    skill_ids=active_skill_ids,
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
                subproject_id=envelope.subproject_id,
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
            use_opus = route_decision(
                task=envelope.content,
                context_tokens=estimate_tokens(context_prompt + envelope.content),
                project_config=self.config,
                risk_level='review',
                project=self.project_id,
            ).use_opus
            client, _ = await self._get_api_client(use_opus=use_opus)
            response_text, _, usage, client, model_used = await self._chat_with_fallback(
                client,
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
            subproject_id=envelope.subproject_id,
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

    async def _validate_final_response(
        self,
        response_text: str,
        envelope: MessageEnvelope,
        context_prompt: str,
        history: list[dict],
        model_choice: ModelChoice,
        use_opus: bool,
        available_tools: list[dict],
        context_files: list[str],
        executed_tool_calls: list[dict],
        current_client,
        current_model_used: str,
        current_cost: float,
    ) -> tuple[str, object | None, str, float, dict]:
        """
        Validate the final user-visible response and retry on recoverable
        failures with progressively stronger models.
        """
        metadata: dict = {}
        written_files = self._written_files_from_tool_calls(executed_tool_calls)

        if not (response_text or '').strip() and executed_tool_calls and current_client is not None:
            synthesized_text, current_client, current_model_used, current_cost = (
                await self._retry_empty_response_with_current_client(
                    envelope=envelope,
                    context_prompt=context_prompt,
                    history=history,
                    executed_tool_calls=executed_tool_calls,
                    current_client=current_client,
                    current_model_used=current_model_used,
                    current_cost=current_cost,
                    use_opus=use_opus,
                )
            )
            if synthesized_text.strip():
                response_text = synthesized_text

        validation = validate(
            response_text,
            executed_tool_calls=executed_tool_calls,
            files_in_context=context_files,
            written_files=written_files,
            project=self.project_id,
            project_root=self._project_root,
        )
        if validation.passed:
            return response_text, current_client, current_model_used, current_cost, metadata

        metadata['validation_failures'] = validation.failures

        if validation.hard_fail:
            failure_text = "Validation failed:\n" + '\n'.join(
                f'- {failure}' for failure in validation.failures
            )
            return failure_text, current_client, current_model_used, current_cost, metadata

        recovery_message = self._build_validation_retry_prompt(
            envelope.content,
            validation.failures,
            executed_tool_calls,
            context_files=context_files,
        )

        for retry_choice in self._next_model_choices(
            model_choice,
            requires_vision=bool(envelope.image_base64),
        ):
            if retry_choice == ModelChoice.LOCAL:
                continue
            retry_client, _ = await self._get_api_client(
                use_opus=use_opus,
                prefer_deepseek=(
                    retry_choice == ModelChoice.DEEPSEEK
                    and not envelope.image_base64
                ),
                provider_override=(
                    (envelope.model_override or '').lower() or None
                    if retry_choice == ModelChoice.API else None
                ),
                requires_vision=bool(envelope.image_base64),
            )
            retried_text, _, retry_usage, retry_client, retry_model_used = await self._chat_with_fallback(
                retry_client,
                system=context_prompt,
                history=history,
                message=recovery_message,
                tools=None,
                use_opus=use_opus,
                image_base64=envelope.image_base64,
                image_media_type=envelope.image_media_type,
                provider_override=(envelope.model_override or '').lower() or None,
                requires_vision=bool(envelope.image_base64),
            )
            retry_cost = current_cost + self._calculate_cost(retry_usage, retry_client)
            retried_validation = validate(
                retried_text or '',
                executed_tool_calls=executed_tool_calls,
                files_in_context=context_files,
                written_files=written_files,
                project=self.project_id,
                project_root=self._project_root,
            )
            if retried_validation.passed:
                metadata['validation_recovered'] = True
                metadata['validation_retries'] = metadata.get('validation_retries', 0) + 1
                return (
                    retried_text,
                    retry_client,
                    retry_model_used,
                    retry_cost,
                    metadata,
                )
            metadata['validation_failures'] = retried_validation.failures
            current_cost = retry_cost

        if any(failure.startswith('CHECK 5') for failure in metadata['validation_failures']) and executed_tool_calls:
            metadata['validation_fallback_used'] = 'tool_results_summary'
            return (
                self._tool_results_fallback_response(
                    envelope.content,
                    executed_tool_calls,
                ),
                current_client,
                current_model_used,
                current_cost,
                metadata,
            )

        failure_text = "Validation failed:\n" + '\n'.join(
            f'- {failure}' for failure in metadata['validation_failures']
        )
        return failure_text, current_client, current_model_used, current_cost, metadata

    async def _retry_empty_response_with_current_client(
        self,
        envelope: MessageEnvelope,
        context_prompt: str,
        history: list[dict],
        executed_tool_calls: list[dict],
        current_client,
        current_model_used: str,
        current_cost: float,
        use_opus: bool,
    ) -> tuple[str, object, str, float]:
        synthesis_prompt = self._build_empty_response_retry_prompt(
            envelope.content,
            executed_tool_calls,
        )
        retried_text, _, retry_usage, current_client, current_model_used = await self._chat_with_fallback(
            current_client,
            system=context_prompt,
            history=history,
            message=synthesis_prompt,
            tools=None,
            use_opus=use_opus,
            image_base64=envelope.image_base64,
            image_media_type=envelope.image_media_type,
            provider_override=(envelope.model_override or '').lower() or None,
            requires_vision=bool(envelope.image_base64),
        )
        current_cost += self._calculate_cost(retry_usage, current_client)
        return retried_text or '', current_client, current_model_used, current_cost

    def _build_memory_metadata(
        self,
        context_meta: dict,
        context_tokens: int,
        assembly_meta: dict | None = None,
        active_skill_ids: list[str] | None = None,
        packet: MemoryPacket | None = None,
    ) -> dict:
        # Legacy path: build from context_meta + assembly_meta
        match_counts = context_meta.get('match_quality_counts', {}) or {}
        budget_pct = min(100, round((context_tokens / 40_000) * 100, 1))
        assembly_meta = assembly_meta or {}
        section_tokens = context_meta.get('assembly_tokens', {}) or {}
        return {
            'retrieval_mode': context_meta.get('retrieval_mode', 'keyword'),
            'chunks': int(context_meta.get('retrieved_chunk_count', 0) or 0),
            'files': int(context_meta.get('context_file_count', 0) or 0),
            'mentions': int(context_meta.get('resolved_mention_count', 0) or 0),
            'estimated_tokens': int(context_tokens or 0),
            'budget_pct': float(assembly_meta.get('budget_pct', budget_pct)),
            'exact_hits': int(match_counts.get('exact', 0) or 0),
            'semantic_hits': int(match_counts.get('semantic', 0) or 0),
            'both_hits': int(match_counts.get('exact+semantic', 0) or 0),
            'retrieved_files': list(context_meta.get('retrieved_files', []) or []),
            'provider': assembly_meta.get('provider'),
            'budget_total': int(assembly_meta.get('budget_total', 0) or 0),
            'budget_used': int(assembly_meta.get('budget_used', 0) or 0),
            'history_messages': int(assembly_meta.get('history_messages', 0) or 0),
            'core_tokens': int(section_tokens.get('core', 0) or 0),
            'skill_tokens': int(section_tokens.get('skill', 0) or 0),
            'mention_tokens': int(section_tokens.get('mentions', 0) or 0),
            'retrieved_tokens': int(section_tokens.get('retrieved', 0) or 0),
            'active_skills': list(active_skill_ids or []),
        }

        # Enrich with MemoryPacket data when available (budget breakdowns, provider info)
        if packet is not None:
            from .memory.assembler import PROVIDER_BUDGETS
            budget = PROVIDER_BUDGETS.get(packet.provider, PROVIDER_BUDGETS.get('sonnet', {}))
            result['provider'] = packet.provider
            result['budget_total'] = budget.get('total', 0)
            result['budget_used'] = packet.total_tokens_estimated
            result['budget_pct'] = packet.budget_used_pct
            result['estimated_tokens'] = packet.total_tokens_estimated
            result['history_messages'] = len(packet.recent_messages)
            result['core_tokens'] = self.assembler._estimate_tokens(packet.core_rules)
            result['skill_tokens'] = self.assembler._estimate_tokens(packet.skill_context or '')
            result['mention_tokens'] = sum(
                self.assembler._estimate_tokens(m.get('content', ''))
                for m in packet.mentioned_context
            )
            result['retrieved_tokens'] = sum(
                self.assembler._estimate_tokens(c.get('content', ''))
                for c in packet.retrieved_chunks
            )
            result['active_skills'] = list(packet.active_skills)

        return result

    def _written_files_from_tool_calls(self, executed_tool_calls: list[dict]) -> list[str]:
        written_files: list[str] = []
        for call in executed_tool_calls:
            tool_name = call.get('tool_name')
            result = call.get('result', '')
            file_path = call.get('input', {}).get('file_path')
            if tool_name in {'edit_file', 'create_file'} and file_path and str(result).startswith('OK:'):
                written_files.append(os.path.join(self._project_root, file_path))
        return written_files

    def _build_validation_retry_prompt(
        self,
        original_message: str,
        failures: list[str],
        executed_tool_calls: list[dict],
        context_files: list[str] | None = None,
    ) -> str:
        failure_block = '\n'.join(f'- {failure}' for failure in failures)
        tool_summary = self._tool_results_summary(executed_tool_calls)
        prompt = (
            "The previous draft failed validation.\n"
            f"Validation failures:\n{failure_block}\n\n"
            f"Original request:\n{original_message}\n\n"
            "Write a corrected final answer. Do not mention tool names, "
            "simulated actions, or nonexistent files."
        )
        if context_files:
            allowed = '\n'.join(f'- {path}' for path in context_files[:20])
            prompt += (
                "\n\nIf you mention files, ONLY use files from this allowlist "
                "or files explicitly shown in tool results:\n"
                f"{allowed}"
            )
        if tool_summary:
            prompt += f"\n\nAvailable tool results:\n{tool_summary}"
        return prompt

    def _build_empty_response_retry_prompt(
        self,
        original_message: str,
        executed_tool_calls: list[dict],
    ) -> str:
        tool_summary = self._tool_results_summary(executed_tool_calls)
        prompt = (
            "You already gathered the needed context with tools, but your last "
            "draft was empty.\n\n"
            f"Original request:\n{original_message}\n\n"
            "Write a direct final answer now.\n"
            "Requirements:\n"
            "- Answer the user's question directly.\n"
            "- Mention the main files involved when relevant.\n"
            "- Summarise the flow or result in clear prose or bullets.\n"
            "- Do not call tools.\n"
            "- Do not say you cannot, cannot access, or need more tools.\n"
        )
        if tool_summary:
            prompt += f"\n\nAvailable tool results:\n{tool_summary}"
        return prompt

    def _tool_results_summary(self, executed_tool_calls: list[dict]) -> str:
        lines: list[str] = []
        for call in executed_tool_calls[-6:]:
            result = str(call.get('result', ''))
            if len(result) > 400:
                result = result[:400] + '...'
            lines.append(
                f"{call.get('tool_name', 'tool')} "
                f"{call.get('input', {})}: {result}"
            )
        return '\n'.join(lines)

    def _tool_results_fallback_response(
        self,
        original_message: str,
        executed_tool_calls: list[dict],
    ) -> str:
        files_reviewed: list[str] = []
        searches: list[str] = []
        for call in executed_tool_calls:
            tool_name = call.get('tool_name')
            inp = call.get('input', {})
            if tool_name == 'read_file':
                file_path = inp.get('file_path')
                if file_path and file_path not in files_reviewed:
                    files_reviewed.append(file_path)
            elif tool_name == 'search_code':
                query = inp.get('query')
                if query:
                    searches.append(query)

        response = (
            f"I gathered context for your request, `{original_message}`.\n\n"
            "Here are the main sources CLAW inspected:"
        )
        if files_reviewed:
            response += '\n' + '\n'.join(f"- `{path}`" for path in files_reviewed[:8])
        else:
            response += "\n- No file paths were captured from the tool results."

        if searches:
            response += (
                "\n\nSearches run:\n"
                + '\n'.join(f"- `{query}`" for query in searches[:4])
            )

        response += (
            "\n\nThe model did not produce a polished final narrative after using those tools, "
            "but the retrieved files above are the primary sources for answering the request."
        )
        return response

    def _next_model_choices(
        self,
        current_choice: ModelChoice,
        requires_vision: bool = False,
    ) -> list[ModelChoice]:
        if current_choice == ModelChoice.LOCAL:
            if requires_vision:
                return [ModelChoice.API]
            return [ModelChoice.DEEPSEEK, ModelChoice.API]
        if current_choice == ModelChoice.DEEPSEEK:
            return [ModelChoice.API]
        return []

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
        provider_override: str | None = None,
        requires_vision: bool = False,
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
        override = (provider_override or '').lower()

        if override in ('sonnet', 'opus'):
            model = self.claude.opus_model if override == 'opus' else self.claude.model
            return self.claude, model

        if override == 'deepseek':
            if requires_vision:
                return self.claude, self.claude.model
            if not self.deepseek:
                raise RuntimeError('model_override=deepseek but DEEPSEEK_API_KEY not set')
            return self.deepseek, self.deepseek.model

        # ── Explicit provider overrides ──────────────────────────────────────
        if self._api_provider == 'openai':
            if not self.openai:
                raise RuntimeError('API_PROVIDER=openai but OPENAI_API_KEY not set')
            return self.openai, self.openai.model

        if self._api_provider == 'deepseek':
            if requires_vision:
                if self.openai:
                    return self.openai, self.openai.model
                return self.claude, self.claude.opus_model if use_opus else self.claude.model
            if not self.deepseek:
                raise RuntimeError('API_PROVIDER=deepseek but DEEPSEEK_API_KEY not set')
            return self.deepseek, self.deepseek.model

        # ── Auto / Claude mode ────────────────────────────────────────────────
        # Tier 2: DeepSeek when router selected it and key is available
        if prefer_deepseek and self.deepseek and not use_opus and not requires_vision:
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
    ) -> tuple[str, dict | None, dict, object, str]:
        """
        Wrap a client.chat() call with automatic fallback on retryable
        provider errors. Falls back: Claude → DeepSeek → OpenAI.
        """
        request_kwargs = dict(kwargs)
        requires_vision = bool(
            request_kwargs.pop('requires_vision', False)
            or request_kwargs.get('image_base64')
        )
        provider_override = request_kwargs.pop('provider_override', None)
        timeout_seconds = self._model_timeout_seconds()
        try:
            response_text, tool_call, usage = await asyncio.wait_for(
                client.chat(**request_kwargs),
                timeout=timeout_seconds,
            )
            return (
                response_text,
                tool_call,
                usage,
                client,
                self._model_name_for_client(client, request_kwargs.get('use_opus', False)),
            )
        except Exception as exc:
            if not self._is_retryable_api_error(exc, allow_timeout=True):
                raise
            fallback = self._fallback_client_for(
                client,
                requires_vision=requires_vision,
                provider_override=provider_override,
            )
            if fallback is None:
                raise GenerationTimedOut(
                    f"Model call timed out or failed after {timeout_seconds:.0f}s and no fallback provider was available."
                ) from exc
            safe_kwargs = dict(request_kwargs)
            if not isinstance(fallback, ClaudeClient):
                safe_kwargs.pop('use_opus', None)

            # Normalise message history for the target provider
            normaliser = MessageNormaliser()
            if isinstance(fallback, (OpenAIClient, DeepSeekClient)) and not isinstance(client, (OpenAIClient, DeepSeekClient)):
                for key in ('raw_messages',):
                    if key in safe_kwargs and safe_kwargs[key]:
                        safe_kwargs[key] = normaliser.to_openai(safe_kwargs[key])
                if 'history' in safe_kwargs and safe_kwargs['history']:
                    safe_kwargs['history'] = normaliser.to_openai(safe_kwargs['history'])
            elif isinstance(fallback, ClaudeClient) and not isinstance(client, ClaudeClient):
                for key in ('raw_messages',):
                    if key in safe_kwargs and safe_kwargs[key]:
                        safe_kwargs[key] = normaliser.to_anthropic(safe_kwargs[key])
                if 'history' in safe_kwargs and safe_kwargs['history']:
                    safe_kwargs['history'] = normaliser.to_anthropic(safe_kwargs['history'])

            try:
                response_text, tool_call, usage = await asyncio.wait_for(
                    fallback.chat(**safe_kwargs),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError as timeout_exc:
                raise GenerationTimedOut(
                    f"Fallback model call timed out after {timeout_seconds:.0f}s."
                ) from timeout_exc
            return (
                response_text,
                tool_call,
                usage,
                fallback,
                self._model_name_for_client(fallback, False),
            )

    def _fallback_client_for(
        self,
        client,
        requires_vision: bool = False,
        provider_override: str | None = None,
    ):
        override = (provider_override or '').lower()
        if isinstance(client, ClaudeClient):
            if requires_vision or override in ('sonnet', 'opus'):
                if self.openai:
                    return self.openai
                return None
            if self.deepseek:
                return self.deepseek
            if self.openai:
                return self.openai
            return None
        if isinstance(client, DeepSeekClient):
            if requires_vision:
                if self.openai:
                    return self.openai
                return self.claude
            if self.openai:
                return self.openai
            return None
        return None

    def _model_name_for_client(self, client, use_opus: bool) -> str:
        if isinstance(client, ClaudeClient):
            return client.opus_model if use_opus else client.model
        return getattr(client, 'model', '')

    def _is_retryable_api_error(self, exc: Exception, allow_timeout: bool = False) -> bool:
        if allow_timeout and isinstance(exc, asyncio.TimeoutError):
            return True
        status = getattr(exc, 'status_code', None)
        if status in (429, 529):
            return True
        msg = str(exc).lower()
        return any(token in msg for token in ('429', '529', 'rate limit', 'overload'))

    def _model_timeout_seconds(self) -> float:
        raw = os.getenv('CLAW_MODEL_TIMEOUT_SECONDS', '45')
        try:
            return max(5.0, float(raw))
        except (TypeError, ValueError):
            return 45.0

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
        if read_only or self._should_limit_to_safe_tools(task):
            safe_names = {
                name for name, t in self.tools._tools.items()
                if t.risk_level == RiskLevel.SAFE
            }
            return [t for t in all_tools if t['name'] in safe_names]
        return all_tools

    def _should_limit_to_safe_tools(self, task: str) -> bool:
        """
        Keep architecture / explanation prompts in a read-only tool posture even
        during normal chat. These requests should answer from retrieved context
        rather than escalating into edits or shell commands.
        """
        text = task.lower()
        action_keywords = (
            'fix ', 'implement ', 'change ', 'edit ', 'update ', 'create ',
            'write ', 'delete ', 'rename ', 'refactor ', 'run ', 'execute ',
            'commit ', 'apply ', 'add ', 'remove ', 'patch ',
        )
        if any(keyword in text for keyword in action_keywords):
            return False

        explanation_keywords = (
            'explain', 'describe', 'tell me about', 'how does', 'how do',
            'what is', 'walk me through', 'flow', 'overview', 'architecture',
            'which files', 'name the main files', 'where does', 'read the',
        )
        return any(keyword in text for keyword in explanation_keywords)

    def _effective_max_tool_rounds(self, envelope: MessageEnvelope) -> int:
        if getattr(envelope, 'max_tool_rounds', None):
            return int(envelope.max_tool_rounds)
        if envelope.read_only or self._should_limit_to_safe_tools(envelope.content):
            return self.EXPLANATION_MAX_TOOL_ROUNDS
        return self.MAX_TOOL_ROUNDS

    def _queued_tool_fallback_response(
        self,
        original_request: str,
        tool_call: dict,
        executed_tool_calls: list[dict],
    ) -> str:
        response = (
            "I gathered some context, but the next step requested approval for "
            f"`{tool_call.get('name', 'a tool')}` and I have not executed it."
        )
        summary = self._tool_results_summary(executed_tool_calls)
        if summary:
            response += f"\n\nContext gathered so far:\n{summary}"
        response += f"\n\nOriginal request: {original_request}"
        return response

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
