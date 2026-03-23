import json
import httpx
from typing import Optional


class OllamaClient:
    """
    Wrapper around the Ollama REST API.
    Handles chat completion with optional tool/function calling.
    Qwen 2.5 Coder supports tool calling via the Ollama API.
    """

    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip('/')
        self.model = model

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
        usage is {'total_tokens': int}.
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
            # Ollama tool format (same as OpenAI)
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
                f"{self.base_url}/api/chat",
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
            tool_call = {'name': fn.get('name', ''), 'input': args}

        usage = {
            'total_tokens': data.get('eval_count', 0)
            + data.get('prompt_eval_count', 0),
            'input_tokens': data.get('prompt_eval_count', 0),
            'output_tokens': data.get('eval_count', 0),
        }

        return response_text, tool_call, usage

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

    async def is_available(self) -> bool:
        """Check if Ollama is running and the model is loaded."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                data = response.json()
                models = [m['name'] for m in data.get('models', [])]
                return any(self.model in m for m in models)
        except Exception:
            return False
