"""
Cairn Email Ingestion — cairn@ memory inbox + bulk ingest from sales@ / toby@.

Modules:
    db          — schema + get_conn
    filters     — PII redaction, skip/relevance logic
    imap_client — IMAP connection + message parsing
    bulk_ingest — one-off bulk ingest from sales@ and toby@
    embedder    — embed stored emails into claw_code_chunks
    processor   — ongoing cairn@ inbox polling
"""
