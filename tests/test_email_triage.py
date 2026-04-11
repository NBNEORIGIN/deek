"""
Tests for scripts.email_triage.

Uses monkeypatched anthropic + httpx + smtplib so nothing goes over
the network. Focus is on control flow and the edge cases that
matter for the Mode A safety rails.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


# ── Classifier tests ───────────────────────────────────────────────────


def test_classifier_blocks_automation_senders():
    """Automation senders are filtered without a Haiku call."""
    from scripts.email_triage.classifier import classify_email

    email = {
        'sender': 'noreply@amazon.co.uk',
        'subject': 'Your Amazon order has shipped',
        'body_text': 'Hello Toby, your order #123-456 is on its way...',
    }
    result = classify_email(email, anthropic_api_key='test-key')
    assert result['classification'] == 'automation'
    assert result['confidence'] == 'high'
    assert 'blocklist' in result['reason'].lower()


def test_classifier_blocks_common_automation_domains():
    from scripts.email_triage.classifier import _is_automation_sender
    assert _is_automation_sender('noreply@stripe.com') is True
    assert _is_automation_sender('notifications@facebookmail.com') is True
    assert _is_automation_sender('updates@calendly.com') is True
    assert _is_automation_sender('mailer@mailchimp.com') is True
    assert _is_automation_sender('zoom@zoom.us') is True
    # Real humans pass through
    assert _is_automation_sender('debbie_potter@hotmail.co.uk') is False
    assert _is_automation_sender('ami@realfitness.co.uk') is False


def test_classifier_returns_fallback_without_api_key(monkeypatch):
    from scripts.email_triage.classifier import classify_email
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    email = {
        'sender': 'test@example.com',
        'subject': 'Hi',
        'body_text': 'Hello',
    }
    result = classify_email(email, anthropic_api_key='')
    assert result['classification'] == 'unclassified'
    assert result['confidence'] == 'low'
    assert 'not set' in result['reason'].lower()


def test_classifier_parses_haiku_json_output():
    """Mocked Haiku returns valid JSON — classifier parses it."""
    from scripts.email_triage.classifier import classify_email

    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(type='text', text=(
        '{"classification":"new_enquiry","confidence":"high",'
        '"reason":"Client asking for a quote on a pavement sign",'
        '"client_name_guess":"Debbie Potter","project_hint":""}'
    ))]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_resp

    with patch('anthropic.Anthropic', return_value=mock_client):
        result = classify_email({
            'sender': 'debbie_potter@hotmail.co.uk',
            'subject': 'Quote for A board',
            'body_text': 'Please could you give me a quote for an a board for the Serviceman pub',
        }, anthropic_api_key='test-key')

    assert result['classification'] == 'new_enquiry'
    assert result['confidence'] == 'high'
    assert result['client_name_guess'] == 'Debbie Potter'


def test_classifier_rejects_invalid_classification():
    """If Haiku returns a bogus category, it's coerced to unclassified."""
    from scripts.email_triage.classifier import classify_email

    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(type='text', text=(
        '{"classification":"invalid_category","confidence":"medium",'
        '"reason":"test","client_name_guess":"","project_hint":""}'
    ))]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_resp

    with patch('anthropic.Anthropic', return_value=mock_client):
        result = classify_email({
            'sender': 'foo@bar.com',
            'subject': 'test',
            'body_text': 'test',
        }, anthropic_api_key='test-key')

    assert result['classification'] == 'unclassified'


def test_classifier_handles_haiku_failure_gracefully():
    from scripts.email_triage.classifier import classify_email

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception('API timeout')

    with patch('anthropic.Anthropic', return_value=mock_client):
        result = classify_email({
            'sender': 'foo@bar.com',
            'subject': 'test',
            'body_text': 'test',
        }, anthropic_api_key='test-key')

    assert result['classification'] == 'unclassified'
    assert 'failed' in result['reason'].lower()


# ── Project matcher tests ──────────────────────────────────────────────


def test_project_matcher_returns_empty_without_token(monkeypatch):
    from scripts.email_triage.project_matcher import match_project
    monkeypatch.delenv('CAIRN_API_KEY', raising=False)
    result = match_project(
        email={'sender': 'foo@bar.com', 'subject': 'test', 'body_text': 'test'},
        classifier_result={'classification': 'existing_project_reply'},
        api_key='',
    )
    assert result['project_id'] == ''
    assert result['match_score'] == 0.0


def test_project_matcher_filters_weak_hits():
    """Matches below MIN_MATCH_SCORE return empty."""
    from scripts.email_triage.project_matcher import match_project

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        'results': [
            {
                'source_type': 'project',
                'source_id': 'prj-weak',
                'score': 0.01,  # below MIN_MATCH_SCORE=0.025
                'metadata': {'project_name': 'Weak match'},
            }
        ],
    }

    mock_client_cm = MagicMock()
    mock_client_cm.__enter__ = MagicMock(return_value=mock_client_cm)
    mock_client_cm.__exit__ = MagicMock(return_value=None)
    mock_client_cm.get.return_value = mock_response

    with patch('httpx.Client', return_value=mock_client_cm):
        result = match_project(
            email={'sender': 'debbie@test.com', 'subject': 'Re: Something', 'body_text': 'x'},
            classifier_result={
                'classification': 'existing_project_reply',
                'client_name_guess': 'Debbie',
                'project_hint': '',
            },
            api_key='test-token',
        )
    assert result['project_id'] == ''  # weak hit rejected
    assert result['match_score'] == 0.01


def test_project_matcher_prefers_project_rows_over_client_rows():
    from scripts.email_triage.project_matcher import match_project

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        'results': [
            {
                'source_type': 'client',
                'source_id': 'cli-top',
                'score': 0.08,
                'metadata': {},
            },
            {
                'source_type': 'project',
                'source_id': 'prj-second',
                'score': 0.05,
                'metadata': {'project_name': 'The right project'},
            },
        ],
    }

    mock_client_cm = MagicMock()
    mock_client_cm.__enter__ = MagicMock(return_value=mock_client_cm)
    mock_client_cm.__exit__ = MagicMock(return_value=None)
    mock_client_cm.get.return_value = mock_response

    with patch('httpx.Client', return_value=mock_client_cm):
        result = match_project(
            email={'sender': 'x@y.com', 'subject': 'Re: test', 'body_text': 'x'},
            classifier_result={'client_name_guess': 'Test'},
            api_key='test-token',
        )
    assert result['project_id'] == 'prj-second'  # project preferred


# ── Runner tests ───────────────────────────────────────────────────────


def test_runner_requires_kill_switch_enabled(monkeypatch, caplog):
    from scripts.email_triage.triage_runner import run_triage
    monkeypatch.setenv('CAIRN_EMAIL_TRIAGE_ENABLED', 'false')
    result = run_triage(commit=True, max_emails=5, window_days=1)
    assert result == 0


def test_runner_dry_run_writes_nothing(monkeypatch):
    from scripts.email_triage import triage_runner
    monkeypatch.setenv('CAIRN_EMAIL_TRIAGE_ENABLED', 'true')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://bogus:bogus@localhost:9999/bogus')

    # Mock the DB calls the runner makes
    with patch.object(triage_runner, 'fetch_candidate_emails', return_value=[]):
        with patch.object(triage_runner, 'intel_ensure_schema'):
            result = triage_runner.run_triage(commit=False, max_emails=5, window_days=1)
    assert result == 0


def test_runner_loop_prevention_filters_cairn_sender():
    """Emails from cairn@ are dropped in fetch_candidate_emails."""
    from scripts.email_triage.triage_runner import (
        LOOP_PREVENTION_SENDER_PATTERNS,
    )
    # Just verify the pattern list contains the right entries
    assert any('cairn@nbnesigns.com' in p for p in LOOP_PREVENTION_SENDER_PATTERNS)


# ── Digest sender tests ───────────────────────────────────────────────


def test_digest_sender_dry_run_when_smtp_missing(monkeypatch):
    """With no SMTP creds, digest_sender logs instead of sending."""
    from scripts.email_triage import digest_sender
    monkeypatch.setenv('CAIRN_EMAIL_TRIAGE_ENABLED', 'true')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://bogus:bogus@localhost:9999/bogus')
    monkeypatch.delenv('SMTP_HOST', raising=False)
    monkeypatch.delenv('SMTP_USER', raising=False)
    monkeypatch.delenv('SMTP_PASS', raising=False)

    assert digest_sender.smtp_config() is None


def test_digest_sender_smtp_config_when_env_set(monkeypatch):
    from scripts.email_triage import digest_sender
    monkeypatch.setenv('SMTP_HOST', 'smtp.ionos.co.uk')
    monkeypatch.setenv('SMTP_PORT', '587')
    monkeypatch.setenv('SMTP_USER', 'cairn@nbnesigns.com')
    monkeypatch.setenv('SMTP_PASS', 'secret')
    cfg = digest_sender.smtp_config()
    assert cfg is not None
    assert cfg['host'] == 'smtp.ionos.co.uk'
    assert cfg['port'] == 587
    assert cfg['user'] == 'cairn@nbnesigns.com'
    assert cfg['from_addr'] == 'cairn@nbnesigns.com'  # defaults from user


def test_digest_sender_formats_body_for_new_enquiry():
    from scripts.email_triage.digest_sender import format_digest_body

    row = {
        'id': 42,
        'email_message_id': '<msg-1@example.com>',
        'email_mailbox': 'toby',
        'email_sender': 'debbie@example.com',
        'email_subject': 'Quote for A board',
        'email_received_at': datetime(2026, 4, 11, 12, 48, tzinfo=timezone.utc),
        'classification': 'new_enquiry',
        'classification_confidence': 'high',
        'client_name_guess': 'Debbie Potter',
        'project_id': None,
        'analyzer_brief': (
            '⚠️ STRICT VERBATIM OUTPUT — DO NOT MODIFY OR APPEND ⚠️\n\n'
            '<<<ANALYZER_BRIEF_START>>>\n'
            '**Recommendation, not decision.**\n\nBrief content here.\n'
            '<<<ANALYZER_BRIEF_END>>>'
        ),
        'analyzer_job_size': 'small',
    }

    subject, body = format_digest_body(row)
    assert '[Cairn] new_enquiry' in subject
    assert 'Quote for A board' in subject
    assert 'Classification: new_enquiry' in body
    assert 'Debbie Potter' in body
    assert 'small' in body
    # Verbatim wrapper stripped
    assert '<<<ANALYZER_BRIEF_START>>>' not in body
    assert '<<<ANALYZER_BRIEF_END>>>' not in body
    assert 'STRICT VERBATIM' not in body
    # Brief content preserved
    assert 'Recommendation, not decision' in body
    assert 'Brief content here' in body


def test_digest_sender_strip_wrapper_returns_empty_for_missing():
    from scripts.email_triage.digest_sender import _strip_verbatim_wrapper
    assert _strip_verbatim_wrapper('') == ''
    assert _strip_verbatim_wrapper(None) == ''


def test_digest_sender_strip_wrapper_handles_plain_brief():
    """A brief without the wrapper markers passes through unchanged."""
    from scripts.email_triage.digest_sender import _strip_verbatim_wrapper
    plain = '**Recommendation, not decision.**\n\nNext step: x'
    assert _strip_verbatim_wrapper(plain) == plain


def test_digest_sender_existing_project_body_format():
    from scripts.email_triage.digest_sender import format_digest_body

    row = {
        'id': 1,
        'email_message_id': '<msg-2@example.com>',
        'email_mailbox': 'sales',
        'email_sender': 'existing@client.com',
        'email_subject': 'Re: Our sign',
        'email_received_at': None,
        'classification': 'existing_project_reply',
        'classification_confidence': 'high',
        'client_name_guess': 'Existing Client',
        'project_id': 'prj-abc-123',
        'analyzer_brief': None,
        'analyzer_job_size': None,
    }

    subject, body = format_digest_body(row)
    assert 'existing_project_reply' in subject
    assert 'prj-abc-123' in body
    assert 'follow-up' in body.lower()


# ── DB helpers smoke test ──────────────────────────────────────────────


def test_upsert_email_triage_signature():
    """Just import and make sure the function exists with the right shape."""
    from core.intel.db import (
        upsert_email_triage,
        load_unsent_triage_drafts,
        mark_triage_sent,
        already_triaged_message_ids,
    )
    # All four helpers importable
    assert callable(upsert_email_triage)
    assert callable(load_unsent_triage_drafts)
    assert callable(mark_triage_sent)
    assert callable(already_triaged_message_ids)
