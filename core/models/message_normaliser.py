"""
Cross-provider message normalisation.

Anthropic and OpenAI use incompatible message formats
for tool calls. When the fallback path switches providers
mid-conversation, messages must be normalised to the
target provider's format before the API call.

Anthropic format:
  Assistant message with tool_use:
  {
    "role": "assistant",
    "content": [
      {"type": "text", "text": "..."},
      {
        "type": "tool_use",
        "id": "toolu_abc",
        "name": "read_file",
        "input": {"file_path": "core/agent.py"}
      }
    ]
  }

  Tool result (user turn):
  {
    "role": "user",
    "content": [
      {
        "type": "tool_result",
        "tool_use_id": "toolu_abc",
        "content": "file contents here"
      }
    ]
  }

OpenAI format:
  Assistant message with tool_calls:
  {
    "role": "assistant",
    "content": null,
    "tool_calls": [
      {
        "id": "call_abc",
        "type": "function",
        "function": {
          "name": "read_file",
          "arguments": "{\"file_path\": \"core/agent.py\"}"
        }
      }
    ]
  }

  Tool result:
  {
    "role": "tool",
    "tool_call_id": "call_abc",
    "content": "file contents here"
  }
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class MessageNormaliser:
    """
    Normalises message lists between provider formats.
    All methods are pure — no side effects, no API calls.
    Input messages are never mutated — always returns new list.
    """

    def to_openai(self, messages: list[dict]) -> list[dict]:
        """
        Convert message list to OpenAI format.
        Safe to call on already-OpenAI-format messages.
        Safe to call on mixed-format messages.
        """
        normalised: list[dict] = []
        for msg in messages:
            converted = self._convert_message_to_openai(msg)
            if isinstance(converted, list):
                normalised.extend(converted)
            else:
                normalised.append(converted)
        return normalised

    def to_anthropic(self, messages: list[dict]) -> list[dict]:
        """
        Convert message list to Anthropic format.
        Safe to call on already-Anthropic-format messages.
        Safe to call on mixed-format messages.
        """
        normalised: list[dict] = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            converted = self._convert_message_to_anthropic(msg)
            if isinstance(converted, list):
                normalised.extend(converted)
                i += 1
            elif msg.get('role') == 'tool':
                # Collect consecutive tool messages into one user turn
                tool_results: list[dict] = []
                while i < len(messages) and messages[i].get('role') == 'tool':
                    tool_results.append(
                        self._openai_tool_to_anthropic_result(messages[i])
                    )
                    i += 1
                normalised.append({'role': 'user', 'content': tool_results})
            else:
                normalised.append(converted)
                i += 1
        return normalised

    def to_deepseek(self, messages: list[dict]) -> list[dict]:
        """DeepSeek is OpenAI-compatible."""
        return self.to_openai(messages)

    def detect_format(self, messages: list[dict]) -> str:
        """
        Detect which format the message list is in.
        Returns: 'anthropic' | 'openai' | 'mixed' | 'plain'
        """
        has_anthropic = False
        has_openai = False

        for msg in messages:
            content = msg.get('content', '')

            # Anthropic indicators
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get('type') in ('tool_use', 'tool_result'):
                            has_anthropic = True

            # OpenAI indicators
            if msg.get('tool_calls'):
                has_openai = True
            if msg.get('role') == 'tool':
                has_openai = True

        if has_anthropic and has_openai:
            return 'mixed'
        elif has_anthropic:
            return 'anthropic'
        elif has_openai:
            return 'openai'
        return 'plain'

    # ------------------------------------------------------------------
    # Single-message converters
    # ------------------------------------------------------------------

    def _convert_message_to_openai(self, msg: dict) -> dict | list[dict]:
        """
        Convert a single message to OpenAI format.
        May return a list if one message expands to multiple
        (e.g. Anthropic tool_result in user turn).
        """
        role = msg.get('role', '')
        content = msg.get('content')

        # Already OpenAI format — pass through
        if msg.get('tool_calls') or role == 'tool':
            return msg

        # Plain string content — pass through
        if isinstance(content, str) or content is None:
            return msg

        # Anthropic content list
        if isinstance(content, list):
            return self._anthropic_content_to_openai(role, content, msg)

        return msg

    def _convert_message_to_anthropic(self, msg: dict) -> dict | list[dict]:
        """
        Convert a single message to Anthropic format.
        Returns the message unchanged if already Anthropic format.
        """
        role = msg.get('role', '')
        content = msg.get('content')

        # Already Anthropic format (content is list) — pass through
        if isinstance(content, list):
            return msg

        # OpenAI tool message → handled by caller (consecutive grouping)
        if role == 'tool':
            # Signal to caller — don't convert here, let caller group
            return msg

        # OpenAI assistant with tool_calls → Anthropic tool_use blocks
        if msg.get('tool_calls'):
            content_blocks: list[dict] = []
            text = msg.get('content')
            if text:
                content_blocks.append({'type': 'text', 'text': str(text)})
            for tc in msg['tool_calls']:
                fn = tc.get('function', {})
                args = fn.get('arguments', '{}')
                try:
                    input_data = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    input_data = {'raw': args}
                content_blocks.append({
                    'type': 'tool_use',
                    'id': tc.get('id', ''),
                    'name': fn.get('name', ''),
                    'input': input_data,
                })
            return {'role': 'assistant', 'content': content_blocks}

        # Plain string — pass through as-is (Anthropic accepts strings)
        return msg

    # ------------------------------------------------------------------
    # Anthropic → OpenAI helpers
    # ------------------------------------------------------------------

    def _anthropic_content_to_openai(
        self,
        role: str,
        content: list,
        original: dict,
    ) -> dict | list[dict]:
        """
        Convert Anthropic content block list to OpenAI format.

        Assistant messages with tool_use blocks become
        OpenAI assistant messages with tool_calls.

        User messages with tool_result blocks become
        OpenAI tool messages (one per tool result).
        """
        text_blocks: list[str] = []
        tool_use_blocks: list[dict] = []
        tool_result_blocks: list[dict] = []

        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get('type', '')
            if block_type == 'text':
                text_blocks.append(block.get('text', ''))
            elif block_type == 'tool_use':
                tool_use_blocks.append(block)
            elif block_type == 'tool_result':
                tool_result_blocks.append(block)

        # Tool results → OpenAI tool messages
        if tool_result_blocks:
            result_messages: list[dict] = []
            for tr in tool_result_blocks:
                tool_content = tr.get('content', '')
                if isinstance(tool_content, list):
                    tool_content = ' '.join(
                        b.get('text', '')
                        for b in tool_content
                        if isinstance(b, dict)
                    )
                result_messages.append({
                    'role': 'tool',
                    'tool_call_id': tr.get('tool_use_id', 'unknown'),
                    'content': str(tool_content),
                })
            return result_messages

        # Tool use → OpenAI tool_calls
        if tool_use_blocks:
            tool_calls: list[dict] = []
            for tu in tool_use_blocks:
                tool_calls.append({
                    'id': tu.get('id', 'unknown'),
                    'type': 'function',
                    'function': {
                        'name': tu.get('name', ''),
                        'arguments': json.dumps(tu.get('input', {})),
                    },
                })
            text_content = ' '.join(text_blocks).strip()
            return {
                'role': 'assistant',
                'content': text_content or None,
                'tool_calls': tool_calls,
            }

        # Plain text content blocks — collapse to string
        text = ' '.join(text_blocks).strip()
        return {'role': role, 'content': text}

    # ------------------------------------------------------------------
    # OpenAI → Anthropic helpers
    # ------------------------------------------------------------------

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
