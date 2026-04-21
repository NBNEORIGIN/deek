"""Unit tests for core.triage.replies — Phase B parser.

DB-dependent apply path is covered by the live run; these tests
cover the pure parsing + classification logic.
"""
from __future__ import annotations

import pytest

from core.triage.replies import (
    ParsedAnswer,
    is_triage_reply, strip_reply_prefix,
    strip_quoted, parse_reply_body,
    _classify_match_confirm, _classify_reply_approval,
    _strip_format_hint,
)


# ── Subject helpers ─────────────────────────────────────────────────

class TestIsTriageReply:
    def test_matches_re_with_bracket(self):
        assert is_triage_reply(
            'Re: [Deek] existing_project_reply — Re: Window displays'
        )

    def test_matches_new_enquiry(self):
        assert is_triage_reply('Re: [Deek] new_enquiry — Signage quote')

    def test_case_insensitive(self):
        assert is_triage_reply('RE: [deek] existing_project_reply — x')
        assert is_triage_reply('re: [DEEK] NEW_ENQUIRY — x')

    def test_no_reply_prefix(self):
        """Plain digests (before reply) should NOT match."""
        assert not is_triage_reply('[Deek] existing_project_reply — x')

    def test_wrong_classification(self):
        assert not is_triage_reply('Re: [Deek] automation — x')

    def test_empty(self):
        assert not is_triage_reply('')
        assert not is_triage_reply(None or '')


class TestStripReplyPrefix:
    def test_single_re(self):
        assert strip_reply_prefix('Re: Subject') == 'Subject'

    def test_multiple(self):
        assert strip_reply_prefix('Re: Re: Fw: Subject') == 'Subject'

    def test_case_mixed(self):
        assert strip_reply_prefix('RE: Fwd: Subject') == 'Subject'

    def test_no_prefix(self):
        assert strip_reply_prefix('Just a subject') == 'Just a subject'


# ── Quote stripping (same contract as core.brief.replies) ───────────

class TestStripQuoted:
    def test_gt_quote(self):
        body = 'My reply\n\n> Original\n> More original'
        assert strip_quoted(body) == 'My reply'

    def test_original_message_header(self):
        body = 'My reply\n\n--- Original Message ---\nFrom: deek@'
        assert strip_quoted(body) == 'My reply'

    def test_on_wrote_header(self):
        body = 'My reply\n\nOn Mon 21 Apr, Deek wrote:\n...'
        assert strip_quoted(body) == 'My reply'


# ── Format hint stripping ────────────────────────────────────────────

class TestStripFormatHint:
    def test_drops_expected_format_line(self):
        txt = "YES\n\n(Expected reply format: YES / NO)"
        out = _strip_format_hint(txt)
        assert 'YES' in out
        assert 'Expected reply format' not in out


# ── match_confirm classification ────────────────────────────────────

class TestMatchConfirm:
    def test_yes(self):
        a = _classify_match_confirm('YES')
        assert a.verdict == 'affirm'

    def test_yes_with_trailing(self):
        a = _classify_match_confirm('Yes — looks right')
        assert a.verdict == 'affirm'

    def test_no(self):
        a = _classify_match_confirm('NO')
        assert a.verdict == 'deny'

    def test_candidate_1(self):
        a = _classify_match_confirm('1')
        assert a.verdict == 'select_candidate'
        assert a.selected_candidate_index == 1

    def test_candidate_2(self):
        a = _classify_match_confirm('  2  ')
        assert a.verdict == 'select_candidate'
        assert a.selected_candidate_index == 2

    def test_candidate_3(self):
        a = _classify_match_confirm('3')
        assert a.selected_candidate_index == 3

    def test_out_of_range_candidate_is_text(self):
        # Not one of 1/2/3 — treat as free text
        a = _classify_match_confirm('7')
        assert a.verdict == 'text'

    def test_free_text_correction(self):
        a = _classify_match_confirm(
            "None of those — it's actually the Bamburgh job from last year"
        )
        assert a.verdict == 'text'
        assert 'Bamburgh' in a.free_text

    def test_empty(self):
        a = _classify_match_confirm('')
        assert a.verdict == 'empty'


# ── reply_approval classification ───────────────────────────────────

class TestReplyApproval:
    def test_use(self):
        a = _classify_reply_approval('USE')
        assert a.verdict == 'affirm'

    def test_use_case_insensitive(self):
        assert _classify_reply_approval('use').verdict == 'affirm'
        assert _classify_reply_approval('yes').verdict == 'affirm'

    def test_reject(self):
        a = _classify_reply_approval('REJECT')
        assert a.verdict == 'deny'

    def test_edit_inline(self):
        a = _classify_reply_approval('EDIT: Hi Julie, quick update...')
        assert a.verdict == 'edit'
        assert a.edited_text.startswith('Hi Julie')

    def test_edit_case_insensitive(self):
        a = _classify_reply_approval('edit: new text here')
        assert a.verdict == 'edit'
        assert a.edited_text == 'new text here'

    def test_multiline_no_prefix_is_edit(self):
        """A multi-line reply without USE/EDIT prefix is treated as the
        whole edited reply — nice for clients that auto-format."""
        body = "Hi Julie,\n\nNew body here.\n\nBest,\nToby"
        a = _classify_reply_approval(body)
        assert a.verdict == 'edit'
        assert 'New body here' in a.edited_text

    def test_empty(self):
        a = _classify_reply_approval('')
        assert a.verdict == 'empty'


# ── Full reply parsing ──────────────────────────────────────────────

class TestParseReplyBody:
    def test_all_four_answered(self):
        body = """\
--- Q1 (match_confirm) ---
YES

--- Q2 (reply_approval) ---
USE

--- Q3 (project_folder) ---
D:\\NBNE\\Projects\\M1234-flowers-by-julie

--- Q4 (notes) ---
Julie mentioned she might want extra window graphics next year.
"""
        reply = parse_reply_body(body, 'toby@x', 155)
        assert len(reply.answers) == 4
        verdicts = [a.verdict for a in reply.answers]
        assert verdicts == ['affirm', 'affirm', 'text', 'text']

        q3 = reply.answers[2]
        assert 'M1234' in q3.free_text
        q4 = reply.answers[3]
        assert 'window graphics' in q4.free_text

    def test_candidate_selected(self):
        body = """\
--- Q1 (match_confirm) ---
2

--- Q2 (reply_approval) ---
EDIT: Different reply entirely

--- Q3 (project_folder) ---

--- Q4 (notes) ---

"""
        reply = parse_reply_body(body, 'toby@x', 155)
        assert reply.answers[0].verdict == 'select_candidate'
        assert reply.answers[0].selected_candidate_index == 2
        assert reply.answers[1].verdict == 'edit'
        assert 'Different reply' in reply.answers[1].edited_text
        assert reply.answers[2].verdict == 'empty'
        assert reply.answers[3].verdict == 'empty'

    def test_missing_delimiters_treated_as_notes(self):
        reply = parse_reply_body(
            'Whole thing is one blob of notes', 'toby@x', 155,
        )
        assert len(reply.answers) == 1
        assert reply.answers[0].category == 'notes'
        assert 'delimiters' in reply.parse_notes[0]

    def test_quoted_tail_ignored(self):
        body = """\
--- Q1 (match_confirm) ---
YES

--- Q2 (reply_approval) ---
USE

> On Mon, Deek wrote:
> --- Q3 (sneaky) ---
> should be ignored
"""
        reply = parse_reply_body(body, 'toby@x', 155)
        # Only Q1 + Q2 should be parsed; the quoted Q3 is stripped
        assert len(reply.answers) == 2
        assert [a.category for a in reply.answers] == [
            'match_confirm', 'reply_approval',
        ]

    def test_format_hints_stripped(self):
        body = """\
--- Q1 (match_confirm) ---
YES
(Expected reply format: YES / NO / [candidate number 1-3])

--- Q2 (reply_approval) ---
USE
(Expected reply format: USE / EDIT: <new text> / REJECT)

--- Q3 (project_folder) ---

--- Q4 (notes) ---

"""
        reply = parse_reply_body(body, 'toby@x', 155)
        # Format hints shouldn't break first-word classification
        assert reply.answers[0].verdict == 'affirm'
        assert reply.answers[1].verdict == 'affirm'

    def test_empty_body(self):
        reply = parse_reply_body('', 'toby@x', 155)
        assert reply.answers == []
        assert 'empty' in reply.parse_notes[0].lower()

    def test_unknown_category_preserved(self):
        body = """\
--- Q5 (future_feature) ---
some content
"""
        reply = parse_reply_body(body, 'toby@x', 155)
        assert len(reply.answers) == 1
        assert reply.answers[0].category == 'future_feature'
        assert any('unknown category' in n for n in reply.parse_notes)
