"""
Cross-provider message normaliser.

When a model call fails mid-conversation and falls back to a different
provider (e.g. Claude → OpenAI), the accumulated message history contains
tool-use blocks in the original provider's format. The target provider
rejects these with a 400.

This module converts messages between Anthropic and OpenAI formats so
fallback works cleanly.
"""
from __future__ import annotations

import json
from typing import Any


class MessageNormaliser:

    def to_openai(self, messages: list[dict]) -> list[dict]:
        """
        Convert Anthropic-format messages to OpenAI format.

        Key conversions:
          - tool_use blocks in assistant content → tool_calls array
          - tool_result blocks in user content → role:tool messages
          - content as list of text blocks → content as string
        """
        result: list[dict] = []
        for msg in messages:
            role = msg.get('role', '')
            content = msg.get('content')

            if role == 'assistant' and isinstance(content, list):
                converted = self._convert_assistant_anthropic_to_openai(msg)
                result.append(converted)

            elif role == 'user' and isinstance(content, list):
                tool_msgs, user_msg = self._convert_user_anthropic_to_openai(msg)
                result.extend(tool_msgs)
                if user_msg is not None:
                    result.append(user_msg)

            else:
                result.append(msg)

        return result

    def to_anthropic(self, messages: list[dict]) -> list[dict]:
        """
        Convert OpenAI-format messages to Anthropic format.

        Key conversions:
          - tool_calls array on assistant → tool_use content blocks
          - role:tool messages → tool_result content blocks on a user turn
          - system messages are passed through (caller handles separately)
        """
        result: list[dict] = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            role = msg.get('role', '')

            if role == 'assistant' and 'tool_calls' in msg:
                result.append(self._convert_assistant_openai_to_anthropic(msg))
                i += 1

            elif role == 'tool':
                # Collect consecutive tool messages into one user turn
                tool_results: list[dict] = []
                while i < len(messages) and messages[i].get('role') == 'tool':
                    tool_results.append(
                        self._openai_tool_to_anthropic_result(messages[i])
                    )
                    i += 1
                result.append({'role': 'user', 'content': tool_results})

            else:
                result.append(msg)
                i += 1

        return result

    def to_deepseek(self, messages: list[dict]) -> list[dict]:
        """DeepSeek is OpenAI-compatible."""
        return self.to_openai(messages)

    # ------------------------------------------------------------------
    # Anthropic → OpenAI helpers
    # ------------------------------------------------------------------

    def _convert_assistant_anthropic_to_openai(self, msg: dict) -> dict:
        """
        Convert assistant message with Anthropic content blocks to OpenAI format.

        {role: assistant, content: [{type: text, ...}, {type: tool_use, ...}]}
        →
        {role: assistant, content: "text", tool_calls: [...]}
        """
        content = msg.get('content', [])
        text_parts: list[str] = []
        tool_calls: list[dict] = []

        for block in content:
            if not isinstance(block, dict):
                text_parts.append(str(block))
                continue
            btype = block.get('type', '')
            if btype == 'text':
                text_parts.append(block.get('text', ''))
            elif btype == 'tool_use':
                tool_calls.append(self._anthropic_tool_use_to_openai(block))

        result: dict[str, Any] = {
            'role': 'assistant',
            'content': '\n'.join(text_parts) if text_parts else '',
        }
        if tool_calls:
            result['tool_calls'] = tool_calls
        return result

    def _convert_user_anthropic_to_openai(
        self, msg: dict,
    ) -> tuple[list[dict], dict | None]:
        """
        Convert user message containing tool_result blocks.

        {role: user, content: [{type: tool_result, tool_use_id: X, content: Y}]}
        →
        [{role: tool, tool_call_id: X, content: Y}]

        Any non-tool_result blocks are kept as a separate user message.
        """
        content = msg.get('content', [])
        tool_msgs: list[dict] = []
        text_parts: list[str] = []

        for block in content:
            if not isinstance(block, dict):
                text_parts.append(str(block))
                continue
            btype = block.get('type', '')
            if btype == 'tool_result':
                tool_msgs.append(self._anthropic_tool_result_to_openai(block))
            elif btype == 'text':
                text_parts.append(block.get('text', ''))
            else:
                text_parts.append(str(block))

        user_msg = None
        if text_parts:
            joined = '\n'.join(text_parts).strip()
            if joined:
                user_msg = {'role': 'user', 'content': joined}

        return tool_msgs, user_msg

    def _anthropic_tool_use_to_openai(self, block: dict) -> dict:
        """
        {type: tool_use, id: X, name: Y, input: Z}
        →
        {id: X, type: function, function: {name: Y, arguments: json.dumps(Z)}}
        """
        return {
            'id': block.get('id', ''),
            'type': 'function',
            'function': {
                'name': block.get('name', ''),
                'arguments': json.dumps(block.get('input', {})),
            },
        }

    def _anthropic_tool_result_to_openai(self, block: dict) -> dict:
        """
        {type: tool_result, tool_use_id: X, content: Y}
        →
        {role: tool, tool_call_id: X, content: Y}
        """
        content = block.get('content', '')
        if isinstance(content, list):
            # tool_result content can be a list of blocks
            parts = []
            for c in content:
                if isinstance(c, dict) and c.get('type') == 'text':
                    parts.append(c.get('text', ''))
                else:
                    parts.append(str(c))
            content = '\n'.join(parts)
        return {
            'role': 'tool',
            'tool_call_id': block.get('tool_use_id', ''),
            'content': str(content),
        }

    # ------------------------------------------------------------------
    # OpenAI → Anthropic helpers
    # ------------------------------------------------------------------

    def _convert_assistant_openai_to_anthropic(self, msg: dict) -> dict:
        """
        {role: assistant, content: "text", tool_calls: [...]}
        →
        {role: assistant, content: [{type: text, ...}, {type: tool_use, ...}]}
        """
        content_blocks: list[dict] = []
        text = msg.get('content', '') or ''
        if text:
            content_blocks.append({'type': 'text', 'text': text})

        for tc in msg.get('tool_calls', []):
            func = tc.get('function', {})
            args = func.get('arguments', '{}')
            try:
                parsed_args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                parsed_args = {'raw': args}
            content_blocks.append({
                'type': 'tool_use',
                'id': tc.get('id', ''),
                'name': func.get('name', ''),
                'input': parsed_args,
            })

        return {'role': 'assistant', 'content': content_blocks}

    def _openai_tool_to_anthropic_result(self, msg: dict) -> dict:
        """
        {role: tool, tool_call_id: X, content: Y}
        →
        {type: tool_result, tool_use_id: X, content: Y}
        """
        return {
            'type': 'tool_result',
            'tool_use_id': msg.get('tool_call_id', ''),
            'content': msg.get('content', ''),
        }
