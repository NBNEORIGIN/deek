import os
import anthropic
from typing import Optional


# Pricing per million tokens (update if Anthropic changes pricing)
PRICE_INPUT_PER_M = 3.0
PRICE_OUTPUT_PER_M = 15.0


class ClaudeClient:
    """
    Wrapper around the Anthropic API.
    Handles chat completion with tool use and vision (image) input.
    Models are read from env vars — switch without touching code.
    """

    def __init__(self, api_key: str):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = os.getenv('CLAUDE_MODEL', 'claude-sonnet-4-6')
        self.opus_model = os.getenv('CLAUDE_OPUS_MODEL', 'claude-opus-4-6')

    async def chat(
        self,
        system: str,
        history: list[dict],
        message: str,
        tools: list[dict] | None = None,
        use_opus: bool = False,
        image_base64: str | None = None,
        image_media_type: str = 'image/png',
        raw_messages: list[dict] | None = None,
    ) -> tuple[str, Optional[dict], dict]:
        """
        Send a chat message and return (response_text, tool_call, usage).
        tool_call is {'name': str, 'input': dict} or None.
        usage is {'input_tokens': int, 'output_tokens': int}.
        use_opus: override to Opus for a specific request (architecture,
                  security review, complex debugging).
        """
        model = self.opus_model if use_opus else self.model
        messages = (
            raw_messages if raw_messages is not None
            else self._build_messages(history, message, image_base64, image_media_type)
        )

        kwargs: dict = {
            'model': model,
            'max_tokens': 8096,
            'system': system,
            'messages': messages,
        }

        if tools:
            kwargs['tools'] = tools
            # auto: Claude decides when to use a tool.
            # Without this explicit setting some model versions default to
            # generating text even when a relevant tool is available.
            kwargs['tool_choice'] = {'type': 'auto'}

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

    def build_messages(
        self,
        system: str,  # ignored — Claude receives system separately via the API
        history: list[dict],
        message: str,
        image_base64: str | None = None,
        image_media_type: str = 'image/png',
    ) -> list[dict]:
        """Public accessor so agent.py can build the initial messages list
        before starting the tool loop."""
        return self._build_messages(history, message, image_base64, image_media_type)

    @staticmethod
    def append_tool_round(
        messages: list[dict],
        response_text: str,
        tool_call: dict,
        tool_result: str,
    ) -> list[dict]:
        """
        Append an assistant tool_use turn + tool_result turn to the messages
        list. Returns a new list; does not mutate the original.

        Produces Anthropic's native multi-turn tool-use format:
            assistant: [{type:tool_use, id:..., name:..., input:...}]
            user:      [{type:tool_result, tool_use_id:..., content:...}]
        """
        content: list = []
        if response_text:
            content.append({'type': 'text', 'text': response_text})
        content.append({
            'type': 'tool_use',
            'id': tool_call['tool_use_id'],
            'name': tool_call['name'],
            'input': tool_call['input'],
        })
        return messages + [
            {'role': 'assistant', 'content': content},
            {
                'role': 'user',
                'content': [{
                    'type': 'tool_result',
                    'tool_use_id': tool_call['tool_use_id'],
                    'content': tool_result,
                }],
            },
        ]

    def _build_messages(
        self,
        history: list[dict],
        message: str,
        image_base64: str | None = None,
        image_media_type: str = 'image/png',
    ) -> list[dict]:
        messages = []
        for h in history:
            role = h.get('role', 'user')
            if role in ('user', 'assistant'):
                messages.append({'role': role, 'content': h['content']})

        if image_base64:
            content = [
                {
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': image_media_type,
                        'data': image_base64,
                    },
                },
                {'type': 'text', 'text': message},
            ]
        else:
            content = message

        messages.append({'role': 'user', 'content': content})
        return messages
