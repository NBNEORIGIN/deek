"""
Ollama local inference client.

Supported models and estimated VRAM requirements (Q4 quantisation):
  ┌─────────────────────────────┬──────────┬─────────────────────────────────┐
  │ Model                        │ VRAM GB  │ Notes                           │
  ├─────────────────────────────┼──────────┼─────────────────────────────────┤
  │ deepseek-coder-v2:16b        │ 10.5     │ Preferred — strong coding, 16B  │
  │ qwen2.5-coder:7b             │  5.5     │ Fallback — fits RTX 3050 8GB    │
  │ qwen2.5-coder:32b            │ 20.0     │ Requires RTX 3090+              │
  │ nomic-embed-text             │  0.3     │ Embedding only                  │
  └─────────────────────────────┴──────────┴─────────────────────────────────┘

Routing:
  1. Try OLLAMA_MODEL_PREFERRED (default: deepseek-coder-v2:16b)
     — if pulled and Ollama reachable
  2. Fall back to OLLAMA_MODEL (default: qwen2.5-coder:7b)
     — if preferred model not pulled

Set OLLAMA_MODEL_PREFERRED=qwen2.5-coder:7b in .env if deepseek-coder-v2:16b
does not fit in your GPU VRAM.
"""
import json
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

# VRAM requirements in GB (Q4 quantisation, approximate)
VRAM_REQUIREMENTS: dict[str, float] = {
    'deepseek-coder-v2:16b': 10.5,
    'qwen2.5-coder:7b': 5.5,
    'qwen2.5-coder:32b': 20.0,
    'nomic-embed-text': 0.3,
}

# VRAM available on current dev GPU — adjust after hardware upgrades
_VRAM_AVAILABLE_GB = 8.0


class OllamaClient:
    """
    Wrapper around the Ollama REST API.
    Handles chat completion with optional tool/function calling.

    Model selection:
        Tries OLLAMA_MODEL_PREFERRED first (deepseek-coder-v2:16b by default).
        Falls back to OLLAMA_MODEL (qwen2.5-coder:7b) if preferred not pulled.
    """

    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip('/')
        self.model = model     # resolved active model (set by get_active_model)
        self._preferred = model  # initial value before resolution

    # ─── Model resolution ─────────────────────────────────────────────────────

    async def get_available_models(self) -> list[str]:
        """Query Ollama and return names of all pulled models."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f'{self.base_url}/api/tags')
                r.raise_for_status()
                return [m['name'] for m in r.json().get('models', [])]
        except Exception:
            return []

    async def resolve_active_model(self, preferred: str, fallback: str) -> str:
        """
        Determine which model to actually use.
        Tries `preferred` first; uses `fallback` if preferred is not pulled.
        Logs a VRAM warning if the chosen model exceeds available VRAM.
        Returns the resolved model name.
        """
        available = await self.get_available_models()

        def is_pulled(name: str) -> bool:
            return any(name in m for m in available)

        chosen = preferred if is_pulled(preferred) else fallback

        # VRAM advisory
        vram_needed = _vram_for(chosen)
        if vram_needed > _VRAM_AVAILABLE_GB:
            logger.warning(
                f'[ollama] {chosen} needs ~{vram_needed:.1f} GB VRAM '
                f'(GPU has {_VRAM_AVAILABLE_GB:.0f} GB) — '
                f'may OOM; set OLLAMA_MODEL_PREFERRED=qwen2.5-coder:7b to override'
            )
        else:
            logger.info(
                f'[ollama] active model: {chosen} '
                f'(~{vram_needed:.1f} GB VRAM)'
            )

        self.model = chosen
        return chosen

    # ─── Inference ────────────────────────────────────────────────────────────

    async def chat(
        self,
        system: str,
        history: list[dict],
        message: str,
        tools: list[dict] | None = None,
    ) -> tuple[str, Optional[dict], dict]:
        """
        Send a chat message and return (response_text, tool_call, usage).
        tool_call is {'name': str, 'input': dict} or None.
        usage is {'total_tokens': int, 'input_tokens': int, 'output_tokens': int}.
        """
        messages = self._build_messages(system, history, message)

        payload: dict = {
            'model': self.model,
            'messages': messages,
            'stream': False,
            'options': {
                'temperature': 0.2,
                'num_ctx': 8192,
            },
        }

        if tools:
            payload['tools'] = [
                {
                    'type': 'function',
                    'function': {
                        'name': t['name'],
                        'description': t['description'],
                        'parameters': t.get('input_schema', {}),
                    },
                }
                for t in tools
            ]

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f'{self.base_url}/api/chat',
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        msg = data.get('message', {})
        response_text = msg.get('content', '')
        tool_calls = msg.get('tool_calls', [])

        tool_call = None
        if tool_calls:
            tc = tool_calls[0]
            fn = tc.get('function', {})
            args = fn.get('arguments', {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_call = {
                'name': fn.get('name', ''),
                'input': args,
                'tool_use_id': f'local_{fn.get("name", "tool")}',
            }
        else:
            # Some models emit tool calls as plain-text JSON rather than
            # using the tool_calls API field. Detect and parse them.
            tool_call = self._extract_text_tool_call(response_text)
            if tool_call:
                response_text = ''

        usage = {
            'total_tokens': data.get('eval_count', 0) + data.get('prompt_eval_count', 0),
            'input_tokens': data.get('prompt_eval_count', 0),
            'output_tokens': data.get('eval_count', 0),
        }

        return response_text, tool_call, usage

    # ─── Availability ─────────────────────────────────────────────────────────

    async def is_available(self) -> bool:
        """Return True if Ollama is running and the active model is pulled."""
        try:
            available = await self.get_available_models()
            return any(self.model in m for m in available)
        except Exception:
            return False

    def vram_warning(self) -> bool:
        """True if the active model is estimated to exceed available VRAM."""
        return _vram_for(self.model) > _VRAM_AVAILABLE_GB

    # ─── Internal helpers ─────────────────────────────────────────────────────

    def _build_messages(
        self, system: str, history: list[dict], message: str
    ) -> list[dict]:
        messages = [{'role': 'system', 'content': system}]
        for h in history:
            role = h.get('role', 'user')
            if role in ('user', 'assistant'):
                messages.append({'role': role, 'content': h['content']})
        messages.append({'role': 'user', 'content': message})
        return messages

    def _extract_text_tool_call(self, text: str) -> dict | None:
        """
        Some models (Qwen, DeepSeek-Coder) emit tool calls as raw JSON
        in the response text rather than via the tool_calls field.
        Patterns seen:
          {"name": "read_file", "arguments": {"file_path": "..."}}
          {"name": "read_file", "parameters": {"file_path": "..."}}
        """
        if not text or '{' not in text:
            return None
        clean = text.strip()
        if clean.startswith('```'):
            clean = '\n'.join(clean.split('\n')[1:])
            clean = clean.rstrip('`').strip()
        try:
            parsed = json.loads(clean)
            if isinstance(parsed, dict) and 'name' in parsed:
                args = (
                    parsed.get('arguments')
                    or parsed.get('parameters')
                    or parsed.get('input')
                    or {}
                )
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                return {
                    'name': parsed['name'],
                    'input': args,
                    'tool_use_id': f'local_{parsed["name"]}',
                }
        except (json.JSONDecodeError, ValueError):
            pass
        return None


# ─── Module-level helper ──────────────────────────────────────────────────────

def _vram_for(model: str) -> float:
    """Return estimated VRAM requirement in GB for the given model name."""
    for key, gb in VRAM_REQUIREMENTS.items():
        if key in model:
            return gb
    return 4.0  # conservative unknown default
