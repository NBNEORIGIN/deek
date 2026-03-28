"""
Tests for cross-provider message normalisation.

Covers Anthropic ↔ OpenAI format conversions for tool_use/tool_calls,
tool_result/role:tool, and mixed content blocks.
"""
import json
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


# ── Anthropic → OpenAI ──────────────────────────────────────────────────────

class TestAnthropicToOpenAI:

    def test_normaliser_tool_use_to_openai_format(self, normaliser):
        messages = [ANTHROPIC_TOOL_USE_MSG]
        result = normaliser.to_openai(messages)

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

    def test_normaliser_tool_result_to_openai_format(self, normaliser):
        messages = [ANTHROPIC_TOOL_RESULT_MSG]
        result = normaliser.to_openai(messages)

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

    def test_normaliser_handles_plain_string_content(self, normaliser):
        """Messages with plain string content pass through unchanged."""
        messages = [
            {'role': 'user', 'content': 'Hello'},
            {'role': 'assistant', 'content': 'Hi there'},
        ]
        result = normaliser.to_openai(messages)
        assert result == messages

    def test_normaliser_deepseek_uses_openai_format(self, normaliser):
        messages = [ANTHROPIC_TOOL_USE_MSG, ANTHROPIC_TOOL_RESULT_MSG]
        openai_result = normaliser.to_openai(messages)
        deepseek_result = normaliser.to_deepseek(messages)
        assert openai_result == deepseek_result

    def test_full_tool_round_anthropic_to_openai(self, normaliser):
        """Full conversation with tool use converts correctly."""
        messages = [
            {'role': 'system', 'content': 'You are a helper.'},
            {'role': 'user', 'content': 'Read main.py'},
            ANTHROPIC_TOOL_USE_MSG,
            ANTHROPIC_TOOL_RESULT_MSG,
            {'role': 'assistant', 'content': 'The file contains a main function.'},
        ]
        result = normaliser.to_openai(messages)

        # system and plain messages pass through
        assert result[0] == {'role': 'system', 'content': 'You are a helper.'}
        assert result[1] == {'role': 'user', 'content': 'Read main.py'}

        # tool_use converted
        assert 'tool_calls' in result[2]
        assert result[2]['role'] == 'assistant'

        # tool_result converted
        assert result[3]['role'] == 'tool'

        # plain assistant passes through
        assert result[4]['content'] == 'The file contains a main function.'


# ── OpenAI → Anthropic ──────────────────────────────────────────────────────

class TestOpenAIToAnthropic:

    def test_tool_calls_to_anthropic_format(self, normaliser):
        messages = [OPENAI_TOOL_CALLS_MSG]
        result = normaliser.to_anthropic(messages)

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

    def test_tool_result_to_anthropic_format(self, normaliser):
        messages = [OPENAI_TOOL_RESULT_MSG]
        result = normaliser.to_anthropic(messages)

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

    def test_full_tool_round_openai_to_anthropic(self, normaliser):
        messages = [
            {'role': 'user', 'content': 'Read main.py'},
            OPENAI_TOOL_CALLS_MSG,
            OPENAI_TOOL_RESULT_MSG,
            {'role': 'assistant', 'content': 'Done.'},
        ]
        result = normaliser.to_anthropic(messages)

        assert result[0] == {'role': 'user', 'content': 'Read main.py'}
        assert result[1]['role'] == 'assistant'
        assert result[1]['content'][1]['type'] == 'tool_use'
        assert result[2]['role'] == 'user'
        assert result[2]['content'][0]['type'] == 'tool_result'
        assert result[3] == {'role': 'assistant', 'content': 'Done.'}


# ── Fallback scenario integration ───────────────────────────────────────────

class TestFallbackScenario:

    def test_fallback_no_400_on_anthropic_to_openai(self, normaliser):
        """
        Simulate Claude tool loop history passed to OpenAI fallback.
        Verify no Anthropic-specific blocks remain.
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

        # No message should have Anthropic-style content blocks
        for msg in openai_msgs:
            content = msg.get('content')
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        assert block.get('type') not in ('tool_use', 'tool_result'), \
                            f"Anthropic block leaked through: {block}"
            assert msg.get('role') != 'user' or not isinstance(content, list), \
                f"User message still has list content with tool blocks: {msg}"

        # Should have tool messages with role:tool
        tool_msgs = [m for m in openai_msgs if m.get('role') == 'tool']
        assert len(tool_msgs) == 2

        # Should have assistant messages with tool_calls
        asst_with_tools = [m for m in openai_msgs if m.get('tool_calls')]
        assert len(asst_with_tools) == 2

    def test_fallback_no_400_on_openai_to_anthropic(self, normaliser):
        """
        Simulate OpenAI tool loop history passed to Claude fallback.
        Verify no OpenAI-specific blocks remain.
        """
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

        # No message should have role:tool
        for msg in anthropic_msgs:
            assert msg.get('role') != 'tool', f"OpenAI tool role leaked: {msg}"

        # No message should have tool_calls key
        for msg in anthropic_msgs:
            assert 'tool_calls' not in msg, f"OpenAI tool_calls leaked: {msg}"

        # Should have tool_use block in assistant
        asst = anthropic_msgs[1]
        assert any(
            b.get('type') == 'tool_use'
            for b in asst.get('content', [])
            if isinstance(b, dict)
        )

        # Should have tool_result block in user
        user = anthropic_msgs[2]
        assert any(
            b.get('type') == 'tool_result'
            for b in user.get('content', [])
            if isinstance(b, dict)
        )


class TestEdgeCases:

    def test_empty_messages(self, normaliser):
        assert normaliser.to_openai([]) == []
        assert normaliser.to_anthropic([]) == []
        assert normaliser.to_deepseek([]) == []

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
        assert result[0]['content'] == ''
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
