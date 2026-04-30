"""Regression tests for DeepSeek's DSML tool-call markup parsing.

Two on-the-wire variants observed:

  * single pipe + ``function_calls`` — the original DeepSeek V3 format
  * double pipe + ``tool_calls`` — later model revisions (2026-04-30
    bug surfaced in a Toby chat about personalised revenue: every turn
    after the first leaked raw <｜｜DSML｜｜tool_calls> blocks because
    the parser only matched the single-pipe variant).

These tests pin both formats so a future model revision that drops one
or the other doesn't silently regress the parser.
"""
from __future__ import annotations

from core.models.deepseek_client import (
    _has_dsml_markup,
    _parse_dsml_tool_call,
)


# ── single-pipe + function_calls ────────────────────────────────────


SINGLE_PIPE_SAMPLE = (
    'Let me look up that file.\n\n'
    '<｜DSML｜function_calls>\n'
    '<｜DSML｜invoke name="read_file">\n'
    '<｜DSML｜parameter name="file_path" string="true">.env</｜DSML｜parameter>\n'
    '</｜DSML｜invoke>\n'
    '</｜DSML｜function_calls>'
)


def test_single_pipe_detected():
    assert _has_dsml_markup(SINGLE_PIPE_SAMPLE) is True


def test_single_pipe_parses_to_tool_call():
    clean, tc = _parse_dsml_tool_call(SINGLE_PIPE_SAMPLE)
    assert clean == 'Let me look up that file.'
    assert tc is not None
    assert tc['name'] == 'read_file'
    assert tc['input'] == {'file_path': '.env'}
    assert tc['tool_use_id'].startswith('dsml-')


# ── double-pipe + tool_calls (the 2026-04-30 regression) ────────────


DOUBLE_PIPE_SAMPLE = (
    'Now let me check Etsy.\n\n'
    '<｜｜DSML｜｜tool_calls>\n'
    '<｜｜DSML｜｜invoke name="search_wiki">\n'
    '<｜｜DSML｜｜parameter name="query" string="true">'
    'etsy personalised products revenue 2026'
    '</｜｜DSML｜｜parameter>\n'
    '<｜｜DSML｜｜parameter name="limit" string="false">5'
    '</｜｜DSML｜｜parameter>\n'
    '</｜｜DSML｜｜invoke>\n'
    '</｜｜DSML｜｜tool_calls>'
)


def test_double_pipe_detected():
    assert _has_dsml_markup(DOUBLE_PIPE_SAMPLE) is True


def test_double_pipe_parses_to_tool_call():
    clean, tc = _parse_dsml_tool_call(DOUBLE_PIPE_SAMPLE)
    assert clean == 'Now let me check Etsy.'
    assert tc is not None
    assert tc['name'] == 'search_wiki'
    assert tc['input'] == {
        'query': 'etsy personalised products revenue 2026',
        'limit': '5',
    }


# ── plain text passes through untouched ────────────────────────────


def test_plain_text_no_dsml_returns_unchanged():
    text = 'The answer is 42 — no tool needed.'
    assert _has_dsml_markup(text) is False
    clean, tc = _parse_dsml_tool_call(text)
    assert clean == text
    assert tc is None


def test_empty_string():
    assert _has_dsml_markup('') is False
    clean, tc = _parse_dsml_tool_call('')
    assert clean == ''
    assert tc is None


# ── malformed markup degrades gracefully ────────────────────────────


def test_open_tag_only_no_invoke():
    """Opener present but no <invoke> — strip the markup, no tool call."""
    text = 'Let me think.\n<｜｜DSML｜｜tool_calls>'
    clean, tc = _parse_dsml_tool_call(text)
    assert clean == 'Let me think.'
    assert tc is None


def test_invoke_with_no_parameters():
    """Tool with no params is valid — empty input dict."""
    text = (
        '<｜｜DSML｜｜tool_calls>\n'
        '<｜｜DSML｜｜invoke name="get_status">\n'
        '</｜｜DSML｜｜invoke>\n'
        '</｜｜DSML｜｜tool_calls>'
    )
    clean, tc = _parse_dsml_tool_call(text)
    assert tc is not None
    assert tc['name'] == 'get_status'
    assert tc['input'] == {}


def test_text_after_dsml_block_is_ignored():
    """If the model continues writing after the DSML block, anything
    after is ignored — only the clean text BEFORE counts as the
    visible response."""
    text = (
        'Working on it.\n'
        '<｜｜DSML｜｜tool_calls>\n'
        '<｜｜DSML｜｜invoke name="ping">\n'
        '</｜｜DSML｜｜invoke>\n'
        '</｜｜DSML｜｜tool_calls>\n'
        'And here is more text the user should not see.'
    )
    clean, tc = _parse_dsml_tool_call(text)
    assert clean == 'Working on it.'
    assert tc is not None
    assert tc['name'] == 'ping'
