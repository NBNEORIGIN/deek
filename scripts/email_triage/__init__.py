"""
Email triage pipeline — the Mode A automation that reads incoming
emails from cairn_email_raw, classifies them, runs analyze_enquiry
on new enquiries, and delivers a digest to Toby via SMTP.

Module layout:
    classifier.py       — Haiku-backed email classification
    project_matcher.py  — fuzzy-match emails to existing CRM projects
    triage_runner.py    — main pipeline, runs every 5 minutes
    digest_sender.py    — IONOS SMTP delivery, runs every 5 minutes

Design principles:
    - Mode A only (no direct client reply, no direct CRM writes)
    - Idempotent via cairn_intel.email_triage UNIQUE constraint
    - Loop-safe (filters out emails from cairn@nbnesigns.com)
    - Kill switch: CAIRN_EMAIL_TRIAGE_ENABLED env var must be 'true'
    - Graceful degradation when SMTP credentials are missing
      (dry-run mode: logs what would be sent)
    - Every action recorded in cairn_intel.email_triage with
      timestamps, classification reason, and send outcome
"""
