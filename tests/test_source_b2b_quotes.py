"""
Tests for ``scripts.backfill.sources.b2b_quotes``.

Covers the YAML parser, the verbatim-vs-Sonnet lesson path (parallel
to disputes), the multi-option ``rejected_alternatives`` shape the
brief calls out as canonical (Bakery Barn three-option quote), and
the optional email enrichment hook.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_FIXTURE_YAML = """\
# Canonical three-option quote with a known accept outcome.
- case_id: bakery-barn-2024
  client: Bakery Barn
  quote_value_gbp: 2400
  phases:
    - phase: initial_quote
      decided_at: 2024-09-10
      context: |
        Independent bakery wanted a new illuminated shopfront sign.
        Budget ceiling was firm. Three options were presented:
        cheapest (foamex), middle (aluminium tray with vinyl graphics)
        and premium (built-up illuminated letters).
      chosen_path: "Led with the middle option at exactly budget."
      rejected_alternatives:
        - path: "Foamex only"
          reason: "Would not last two winters outdoors."
        - path: "Built-up illuminated letters"
          reason: "Over budget by 40 percent."
      outcome: |
        Client accepted the middle option and paid deposit within a week.
      chosen_path_score: 0.9
      metrics:
        margin_pct: 38
  lessons_in_your_own_words: |
    For small independent clients with a firm budget, anchor on the
    middle option and design it to land exactly at their ceiling.

# Quote that went cold — no verbatim lesson, Sonnet will generate one.
- case_id: hair-salon-discount-2024
  client: Tyneside Hair Salon
  phases:
    - phase: quote_sent
      decided_at: 2024-11-20
      context: |
        Small hair salon pushed for a 25 percent discount in exchange
        for promised future referrals. Chose to hold full price.
      chosen_path: "Held full price, offered a small goodwill extra."
      rejected_alternatives:
        - path: "Accept the 25 percent discount"
          reason: "Would set an undercutting precedent on future quotes."
      outcome: |
        Client signed at full price. No referrals materialised.
      chosen_path_score: 0.5
"""


@pytest.fixture
def b2b_yaml(tmp_path: Path) -> Path:
    path = tmp_path / 'b2b_quotes.yml'
    path.write_text(_FIXTURE_YAML, encoding='utf-8')
    return path


# ── Parser tests ───────────────────────────────────────────────────────


def test_source_yields_all_phases(b2b_yaml):
    from scripts.backfill.sources.b2b_quotes import B2BQuotesSource
    records = list(B2BQuotesSource(yaml_path=b2b_yaml).iter_records())
    assert len(records) == 2
    assert {r.source_type for r in records} == {'b2b_quote'}
    assert all(r.signal_strength == 0.8 for r in records)
    ids = {r.deterministic_id for r in records}
    assert 'backfill_b2b_bakery-barn-2024_initial_quote' in ids
    assert 'backfill_b2b_hair-salon-discount-2024_quote_sent' in ids


def test_bakery_barn_three_option_dissents(b2b_yaml):
    from scripts.backfill.sources.b2b_quotes import B2BQuotesSource
    records = list(B2BQuotesSource(yaml_path=b2b_yaml).iter_records())
    bakery = next(r for r in records if 'bakery' in r.deterministic_id)
    assert bakery.rejected_paths is not None
    assert len(bakery.rejected_paths) == 2
    paths = {rp['path'] for rp in bakery.rejected_paths}
    assert 'Foamex only' in paths
    assert 'Built-up illuminated letters' in paths


def test_verbatim_lesson_attaches_to_last_phase(b2b_yaml):
    from scripts.backfill.sources.b2b_quotes import B2BQuotesSource
    records = list(B2BQuotesSource(yaml_path=b2b_yaml).iter_records())
    bakery = next(r for r in records if 'bakery' in r.deterministic_id)
    assert bakery.verbatim_lesson is not None
    assert 'middle option' in bakery.verbatim_lesson
    assert bakery.verbatim_lesson_model == 'toby_verbatim'

    hair = next(r for r in records if 'hair-salon' in r.deterministic_id)
    assert hair.verbatim_lesson is None


def test_privacy_scrub_is_required(b2b_yaml):
    """All b2b_quote records must carry needs_privacy_scrub=True."""
    from scripts.backfill.sources.b2b_quotes import B2BQuotesSource
    records = list(B2BQuotesSource(yaml_path=b2b_yaml).iter_records())
    assert all(r.needs_privacy_scrub for r in records)
    assert not any(r.needs_privacy_review for r in records)


def test_client_metadata_in_raw_source_ref(b2b_yaml):
    from scripts.backfill.sources.b2b_quotes import B2BQuotesSource
    records = list(B2BQuotesSource(yaml_path=b2b_yaml).iter_records())
    bakery = next(r for r in records if 'bakery' in r.deterministic_id)
    assert bakery.raw_source_ref['client'] == 'Bakery Barn'
    assert bakery.raw_source_ref['quote_value_gbp'] == 2400
    assert bakery.raw_source_ref['case_id'] == 'bakery-barn-2024'


def test_outcome_and_metrics(b2b_yaml):
    from scripts.backfill.sources.b2b_quotes import B2BQuotesSource
    records = list(B2BQuotesSource(yaml_path=b2b_yaml).iter_records())
    bakery = next(r for r in records if 'bakery' in r.deterministic_id)
    assert bakery.outcome is not None
    assert bakery.outcome.chosen_path_score == 0.9
    assert bakery.outcome.metrics == {'margin_pct': 38}
    assert 'deposit' in bakery.outcome.actual_result.lower()


# ── Error cases ────────────────────────────────────────────────────────


def test_missing_file_raises(tmp_path):
    from scripts.backfill.sources.b2b_quotes import (
        B2BQuotesSource, B2BQuoteYamlError,
    )
    with pytest.raises(B2BQuoteYamlError, match='not found'):
        B2BQuotesSource(yaml_path=tmp_path / 'missing.yml')


def test_enrich_without_db_url_raises(tmp_path):
    from scripts.backfill.sources.b2b_quotes import (
        B2BQuotesSource, B2BQuoteYamlError,
    )
    (tmp_path / 'b2b_quotes.yml').write_text(_FIXTURE_YAML, encoding='utf-8')
    with pytest.raises(B2BQuoteYamlError, match='requires db_url'):
        B2BQuotesSource(
            yaml_path=tmp_path / 'b2b_quotes.yml',
            enrich_from_emails=True,
        )


def test_empty_file_yields_nothing(tmp_path):
    from scripts.backfill.sources.b2b_quotes import B2BQuotesSource
    path = tmp_path / 'b2b_quotes.yml'
    path.write_text('', encoding='utf-8')
    assert list(B2BQuotesSource(yaml_path=path).iter_records()) == []


def test_missing_case_id_raises(tmp_path):
    from scripts.backfill.sources.b2b_quotes import (
        B2BQuotesSource, B2BQuoteYamlError,
    )
    path = tmp_path / 'b2b_quotes.yml'
    path.write_text(
        '- phases:\n'
        '    - phase: x\n'
        '      decided_at: 2024-01-01\n'
        '      context: y\n'
        '      chosen_path: z\n',
        encoding='utf-8',
    )
    with pytest.raises(B2BQuoteYamlError, match='case_id'):
        list(B2BQuotesSource(yaml_path=path).iter_records())


def test_preflight_flags_missing_b2b_quotes_yml(tmp_path):
    from scripts.backfill.run import preflight
    failures = preflight(
        sources=['b2b_quotes'],
        data_dir=tmp_path,
        commit_mode=False,
    )
    assert any('b2b_quotes.yml' in f and 'Toby must write' in f for f in failures)


def test_preflight_accepts_built_b2b_quotes(tmp_path):
    from scripts.backfill.run import preflight
    (tmp_path / 'b2b_quotes.yml').write_text(_FIXTURE_YAML, encoding='utf-8')
    failures = preflight(
        sources=['b2b_quotes'],
        data_dir=tmp_path,
        commit_mode=False,
    )
    assert not any('not yet implemented' in f for f in failures)
