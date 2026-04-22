"""Unit tests for core.brief.replies — Memory Brief Phase B parser.

DB-dependent apply-path is exercised live on Hetzner; this suite
covers the pure parsing + classification logic that sits in front.
"""
from __future__ import annotations

from datetime import date

import pytest

from core.brief.replies import (
    ParsedAnswer,
    extract_date_from_subject,
    parse_reply_body,
    _strip_quoted,
    _classify,
)


# ── Subject parsing ──────────────────────────────────────────────────

class TestExtractDate:
    def test_standard_reply(self):
        assert extract_date_from_subject('Re: Deek morning brief — 2026-04-21') == date(2026, 4, 21)

    def test_hyphen_instead_of_emdash(self):
        assert extract_date_from_subject('Re: Deek morning brief - 2026-04-21') == date(2026, 4, 21)

    def test_case_insensitive(self):
        assert extract_date_from_subject('RE: DEEK MORNING BRIEF - 2026-04-21') == date(2026, 4, 21)

    def test_gmail_prefix(self):
        # Some clients add extra prefixes
        assert extract_date_from_subject(
            '[toby] Re: Deek morning brief — 2026-04-21'
        ) == date(2026, 4, 21)

    def test_non_brief_subject(self):
        assert extract_date_from_subject('Re: Meeting tomorrow') is None
        assert extract_date_from_subject('Deek dream-state digest') is None

    def test_empty(self):
        assert extract_date_from_subject('') is None
        assert extract_date_from_subject(None or '') is None

    def test_no_date(self):
        assert extract_date_from_subject('Re: Deek morning brief') is None

    def test_malformed_date(self):
        assert extract_date_from_subject('Re: Deek morning brief — 21/04/2026') is None


# ── Quote stripping ──────────────────────────────────────────────────

class TestStripQuoted:
    def test_no_quotes(self):
        body = 'My answer here.\nOn two lines.'
        assert _strip_quoted(body) == 'My answer here.\nOn two lines.'

    def test_strips_gt_quotes(self):
        body = 'My reply\n\n> Original first line\n> Original second line'
        assert _strip_quoted(body) == 'My reply'

    def test_strips_outlook_header(self):
        body = 'My reply\n\n--- Original Message ---\nFrom: deek@...'
        assert _strip_quoted(body) == 'My reply'

    def test_strips_on_wrote_header(self):
        body = 'My reply\n\nOn Mon, 21 Apr 2026 at 07:30, Deek <deek@x> wrote:\nOriginal content here'
        assert _strip_quoted(body) == 'My reply'

    def test_empty(self):
        assert _strip_quoted('') == ''

    def test_mbox_from_munging_does_not_strip(self):
        """Regression: 2026-04-22 memory brief parse failure.

        Some email clients prefix '>' (no space) to any quoted line
        that would otherwise start with 'From ' at column 0 — this
        is mbox From-munging, NOT a reply quote. The old strip_quoted
        treated it as a quote boundary and cut off everything below,
        losing Q2 and Q3 answers.

        Real reply quoting uses '> ' (with space).
        """
        body = (
            'My answer to Q1.\n\n'
            '--- Q2 (salience_calibration) ---\n'
            'SALIENCE CHECK\n\n'
            '>From 2026-04-21:\n'           # mbox-munged, NOT a quote
            '  Erkan replies quickly when Jo is copied.\n\n'
            'Is this genuinely important?\n'
            'My answer: Yes, it helps comms efficiency.\n\n'
            '--- Q3 (open_ended) ---\n'
            'One thing worth remembering: QA caught a duplicate QR code.\n'
        )
        out = _strip_quoted(body)
        # All three Q-delimiters should survive
        assert '--- Q2 (salience_calibration) ---' in out
        assert '--- Q3 (open_ended) ---' in out
        assert 'QA caught a duplicate' in out

    def test_real_gt_quote_with_space_still_strips(self):
        """Real reply quoting uses '> ' (with space) and should still
        be recognised as a quote boundary."""
        body = 'My reply\n\n> On Mon, Deek wrote:\n> original content'
        assert _strip_quoted(body) == 'My reply'

    def test_nested_gt_quote_still_strips(self):
        """'>>' (nested quote) should still strip."""
        body = 'My reply\n\n>> Very old quoted content'
        assert _strip_quoted(body) == 'My reply'


# ── Answer classification ────────────────────────────────────────────

class TestClassify:
    def test_empty(self):
        assert _classify('') == ('empty', '')
        assert _classify('   \n  ') == ('empty', '')

    def test_plain_true(self):
        verdict, correction = _classify('TRUE')
        assert verdict == 'affirm'
        assert correction == ''

    def test_plain_yes(self):
        assert _classify('YES')[0] == 'affirm'
        assert _classify('yes')[0] == 'affirm'
        assert _classify('Yes\n')[0] == 'affirm'

    def test_plain_false(self):
        assert _classify('FALSE')[0] == 'deny'
        assert _classify('no')[0] == 'deny'
        assert _classify('Wrong')[0] == 'deny'

    def test_correction_text(self):
        v, c = _classify('The quote was actually £6,500 not £5,000')
        assert v == 'correct'
        assert '£6,500' in c

    def test_ignores_expected_format_hint(self):
        """The composer appends '(Expected reply format: ...)'. If the
        user leaves that intact, it should be stripped before
        classification."""
        v, c = _classify('TRUE\n\n(Expected reply format: TRUE / FALSE)')
        assert v == 'affirm'

    def test_slash_separated_first_line(self):
        """User leaving the '/' between tokens by accident on first line
        still classifies on the first word."""
        assert _classify('TRUE / FALSE')[0] == 'affirm'
        assert _classify('FALSE / [correction]')[0] == 'deny'


# ── Full reply parsing ───────────────────────────────────────────────

_VALID_BRIEF = """\
Deek morning brief — 2026-04-21
============================================================

2 questions for you today.
Reply to this email to answer. One block per question — keep the
Q<n> headers in place so I can parse your replies correctly.

--- Q1 (salience_calibration) ---
SALIENCE CHECK — flagged yesterday with salience 4.2/10

From 2026-04-20:
  Task: Campaign analysis for customer 20286

Is this genuinely important long-term?
Reply: YES  /  NO  /  [why or why not]

(Expected reply format: YES / NO / [why or why not])

--- Q2 (open_ended) ---
OPEN —

One thing from yesterday worth remembering long-term.

Reply: (free text — one or two sentences)

(Expected reply format: Free text (one or two sentences))

— Deek
"""


def _make_reply_body(q1_answer: str, q2_answer: str) -> str:
    """Build a reply body with answers interleaved above each block."""
    return f"""\
{q1_answer}

--- Q1 (salience_calibration) ---
{q2_answer}

--- Q2 (open_ended) ---
"""


class TestParseReplyBody:
    def test_two_clean_answers(self):
        body = """\
--- Q1 (salience_calibration) ---
NO — that memory was routine

--- Q2 (open_ended) ---
Flowers By Julie quote went out today, worth watching the response time.
"""
        reply = parse_reply_body(body, 'toby@nbnesigns.com', date(2026, 4, 21))
        assert len(reply.answers) == 2
        assert reply.answers[0].category == 'salience_calibration'
        assert reply.answers[0].verdict == 'deny'
        assert reply.answers[1].category == 'open_ended'
        assert reply.answers[1].verdict == 'correct'
        assert 'Flowers By Julie' in reply.answers[1].correction_text

    def test_missing_delimiters_treated_as_open_ended(self):
        """If user replies without keeping headers intact — we still
        capture as an open-ended answer."""
        body = 'Whole thing was good. No issues.'
        reply = parse_reply_body(body, 'toby@nbnesigns.com', date(2026, 4, 21))
        assert len(reply.answers) == 1
        assert reply.answers[0].category == 'open_ended'
        assert reply.answers[0].verdict == 'correct'
        assert 'delimiters found' in reply.parse_notes[0]

    def test_empty_body_produces_empty_reply(self):
        reply = parse_reply_body('', 'toby@nbnesigns.com', date(2026, 4, 21))
        assert reply.answers == []
        assert 'empty' in reply.parse_notes[0].lower()

    def test_quoted_content_ignored(self):
        body = """\
--- Q1 (belief_audit) ---
TRUE

--- Q2 (open_ended) ---
Worth noting.

> On Mon, Deek wrote:
> --- Q3 (fake_category) ---
> this should NOT appear as an answer
"""
        reply = parse_reply_body(body, 'toby@nbnesigns.com', date(2026, 4, 21))
        assert len(reply.answers) == 2
        assert all(a.category in {'belief_audit', 'open_ended'} for a in reply.answers)

    def test_block_text_preserves_context(self):
        body = """\
--- Q1 (salience_calibration) ---
NO — we mis-flagged that. The campaign was just a routine analysis
and shouldn't be high salience.

--- Q2 (open_ended) ---
"""
        reply = parse_reply_body(body, 't@x', date(2026, 4, 21))
        assert reply.answers[0].verdict == 'deny'
        # The multi-line explanation should be preserved as raw_text even
        # though first-line classification won.
        assert 'routine analysis' in reply.answers[0].raw_text

    def test_correction_text_captured(self):
        body = """\
--- Q1 (belief_audit) ---
Both are the same customer — canonical name is "Clayport Jewellers Ltd".

--- Q2 (open_ended) ---
"""
        reply = parse_reply_body(body, 't@x', date(2026, 4, 21))
        assert reply.answers[0].verdict == 'correct'
        assert 'Clayport' in reply.answers[0].correction_text
