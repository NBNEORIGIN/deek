"""
CLI entry point for the Cairn counterfactual backfill importer.

Run the smoke test from the dev box:

    python -m scripts.backfill.run --source synthetic --dry-run
    python -m scripts.backfill.run --source synthetic --commit

The default mode is dry-run. ``--commit`` is the only flag that
causes rows to land in ``cairn_intel.decisions``.

Phase 2 wires only the synthetic source. Phase 3 onwards adds
disputes, principles, m_numbers, b2b_quotes, emails, xero, amazon.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from dotenv import load_dotenv

# override=True because this CLI is run from interactive shells where
# stray empty env vars (e.g. an unset-but-exported ANTHROPIC_API_KEY)
# would otherwise silently mask the real values in .env.
load_dotenv(override=True)


# ── Source registry ────────────────────────────────────────────────────


def _load_source(
    name: str,
    data_dir: Path,
    tagger: Any = None,
    embed_fn: Any = None,
):
    """Instantiate a source adapter by short name.

    Extend this when a new source is built. Unknown names are a hard
    error — the CLI's ``--source`` flag is a closed set.

    ``tagger`` and ``embed_fn`` are required for sources that do
    their own LLM work (principles, amazon) and ignored otherwise.
    Both are constructed in main() before _load_source is called.
    """
    if name == 'synthetic':
        from .sources.synthetic import SyntheticSource
        return SyntheticSource()
    if name == 'disputes':
        from .sources.disputes import DisputesSource
        return DisputesSource(yaml_path=data_dir / 'disputes.yml')
    if name == 'b2b_quotes':
        from .sources.b2b_quotes import B2BQuotesSource
        return B2BQuotesSource(
            yaml_path=data_dir / 'b2b_quotes.yml',
            enrich_from_emails=bool(os.getenv('DATABASE_URL')),
            db_url=os.getenv('DATABASE_URL') or None,
        )
    if name == 'principles':
        from .sources.principles import PrinciplesSource
        if tagger is None or embed_fn is None:
            raise ValueError(
                'principles source requires tagger + embed_fn — '
                'caller must supply both'
            )
        wiki_dir = Path(
            os.getenv('CAIRN_WIKI_DIR', str(Path(__file__).parent.parent.parent / 'wiki'))
        )
        max_files_env = os.getenv('CAIRN_PRINCIPLES_MAX_FILES')
        max_files = int(max_files_env) if max_files_env else None
        return PrinciplesSource(
            wiki_dir=wiki_dir,
            tagger=tagger,
            embed_fn=embed_fn,
            max_files=max_files,
        )
    if name == 'xero':
        from .sources.xero import XeroSource
        return XeroSource(db_url=os.getenv('LEDGER_DATABASE_URL'))
    if name == 'amazon':
        from .sources.amazon import AmazonSource
        return AmazonSource(db_url=os.getenv('DATABASE_URL'))
    if name == 'crm_lessons':
        from .sources.crm_lessons import CrmLessonsSource
        return CrmLessonsSource()
    raise ValueError(
        f"unknown source '{name}' in _load_source — "
        'add the loader alongside the others above'
    )


KNOWN_SOURCES = {
    # Phase 2
    'synthetic': {'needs_llm': False, 'needs_data_file': None, 'needs_ledger_db': False},
    # Phase 3+ — stubs so preflight can flag unbuilt sources clearly.
    'disputes':    {'needs_llm': True,  'needs_data_file': 'disputes.yml',   'needs_ledger_db': False},
    'principles':  {'needs_llm': True,  'needs_data_file': None,             'needs_ledger_db': False},
    'm_numbers':   {'needs_llm': False, 'needs_data_file': None,             'needs_ledger_db': False},
    'b2b_quotes':  {'needs_llm': True,  'needs_data_file': 'b2b_quotes.yml', 'needs_ledger_db': False},
    'emails':      {'needs_llm': True,  'needs_data_file': None,             'needs_ledger_db': False},
    'xero':        {'needs_llm': True,  'needs_data_file': None,             'needs_ledger_db': True},
    'amazon':      {'needs_llm': True,  'needs_data_file': None,             'needs_ledger_db': False},
    'crm_lessons': {'needs_llm': True,  'needs_data_file': None,             'needs_ledger_db': False},
}


def _resolve_sources(spec: str) -> list[str]:
    if spec.strip().lower() == 'all':
        # 'all' means every BUILT source. In Phase 2 that's just synthetic.
        return ['synthetic']
    parts = [p.strip() for p in spec.split(',') if p.strip()]
    for p in parts:
        if p not in KNOWN_SOURCES:
            raise SystemExit(
                f"unknown source '{p}'. Valid: {', '.join(sorted(KNOWN_SOURCES))}"
            )
    return parts


# ── Preflight ──────────────────────────────────────────────────────────


def preflight(sources: list[str], data_dir: Path, commit_mode: bool) -> list[str]:
    """Run checks that must pass before any source is touched.

    Returns a list of failure strings. Empty list = all checks passed.
    """
    failures: list[str] = []

    # 1. DATABASE_URL must be set.
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        failures.append('DATABASE_URL is not set (read from .env)')

    # 2. Schema must exist — call ensure_schema to create-if-missing.
    try:
        from core.intel.db import ensure_schema
        ensure_schema(db_url=db_url or None)
    except Exception as exc:
        failures.append(f'cairn_intel schema could not be ensured: {exc}')

    # 3. Embedder must be reachable.
    try:
        from core.wiki.embeddings import get_embed_fn
        embed_fn = get_embed_fn()
        if embed_fn is None:
            failures.append(
                'No embedding provider reachable — start Ollama with '
                'nomic-embed-text, or set OPENAI_API_KEY.'
            )
        else:
            test_vec = embed_fn('preflight test string')
            if not test_vec or len(test_vec) != 768:
                failures.append(
                    f'Embedder returned unexpected shape: '
                    f'len={len(test_vec) if test_vec else 0}'
                )
    except Exception as exc:
        failures.append(f'embedder probe failed: {exc}')

    # 4. ANTHROPIC_API_KEY — required if any requested source uses LLM.
    needs_llm = any(KNOWN_SOURCES[s]['needs_llm'] for s in sources)
    if needs_llm and not os.getenv('ANTHROPIC_API_KEY', '').strip():
        failures.append('ANTHROPIC_API_KEY is not set but sources need Claude')

    # 5. Hand-written data files must exist for sources that require them.
    for source in sources:
        required_file = KNOWN_SOURCES[source]['needs_data_file']
        if required_file:
            path = data_dir / required_file
            if not path.exists():
                failures.append(
                    f"source '{source}' requires {path} — Toby must write "
                    'this file before the importer runs'
                )

    # 6. Source must be built. Extend built_sources as each phase lands.
    built_sources = {'synthetic', 'disputes', 'b2b_quotes', 'principles', 'xero', 'amazon', 'crm_lessons'}
    for source in sources:
        if source not in built_sources:
            failures.append(
                f"source '{source}' is not yet implemented — "
                'currently built: ' + ', '.join(sorted(built_sources))
            )

    # 7. Ledger DB reachable + Xero period populated, if xero is requested.
    if any(KNOWN_SOURCES[s]['needs_ledger_db'] for s in sources):
        ledger_failures = _probe_ledger_db()
        failures.extend(ledger_failures)

    return failures


def _probe_ledger_db() -> list[str]:
    """Confirm the Ledger DB is reachable and 2025 is populated."""
    from .sources.xero import DEFAULT_LEDGER_URL
    url = os.getenv('LEDGER_DATABASE_URL') or DEFAULT_LEDGER_URL
    failures: list[str] = []
    try:
        import psycopg2
        conn = psycopg2.connect(url, connect_timeout=5)
    except Exception as exc:
        return [
            f'xero source: cannot connect to Ledger DB at {url}: {exc}'
        ]
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MIN(date), MAX(date), COUNT(*) "
                "FROM revenue_transactions"
            )
            row = cur.fetchone()
            if not row or row[2] == 0:
                failures.append(
                    'xero source: revenue_transactions table is empty — '
                    'run D:/ledger/scripts/import_xero_invoices.py first'
                )
            else:
                min_date, max_date, n = row
                if min_date is None or min_date.year > 2025:
                    failures.append(
                        f'xero source: revenue_transactions starts at '
                        f'{min_date}, expected <= 2025-01-01'
                    )
    finally:
        conn.close()
    return failures


# ── Main loop ──────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='python -m scripts.backfill.run',
        description='Cairn counterfactual memory historical backfill importer',
    )
    parser.add_argument(
        '--source',
        default='synthetic',
        help='comma-separated list, or "all" (default: synthetic)',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Do not write to the DB (default behaviour unless --commit is passed)',
    )
    parser.add_argument(
        '--commit',
        action='store_true',
        help='Actually write rows to cairn_intel. The ONLY way writes happen.',
    )
    parser.add_argument('--max-claude-sonnet-calls', type=int, default=200)
    parser.add_argument('--max-claude-opus-calls',   type=int, default=50)
    parser.add_argument('--max-bulk-llm-calls',      type=int, default=5000)
    parser.add_argument('--sample-size', type=int, default=5,
                        help='Records to echo in dry-run output')
    parser.add_argument('--resume', default=None,
                        help='Pick up a partial run by backfill_run_id')
    parser.add_argument('--run-id', default=None,
                        help='Override the default timestamp-derived run id')
    parser.add_argument('--source-data-dir', default=None,
                        help='Directory for hand-written YAML inputs')
    args = parser.parse_args(argv)

    dry_run = not args.commit  # commit is the ONLY way to write
    if args.dry_run and args.commit:
        print('[backfill] --dry-run and --commit are mutually exclusive')
        return 2

    data_dir = Path(
        args.source_data_dir
        or (Path(__file__).parent / 'data')
    ).resolve()

    sources = _resolve_sources(args.source)

    run_id = args.resume or args.run_id or _default_run_id()
    mode = 'commit' if not dry_run else 'dry_run'

    print(f'[backfill] run_id={run_id} mode={mode} sources={sources}')
    print(f'[backfill] data_dir={data_dir}')

    # Preflight.
    failures = preflight(sources, data_dir, commit_mode=(not dry_run))
    if failures:
        print('[backfill] PREFLIGHT FAILED:')
        for f in failures:
            print(f'  - {f}')
        return 1
    print('[backfill] preflight: OK')

    # Wire up memory, budget, tagger, lesson generator.
    from core.intel.memory import CounterfactualMemory
    from core.wiki.embeddings import get_embed_fn
    from .archetype_tagger import ArchetypeTagger
    from .lesson_generator import LessonGenerator
    from .llm_budget import LLMBudget
    from .pipeline import RunContext, process_record

    embed_fn = get_embed_fn()
    memory = CounterfactualMemory(
        db_url=os.environ['DATABASE_URL'],
        embed_fn=embed_fn,
    )
    budget = LLMBudget(
        max_sonnet=args.max_claude_sonnet_calls,
        max_opus=args.max_claude_opus_calls,
        max_bulk=args.max_bulk_llm_calls,
    )
    tagger = ArchetypeTagger(budget=budget)
    lesson_gen = LessonGenerator(budget=budget)

    # Start run ledger.
    if not dry_run:
        memory.start_backfill_run(
            run_id=run_id,
            sources_requested=sources,
            mode=mode,
        )

    ctx = RunContext(run_id=run_id, dry_run=dry_run)
    processed = 0
    errors: list[dict] = []
    samples: list[str] = []

    for source_name in sources:
        source = _load_source(
            source_name,
            data_dir=data_dir,
            tagger=tagger,
            embed_fn=embed_fn,
        )
        print(f'[backfill] source: {source_name}')
        for record in source.iter_records():
            result = process_record(
                record=record,
                memory=memory,
                tagger=tagger,
                lesson_gen=lesson_gen,
                run=ctx,
            )
            processed += 1
            if not result.ok:
                errors.append({
                    'source': source_name,
                    'id': result.decision_id,
                    'error': result.error,
                })
                print(f'  [FAIL] {result.decision_id}: {result.error}')
                continue

            if processed % 100 == 0:
                print(f'[backfill] processed={processed}')

            if len(samples) < args.sample_size:
                samples.append(_format_sample(result))

    # Finish run ledger.
    if not dry_run:
        memory.finish_backfill_run(
            run_id=run_id,
            status='complete' if not errors else 'complete_with_errors',
            counts_per_source=ctx.counts_per_source,
            claude_calls_used=budget.sonnet_used + budget.opus_used,
            bulk_llm_calls_used=budget.bulk_used,
            errors=errors or None,
        )

    # Print summary.
    print()
    print(f'[backfill] done -- processed={processed} errors={len(errors)}')
    print(f'[backfill] counts_per_source={ctx.counts_per_source}')
    print(
        f'[backfill] llm budget -- sonnet={budget.sonnet_used}/{budget.max_sonnet} '
        f'opus={budget.opus_used}/{budget.max_opus} '
        f'bulk={budget.bulk_used}/{budget.max_bulk}'
    )
    if samples:
        print()
        print(f'[backfill] sample of {len(samples)} records:')
        for s in samples:
            print(s)
    return 0 if not errors else 3


def _default_run_id() -> str:
    return 'backfill-' + datetime.utcnow().strftime('%Y-%m-%d-%H%M')


def _format_sample(result) -> str:
    tag_txt = ','.join(result.tags) if result.tags else '(none)'
    lesson_txt = ''
    if result.lesson_attached:
        lesson_txt = f' lesson={result.lesson_model}'
    written_txt = 'written' if result.written else 'dry'
    return (
        f'  [{written_txt}] {result.decision_id} '
        f'({result.source_type}) tags=[{tag_txt}]{lesson_txt}\n'
        f'    {result.summary_preview}'
    )


if __name__ == '__main__':
    sys.exit(main())
