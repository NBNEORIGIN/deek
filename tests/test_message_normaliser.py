"""
Tests for cross-provider message normalisation.

Covers Anthropic <-> OpenAI format conversions for tool_use/tool_calls,
tool_result/role:tool, mixed content blocks, format detection,
and belt-and-braces client normalisation.
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core.models.message_normaliser import MessageNormaliser


@pytest.fixture
def normaliser():
    return MessageNormaliser()


# ── Sample messages in each format ───────────────────────────────────────────

ANTHROPIC_TOOL_USE_MSG = {
    'role': 'assistant',
    'content': [
        {'type': 'text', 'text': 'Let me read that file.'},
        {
            'type': 'tool_use',
            'id': 'toolu_abc123',
            'name': 'read_file',
            'input': {'file_path': '/src/main.py'},
        },
    ],
}

ANTHROPIC_TOOL_RESULT_MSG = {
    'role': 'user',
    'content': [
        {
            'type': 'tool_result',
            'tool_use_id': 'toolu_abc123',
            'content': 'def main():\n    print("hello")',
        },
    ],
}

OPENAI_TOOL_CALLS_MSG = {
    'role': 'assistant',
    'content': 'Let me read that file.',
    'tool_calls': [
        {
            'id': 'call_xyz789',
            'type': 'function',
            'function': {
                'name': 'read_file',
                'arguments': json.dumps({'file_path': '/src/main.py'}),
            },
        },
    ],
}

OPENAI_TOOL_RESULT_MSG = {
    'role': 'tool',
    'tool_call_id': 'call_xyz789',
    'content': 'def main():\n    print("hello")',
}


# ── Format detection ────────────────────────────────────────────────────────

class TestDetectFormat:

    def test_normaliser_detect_format_anthropic(self, normaliser):
        messages = [ANTHROPIC_TOOL_USE_MSG, ANTHROPIC_TOOL_RESULT_MSG]
        assert normaliser.detect_format(messages) == 'anthropic'

    def test_normaliser_detect_format_openai(self, normaliser):
        messages = [OPENAI_TOOL_CALLS_MSG, OPENAI_TOOL_RESULT_MSG]
        assert normaliser.detect_format(messages) == 'openai'

    def test_normaliser_detect_format_plain(self, normaliser):
        messages = [
            {'role': 'user', 'content': 'Hello'},
            {'role': 'assistant', 'content': 'Hi there'},
        ]
        assert normaliser.detect_format(messages) == 'plain'

    def test_normaliser_detect_format_mixed(self, normaliser):
        messages = [ANTHROPIC_TOOL_USE_MSG, OPENAI_TOOL_RESULT_MSG]
        assert normaliser.detect_format(messages) == 'mixed'

    def test_detect_format_empty(self, normaliser):
        assert normaliser.detect_format([]) == 'plain'


# ── Anthropic → OpenAI ──────────────────────────────────────────────────────

class TestAnthropicToOpenAI:

    def test_normaliser_tool_use_to_openai(self, normaliser):
        """Anthropic tool_use block → OpenAI tool_calls array with json.dumps arguments."""
        result = normaliser.to_openai([ANTHROPIC_TOOL_USE_MSG])

        assert len(result) == 1
        msg = result[0]
        assert msg['role'] == 'assistant'
        assert msg['content'] == 'Let me read that file.'
        assert 'tool_calls' in msg
        tc = msg['tool_calls'][0]
        assert tc['id'] == 'toolu_abc123'
        assert tc['type'] == 'function'
        assert tc['function']['name'] == 'read_file'
        assert json.loads(tc['function']['arguments']) == {'file_path': '/src/main.py'}

    def test_normaliser_tool_result_to_openai(self, normaliser):
        """Anthropic tool_result in user content → OpenAI role:tool message."""
        result = normaliser.to_openai([ANTHROPIC_TOOL_RESULT_MSG])

        assert len(result) == 1
        msg = result[0]
        assert msg['role'] == 'tool'
        assert msg['tool_call_id'] == 'toolu_abc123'
        assert 'def main()' in msg['content']

    def test_normaliser_handles_mixed_content_blocks(self, normaliser):
        """Assistant message with text + multiple tool_use blocks."""
        messages = [
            {
                'role': 'assistant',
                'content': [
                    {'type': 'text', 'text': 'I will search and read.'},
                    {
                        'type': 'tool_use',
                        'id': 'tu_1',
                        'name': 'search_code',
                        'input': {'query': 'booking'},
                    },
                    {
                        'type': 'tool_use',
                        'id': 'tu_2',
                        'name': 'read_file',
                        'input': {'file_path': 'app.py'},
                    },
                ],
            },
        ]
        result = normaliser.to_openai(messages)
        msg = result[0]
        assert msg['content'] == 'I will search and read.'
        assert len(msg['tool_calls']) == 2
        assert msg['tool_calls'][0]['function']['name'] == 'search_code'
        assert msg['tool_calls'][1]['function']['name'] == 'read_file'

    def test_normaliser_plain_string_passthrough_openai(self, normaliser):
        """Messages with plain string content pass through unchanged."""
        messages = [
            {'role': 'user', 'content': 'Hello'},
            {'role': 'assistant', 'content': 'Hi there'},
        ]
        result = normaliser.to_openai(messages)
        assert result == messages

    def test_normaliser_deepseek_delegates_to_openai(self, normaliser):
        messages = [ANTHROPIC_TOOL_USE_MSG, ANTHROPIC_TOOL_RESULT_MSG]
        openai_result = normaliser.to_openai(messages)
        deepseek_result = normaliser.to_deepseek(messages)
        assert openai_result == deepseek_result

    def test_normaliser_already_correct_format_passthrough(self, normaliser):
        """Already-OpenAI messages pass through to_openai unchanged."""
        messages = [
            {'role': 'user', 'content': 'hello'},
            OPENAI_TOOL_CALLS_MSG,
            OPENAI_TOOL_RESULT_MSG,
        ]
        result = normaliser.to_openai(messages)
        assert result == messages

    def test_normaliser_empty_messages_no_exception(self, normaliser):
        assert normaliser.to_openai([]) == []
        assert normaliser.to_anthropic([]) == []
        assert normaliser.to_deepseek([]) == []


# ── OpenAI → Anthropic ──────────────────────────────────────────────────────

class TestOpenAIToAnthropic:

    def test_normaliser_tool_calls_to_anthropic(self, normaliser):
        """OpenAI tool_calls → Anthropic tool_use blocks in content list."""
        result = normaliser.to_anthropic([OPENAI_TOOL_CALLS_MSG])

        assert len(result) == 1
        msg = result[0]
        assert msg['role'] == 'assistant'
        content = msg['content']
        assert isinstance(content, list)

        text_block = content[0]
        assert text_block['type'] == 'text'
        assert text_block['text'] == 'Let me read that file.'

        tool_block = content[1]
        assert tool_block['type'] == 'tool_use'
        assert tool_block['id'] == 'call_xyz789'
        assert tool_block['name'] == 'read_file'
        assert tool_block['input'] == {'file_path': '/src/main.py'}

    def test_normaliser_tool_message_to_anthropic(self, normaliser):
        """OpenAI role:tool message → Anthropic user message with tool_result block."""
        result = normaliser.to_anthropic([OPENAI_TOOL_RESULT_MSG])

        assert len(result) == 1
        msg = result[0]
        assert msg['role'] == 'user'
        assert isinstance(msg['content'], list)
        block = msg['content'][0]
        assert block['type'] == 'tool_result'
        assert block['tool_use_id'] == 'call_xyz789'
        assert 'def main()' in block['content']

    def test_consecutive_tool_results_merged(self, normaliser):
        """Multiple consecutive role:tool messages become one user turn."""
        messages = [
            {'role': 'tool', 'tool_call_id': 'c1', 'content': 'result1'},
            {'role': 'tool', 'tool_call_id': 'c2', 'content': 'result2'},
        ]
        result = normaliser.to_anthropic(messages)
        assert len(result) == 1
        assert result[0]['role'] == 'user'
        assert len(result[0]['content']) == 2

    def test_normaliser_plain_string_passthrough_anthropic(self, normaliser):
        """Plain string messages pass through to_anthropic unchanged."""
        messages = [
            {'role': 'user', 'content': 'Hello'},
            {'role': 'assistant', 'content': 'Hi there'},
        ]
        result = normaliser.to_anthropic(messages)
        assert result == messages

    def test_normaliser_already_anthropic_passthrough(self, normaliser):
        """Already-Anthropic messages pass through to_anthropic unchanged."""
        messages = [
            {'role': 'user', 'content': 'hello'},
            ANTHROPIC_TOOL_USE_MSG,
            ANTHROPIC_TOOL_RESULT_MSG,
        ]
        result = normaliser.to_anthropic(messages)
        assert result == messages

    def test_normaliser_mixed_content_no_exception(self, normaliser):
        """Mixed Anthropic + OpenAI messages don't crash either direction."""
        messages = [ANTHROPIC_TOOL_USE_MSG, OPENAI_TOOL_RESULT_MSG]
        # Should not raise
        normaliser.to_openai(messages)
        normaliser.to_anthropic(messages)


# ── Full conversation round-trips ───────────────────────────────────────────

class TestFullConversation:

    def test_normaliser_full_conversation_anthropic_to_openai(self, normaliser):
        """
        Full multi-turn conversation with tool use.
        Convert to OpenAI format — verify no 400-causing blocks remain.
        """
        anthropic_history = [
            {'role': 'system', 'content': 'System prompt'},
            {'role': 'user', 'content': 'Fix the bug in app.py'},
            {
                'role': 'assistant',
                'content': [
                    {'type': 'text', 'text': 'Reading the file.'},
                    {
                        'type': 'tool_use',
                        'id': 'tu_001',
                        'name': 'read_file',
                        'input': {'file_path': 'app.py'},
                    },
                ],
            },
            {
                'role': 'user',
                'content': [
                    {
                        'type': 'tool_result',
                        'tool_use_id': 'tu_001',
                        'content': 'import flask\n\napp = flask.Flask(__name__)',
                    },
                ],
            },
            {
                'role': 'assistant',
                'content': [
                    {'type': 'text', 'text': 'Now editing.'},
                    {
                        'type': 'tool_use',
                        'id': 'tu_002',
                        'name': 'edit_file',
                        'input': {'file_path': 'app.py', 'old_str': 'import flask', 'new_str': 'import flask\nimport logging'},
                    },
                ],
            },
            {
                'role': 'user',
                'content': [
                    {
                        'type': 'tool_result',
                        'tool_use_id': 'tu_002',
                        'content': 'File edited successfully.',
                    },
                ],
            },
        ]

        openai_msgs = normaliser.to_openai(anthropic_history)

        # No Anthropic-style content blocks should remain
        for msg in openai_msgs:
            content = msg.get('content')
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        assert block.get('type') not in ('tool_use', 'tool_result'), \
                            f"Anthropic block leaked: {block}"

        # Should have 2 tool messages and 2 assistant messages with tool_calls
        tool_msgs = [m for m in openai_msgs if m.get('role') == 'tool']
        assert len(tool_msgs) == 2
        asst_with_tools = [m for m in openai_msgs if m.get('tool_calls')]
        assert len(asst_with_tools) == 2

    def test_normaliser_full_conversation_openai_to_anthropic(self, normaliser):
        """Reverse of above — OpenAI format to Anthropic."""
        openai_history = [
            {'role': 'user', 'content': 'Search for booking code'},
            {
                'role': 'assistant',
                'content': 'Searching.',
                'tool_calls': [
                    {
                        'id': 'call_001',
                        'type': 'function',
                        'function': {
                            'name': 'search_code',
                            'arguments': json.dumps({'query': 'booking'}),
                        },
                    },
                ],
            },
            {
                'role': 'tool',
                'tool_call_id': 'call_001',
                'content': 'Found 3 matches in views.py',
            },
        ]

        anthropic_msgs = normaliser.to_anthropic(openai_history)

        # No OpenAI-specific format should remain
        for msg in anthropic_msgs:
            assert msg.get('role') != 'tool', f"OpenAI tool role leaked: {msg}"
            assert 'tool_calls' not in msg, f"OpenAI tool_calls leaked: {msg}"

        # Should have tool_use in assistant and tool_result in user
        asst = anthropic_msgs[1]
        assert any(
            b.get('type') == 'tool_use'
            for b in asst.get('content', [])
            if isinstance(b, dict)
        )
        user = anthropic_msgs[2]
        assert any(
            b.get('type') == 'tool_result'
            for b in user.get('content', [])
            if isinstance(b, dict)
        )


# ── Edge cases ──────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_tool_result_with_list_content(self, normaliser):
        """Anthropic tool_result content can be a list of text blocks."""
        msg = {
            'role': 'user',
            'content': [
                {
                    'type': 'tool_result',
                    'tool_use_id': 'tu_x',
                    'content': [
                        {'type': 'text', 'text': 'Line 1'},
                        {'type': 'text', 'text': 'Line 2'},
                    ],
                },
            ],
        }
        result = normaliser.to_openai([msg])
        assert result[0]['role'] == 'tool'
        assert 'Line 1' in result[0]['content']
        assert 'Line 2' in result[0]['content']

    def test_assistant_only_tool_use_no_text(self, normaliser):
        """Assistant message with tool_use but no text block."""
        msg = {
            'role': 'assistant',
            'content': [
                {
                    'type': 'tool_use',
                    'id': 'tu_only',
                    'name': 'git_status',
                    'input': {},
                },
            ],
        }
        result = normaliser.to_openai([msg])
        assert result[0]['content'] is None
        assert len(result[0]['tool_calls']) == 1

    def test_malformed_arguments_handled(self, normaliser):
        """OpenAI tool_calls with unparseable arguments don't crash."""
        msg = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'id': 'c1',
                    'type': 'function',
                    'function': {
                        'name': 'test',
                        'arguments': 'not-valid-json',
                    },
                },
            ],
        }
        result = normaliser.to_anthropic([msg])
        content = result[0]['content']
        tool_block = next(b for b in content if b.get('type') == 'tool_use')
        assert tool_block['input'] == {'raw': 'not-valid-json'}

    def test_none_content_passthrough(self, normaliser):
        """Messages with None content pass through."""
        msg = {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'c1', 'type': 'function', 'function': {'name': 'test', 'arguments': '{}'}}
        ]}
        # to_openai should pass through (already OpenAI format)
        result = normaliser.to_openai([msg])
        assert result[0] is msg


# ── Belt-and-braces client tests ────────────────────────────────────────────

class TestClientNormalisation:

    def test_claude_client_normalises_on_receipt(self):
        """ClaudeClient source has normaliser import and to_anthropic call."""
        source = Path('core/models/claude_client.py').read_text()
        assert 'message_normaliser' in source
        assert 'to_anthropic' in source

    def test_openai_client_normalises_on_receipt(self):
        """OpenAIClient source has normaliser import and to_openai call."""
        source = Path('core/models/openai_client.py').read_text()
        assert 'message_normaliser' in source
        assert 'to_openai' in source

    def test_deepseek_client_normalises_on_receipt(self):
        """DeepSeekClient source has normaliser import and to_openai call."""
        source = Path('core/models/deepseek_client.py').read_text()
        assert 'message_normaliser' in source
        assert 'to_openai' in source
