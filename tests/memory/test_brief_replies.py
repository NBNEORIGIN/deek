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
    _body_hash,
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

    def test_top_posted_with_delimiters_preserves_everything(self):
        """Regression: 2026-04-22 IONOS top-post reply.

        Some clients render Re: replies as 'On <date> wrote:' at the
        TOP with the quoted original and the user's inline answers
        BELOW. If the body contains our Q-delimiters, they are
        ground truth — skip heuristic stripping entirely.
        """
        body = (
            'On 22/04/2026 11:18 BST cairn@nbnesigns.com wrote:\n\n'
            'Deek morning brief — 2026-04-22\n\n'
            '--- Q1 (belief_audit) ---\n'
            'I currently believe: X\n'
            'Reply: TRUE / FALSE / [correction]\n'
            'TRUE\n\n'
            '--- Q2 (salience_calibration) ---\n'
            'Is this genuinely important?\n'
            'Reply: YES / NO / [why or why not]\n'
            'NO - outdated domain\n\n'
            '--- Q3 (open_ended) ---\n'
            'Reply: (free text)\n'
            'Thorough QA on client designs.\n'
        )
        out = _strip_quoted(body)
        assert 'TRUE' in out
        assert 'outdated domain' in out
        assert 'QA on client designs' in out
        assert '--- Q1' in out
        assert '--- Q3' in out


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

    def test_ionos_top_post_interleaved_answers(self):
        """Regression: 2026-04-22 run_id 6262f0b6 parse failure.

        IONOS webmail replies put 'On <date> wrote:' at the top and
        the quoted original below with '> ' on every line. The user
        types answers as UN-prefixed lines interleaved between the
        quoted prompts. The parser must recover all three answers.
        """
        body = (
            "> On 22/04/2026 11:18 BST cairn@nbnesigns.com wrote:\n"
            "> \n"
            ">  \n"
            "> Deek morning brief — 2026-04-22\n"
            "> ============================================================\n"
            "> \n"
            "> --- Q1 (belief_audit) ---\n"
            "> BELIEF AUDIT — 2 days old, used 0 times\n"
            "> \n"
            "> I currently believe:\n"
            ">   Follow up with budget concerns via email within two days.\n"
            "> \n"
            "> Is this still true?\n"
            "> Reply: TRUE  /  FALSE  /  [correction]\n"
            "TRUE\n"
            "> \n"
            "> (Expected reply format: TRUE / FALSE / [correction])\n"
            "> \n"
            "> --- Q2 (salience_calibration) ---\n"
            "> SALIENCE CHECK — flagged yesterday with salience 7.0/10\n"
            "> \n"
            "> Dated 2026-04-22:\n"
            ">   Toby open-ended reflection: Web: nbnesigns.com\n"
            "> \n"
            "> Signal breakdown: toby_flag 1.00\n"
            "> \n"
            "> Is this genuinely important long-term?\n"
            "> Reply: YES  /  NO  /  [why or why not]\n"
            "NO - we don't use nbnesigns.com anymore, we use nbnesigns.co.uk\n"
            "> \n"
            "> (Expected reply format: YES / NO / [why or why not])\n"
            "> \n"
            "> --- Q3 (open_ended) ---\n"
            "> OPEN —\n"
            "> \n"
            "> One thing from yesterday worth remembering long-term.\n"
            "> \n"
            "> Reply: (free text — one or two sentences)\n"
            "> \n"
            "> (Expected reply format: Free text (one or two sentences))\n"
            "We need to complete thorough qa checks on our client designs\n"
            "> \n"
            "> — Deek\n"
            "\n"
            "Toby Fletcher CEng MIMechE\n"
            "\n"
            "Email: toby@nbnesigns.com\n"
            "Landline: 01665 606741\n"
            "Mobile: 07747484353\n"
            "Web: nbnesigns.com\n"
        )
        reply = parse_reply_body(body, 'toby@x', date(2026, 4, 22))
        assert len(reply.answers) == 3
        a1, a2, a3 = reply.answers
        assert a1.q_number == 1 and a1.category == 'belief_audit'
        assert a1.verdict == 'affirm'
        assert a2.q_number == 2 and a2.category == 'salience_calibration'
        assert a2.verdict == 'deny'
        assert 'nbnesigns.co.uk' in a2.correction_text
        assert a3.q_number == 3 and a3.category == 'open_ended'
        assert 'qa' in a3.correction_text.lower() or 'QA' in a3.correction_text
        # Signature stripped from last block
        assert 'Toby Fletcher CEng' not in a3.correction_text
        assert 'Landline' not in a3.correction_text


# ── Idempotency hash agreement ───────────────────────────────────────

class TestBodyHash:
    """Pin the agreement between the Python _body_hash and the SQL
    idempotency check (encode(sha256(raw_body::bytea), 'hex')).

    Regression for 2026-04-28: an earlier version folded run_id into
    the Python digest while the SQL only hashed raw_body. They never
    matched, so already_applied() always returned False and every
    cron tick re-applied the same reply. Jo's run was applied 49+
    times in 24h before this was caught; an earlier run, 283 times.
    """

    def test_hash_matches_sql_form(self):
        import hashlib
        body = "TRUE\n\nNo, the supplier is fine."
        run_id = "11111111-2222-3333-4444-555555555555"
        # SQL form: encode(sha256(raw_body::bytea), 'hex')
        sql_form = hashlib.sha256(body.encode("utf-8")).hexdigest()
        py_form = _body_hash(body, run_id)
        assert py_form == sql_form, (
            f"Python {py_form} disagrees with SQL {sql_form} — "
            "idempotency would silently fail again"
        )

    def test_hash_independent_of_run_id(self):
        # run_id is in the signature but must not affect the digest —
        # scope is enforced by the SQL WHERE run_id = ... clause.
        body = "any text"
        a = _body_hash(body, "run-aaaa")
        b = _body_hash(body, "run-bbbb")
        assert a == b

    def test_hash_handles_empty_body(self):
        assert _body_hash("", "any-run") == _body_hash("", "any-run")
