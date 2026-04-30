import json
import os
from typing import Optional

from .dsml import has_dsml_markup, parse_dsml_tool_call
from .message_normaliser import MessageNormaliser

_normaliser = MessageNormaliser()


class OpenAIClient:
    """
    OpenAI wrapper with the same chat() interface as ClaudeClient.
    Drop-in replacement — used when ANTHROPIC_API_KEY is rate-limited,
    or when API_PROVIDER=openai is set explicitly.

    Also used as the OpenRouter client (base_url=https://openrouter.ai/api/v1).
    OpenRouter routes DeepSeek under the hood, which means the same DSML
    plain-text-tool-call leak we see on the direct DeepSeek path can hit
    here too — see core.models.dsml for the fallback parser.

    Tool format translation:
        Anthropic: {name, description, input_schema}
        OpenAI:    {type: 'function', function: {name, description, parameters}}
    """

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        model: str | None = None,
    ):
        from openai import AsyncOpenAI
        kwargs: dict = {'api_key': api_key}
        if base_url:
            kwargs['base_url'] = base_url
        self.client = AsyncOpenAI(**kwargs)
        self.model = model or os.getenv('OPENAI_MODEL', 'gpt-4o')
        # Expose same attribute names as ClaudeClient so agent.py can log them
        self.opus_model = self.model

    async def chat(
        self,
        system: str,
        history: list[dict],
        message: str,
        tools: list[dict] | None = None,
        use_opus: bool = False,          # ignored — OpenAI has no Opus equivalent
        image_base64: str | None = None,
        image_media_type: str = 'image/png',
        raw_messages: list[dict] | None = None,
        pre_assembled: list[dict] | None = None,
        cache_manager=None,
        provider_name: str = '',
    ) -> tuple[str, Optional[dict], dict]:
        """
        Same signature as ClaudeClient.chat().
        Returns (response_text, tool_call, usage) where:
          tool_call = {'name': str, 'input': dict, 'tool_use_id': str} | None
          usage     = {'input_tokens': int, 'output_tokens': int, 'total_tokens': int}
        """
        if pre_assembled is not None:
            messages = pre_assembled
        else:
            # raw_messages already includes system as first message
            messages = (
                raw_messages if raw_messages is not None
                else self._build_messages(system, history, message, image_base64, image_media_type)
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
            tc = choice.message.tool_calls[0]
            tool_call = {
                'name': tc.function.name,
                'input': json.loads(tc.function.arguments),
                'tool_use_id': tc.id,
            }
        elif has_dsml_markup(response_text):
            # OpenRouter routing DeepSeek occasionally returns the function
            # call as plain DSML text instead of structured tool_calls.
            # Parse it so the agent loop runs the tool rather than showing
            # the user raw <｜DSML｜…> tokens. (Same bug, same fix as the
            # direct DeepSeek path — see DeepSeekClient.chat.)
            response_text, tool_call = parse_dsml_tool_call(response_text)

        usage = {
            'input_tokens': response.usage.prompt_tokens,
            'output_tokens': response.usage.completion_tokens,
            'total_tokens': response.usage.total_tokens,
        }

        # Record cache hits if available (OpenAI cached_tokens in prompt_tokens_details)
        cached_tokens = 0
        prompt_details = getattr(response.usage, 'prompt_tokens_details', None)
        if prompt_details:
            cached_tokens = getattr(prompt_details, 'cached_tokens', 0) or 0
        if cached_tokens and cache_manager:
            try:
                cache_manager.record_request(
                    provider=provider_name or 'openai',
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
        image_base64: str | None = None,
        image_media_type: str = 'image/png',
    ) -> list[dict]:
        """Public accessor. System IS included as first message (OpenAI format)."""
        return self._build_messages(system, history, message, image_base64, image_media_type)

    @staticmethod
    def append_tool_round(
        messages: list[dict],
        response_text: str,
        tool_call: dict,
        tool_result: str,
    ) -> list[dict]:
        """
        Append an assistant tool_calls turn + tool result turn.
        Returns a new list; does not mutate the original.

        Produces OpenAI's native multi-turn tool-use format:
            assistant: {content:..., tool_calls:[{id:..., function:{name:..., arguments:...}}]}
            tool:      {tool_call_id:..., content:...}
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
        """Translate Anthropic tool schema format to OpenAI function calling format."""
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
        image_base64: str | None = None,
        image_media_type: str = 'image/png',
    ) -> list[dict]:
        # OpenAI uses a top-level system message (unlike Anthropic's separate param)
        messages: list[dict] = [{'role': 'system', 'content': system}]

        for h in history:
            role = h.get('role', 'user')
            if role in ('user', 'assistant'):
                messages.append({'role': role, 'content': h['content']})

        if image_base64:
            content: list | str = [
                {
                    'type': 'image_url',
                    'image_url': {
                        'url': f'data:{image_media_type};base64,{image_base64}',
                    },
                },
                {'type': 'text', 'text': message},
            ]
        else:
            content = message

        messages.append({'role': 'user', 'content': content})
        return messages
