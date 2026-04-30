"""
DeepSeek API client — OpenAI-compatible, identical interface to OpenAIClient.

Note on DSML fallback: DeepSeek occasionally outputs function calls as plain
text using its native DSML format (<｜DSML｜function_calls> or the newer
<｜｜DSML｜｜tool_calls> variant) instead of returning structured tool_calls
via the OpenAI-compatible API. The shared parser in core.models.dsml detects
and parses both variants so the agent can still execute the tool rather than
showing garbage markup to the user.

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

from .dsml import has_dsml_markup, parse_dsml_tool_call
from .message_normaliser import MessageNormaliser

_normaliser = MessageNormaliser()

# Re-export under the legacy private names so existing tests/callers
# (tests/test_deepseek_dsml.py imports `_has_dsml_markup` and
# `_parse_dsml_tool_call` from this module) keep working. The actual
# implementation lives in core.models.dsml — see that module's
# docstring for the on-the-wire variants we tolerate.
_has_dsml_markup = has_dsml_markup
_parse_dsml_tool_call = parse_dsml_tool_call


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
        pre_assembled: list[dict] | None = None,
        cache_manager=None,
        provider_name: str = '',
    ) -> tuple[str, Optional[dict], dict]:
        """
        Same signature as ClaudeClient.chat() and OpenAIClient.chat().
        Returns (response_text, tool_call, usage) where:
          tool_call = {'name': str, 'input': dict, 'tool_use_id': str} | None
          usage     = {'input_tokens': int, 'output_tokens': int, 'total_tokens': int}
        """
        if pre_assembled is not None:
            messages = pre_assembled
        else:
            messages = (
                raw_messages if raw_messages is not None
                else self._build_messages(system, history, message)
            )

        # Belt-and-braces: normalise messages to OpenAI format
        # in case they arrived in Anthropic format from a fallback path
        messages = _normaliser.to_openai(messages)

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
            # Structured tool call — normal path
            tc = choice.message.tool_calls[0]
            tool_call = {
                'name': tc.function.name,
                'input': json.loads(tc.function.arguments),
                'tool_use_id': tc.id,
            }
        elif has_dsml_markup(response_text):
            # DeepSeek emitted DSML markup as plain text instead of using
            # the OpenAI-compatible tool_calls path — parse the markup
            # so the agent loop runs the tool rather than showing the
            # user raw <｜DSML｜…> tokens.
            response_text, tool_call = parse_dsml_tool_call(response_text)

        usage = {
            'input_tokens': response.usage.prompt_tokens,
            'output_tokens': response.usage.completion_tokens,
            'total_tokens': response.usage.total_tokens,
        }

        # Record cache hits if available (DeepSeek supports prompt caching)
        cached_tokens = getattr(response.usage, 'prompt_cache_hit_tokens', 0) or 0
        if cached_tokens and cache_manager:
            try:
                cache_manager.record_request(
                    provider=provider_name or 'deepseek',
                    input_tokens=response.usage.prompt_tokens,
                    cached_tokens=cached_tokens,
                )
            except Exception:
                pass
        usage['cached_input_tokens'] = cached_tokens

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
