import anthropic
from typing import Optional


# claude-sonnet-4-5 is the current Sonnet model identifier
CLAUDE_MODEL = "claude-sonnet-4-5"

# Pricing per million tokens (update if Anthropic changes pricing)
PRICE_INPUT_PER_M = 3.0
PRICE_OUTPUT_PER_M = 15.0


class ClaudeClient:
    """
    Wrapper around the Anthropic API.
    Handles chat completion with tool use.
    Used as the API fallback for complex tasks.
    """

    def __init__(self, api_key: str):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

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
        usage is {'input_tokens': int, 'output_tokens': int}.
        """
        messages = self._build_messages(history, message)

        kwargs: dict = {
            'model': CLAUDE_MODEL,
            'max_tokens': 4096,
            'system': system,
            'messages': messages,
        }

        if tools:
            kwargs['tools'] = tools

        response = await self.client.messages.create(**kwargs)

        response_text = ''
        tool_call = None

        for block in response.content:
            if block.type == 'text':
                response_text = block.text
            elif block.type == 'tool_use':
                tool_call = {
                    'name': block.name,
                    'input': block.input,
                    'tool_use_id': block.id,
                }

        usage = {
            'input_tokens': response.usage.input_tokens,
            'output_tokens': response.usage.output_tokens,
            'total_tokens': (
                response.usage.input_tokens + response.usage.output_tokens
            ),
        }

        return response_text, tool_call, usage

    def _build_messages(
        self, history: list[dict], message: str
    ) -> list[dict]:
        messages = []
        for h in history:
            role = h.get('role', 'user')
            if role in ('user', 'assistant'):
                messages.append({'role': role, 'content': h['content']})
        messages.append({'role': 'user', 'content': message})
        return messages
