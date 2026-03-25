"""
DeepSeek API client — OpenAI-compatible, identical interface to OpenAIClient.

DeepSeek V3 (deepseek-chat) sits between local Ollama and Claude:
  - ~10x cheaper than Claude Sonnet
  - Strong coding performance (HumanEval competitive with GPT-4o)
  - OpenAI-compatible API at https://api.deepseek.com

Pricing (as of March 2026):
  Input:  $0.27/M tokens (cache hit: $0.07/M)
  Output: $1.10/M tokens
"""
import json
import os
from typing import Optional


class DeepSeekClient:
    """
    DeepSeek wrapper using the OpenAI-compatible API.
    Implements the same interface as ClaudeClient and OpenAIClient:
      chat(), build_messages(), append_tool_round()

    Drop-in replacement — the agent selects this based on DEEPSEEK_API_KEY
    and routing tier priority.
    """

    # Pricing per million tokens
    PRICE_INPUT_PER_M = 0.27
    PRICE_OUTPUT_PER_M = 1.10

    def __init__(self, api_key: str):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url='https://api.deepseek.com',
        )
        self.model = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')
        # Expose same attribute names as ClaudeClient so agent.py can log them
        self.opus_model = self.model

    async def chat(
        self,
        system: str,
        history: list[dict],
        message: str,
        tools: list[dict] | None = None,
        use_opus: bool = False,           # ignored — no Opus equivalent
        image_base64: str | None = None,  # ignored — DeepSeek V3 is text-only
        image_media_type: str = 'image/png',
        raw_messages: list[dict] | None = None,
    ) -> tuple[str, Optional[dict], dict]:
        """
        Same signature as ClaudeClient.chat() and OpenAIClient.chat().
        Returns (response_text, tool_call, usage) where:
          tool_call = {'name': str, 'input': dict, 'tool_use_id': str} | None
          usage     = {'input_tokens': int, 'output_tokens': int, 'total_tokens': int}
        """
        messages = (
            raw_messages if raw_messages is not None
            else self._build_messages(system, history, message)
        )

        kwargs: dict = {
            'model': self.model,
            'messages': messages,
            'max_tokens': 8096,
        }

        if tools:
            kwargs['tools'] = self._convert_tools(tools)
            kwargs['tool_choice'] = 'auto'

        response = await self.client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        response_text = choice.message.content or ''
        tool_call = None

        if choice.message.tool_calls:
            tc = choice.message.tool_calls[0]
            tool_call = {
                'name': tc.function.name,
                'input': json.loads(tc.function.arguments),
                'tool_use_id': tc.id,
            }

        usage = {
            'input_tokens': response.usage.prompt_tokens,
            'output_tokens': response.usage.completion_tokens,
            'total_tokens': response.usage.total_tokens,
        }

        return response_text, tool_call, usage

    def build_messages(
        self,
        system: str,
        history: list[dict],
        message: str,
        image_base64: str | None = None,   # accepted but ignored (text-only model)
        image_media_type: str = 'image/png',
    ) -> list[dict]:
        """Public accessor. System IS included as first message (OpenAI format)."""
        return self._build_messages(system, history, message)

    @staticmethod
    def append_tool_round(
        messages: list[dict],
        response_text: str,
        tool_call: dict,
        tool_result: str,
    ) -> list[dict]:
        """
        Identical to OpenAIClient.append_tool_round().
        DeepSeek uses the same multi-turn tool-use format as OpenAI.
        """
        return messages + [
            {
                'role': 'assistant',
                'content': response_text or '',
                'tool_calls': [{
                    'id': tool_call['tool_use_id'],
                    'type': 'function',
                    'function': {
                        'name': tool_call['name'],
                        'arguments': json.dumps(tool_call['input']),
                    },
                }],
            },
            {
                'role': 'tool',
                'tool_call_id': tool_call['tool_use_id'],
                'content': tool_result,
            },
        ]

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Translate Anthropic tool schema format to OpenAI/DeepSeek function calling format."""
        return [
            {
                'type': 'function',
                'function': {
                    'name': t['name'],
                    'description': t['description'],
                    'parameters': t['input_schema'],
                },
            }
            for t in tools
        ]

    def _build_messages(
        self,
        system: str,
        history: list[dict],
        message: str,
    ) -> list[dict]:
        messages: list[dict] = [{'role': 'system', 'content': system}]

        for h in history:
            role = h.get('role', 'user')
            if role in ('user', 'assistant'):
                messages.append({'role': role, 'content': h['content']})

        messages.append({'role': 'user', 'content': message})
        return messages
