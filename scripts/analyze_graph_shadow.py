#!/usr/bin/env python3
"""Analyse data/graph_shadow.jsonl and report what the graph would add.

Human summary on stdout, JSON on stderr when --json is set. Used by
the Phase C cutover decision and independently runnable.

Stats computed:

    records                  total shadow records
    records_with_graph_hit   records where the walk surfaced >= 1 memory
    records_graph_empty      records where the walk returned nothing
    graph_would_add_only_rate  fraction where graph surfaced memories
                                 NOT in the existing top-5
    mean_graph_candidates    mean count of graph candidates per query
    top_entities_surfaced    most common path entities in graph hits

Exit codes:
    0 — analysis completed
    1 — shadow log missing / unreadable
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


def _default_log() -> Path:
    repo = Path(__file__).resolve().parents[1]
    return Path(
        os.getenv('DEEK_CROSSLINK_SHADOW_LOG',
                  str(repo / 'data' / 'graph_shadow.jsonl'))
    )


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def analyse(records: list[dict]) -> dict:
    n = len(records)
    if n == 0:
        return {'records': 0}

    ts = []
    with_hit = 0
    would_add_only = 0
    cand_counts: list[int] = []
    path_counter: Counter = Counter()

    for r in records:
        try:
            ts.append(datetime.fromisoformat(
                r.get('ts', '').replace('Z', '+00:00')
            ))
        except Exception:
            pass
        graph_top = r.get('graph_top') or []
        cand_counts.append(len(graph_top))
        if not graph_top:
            continue
        with_hit += 1
        old_top = set(r.get('old_top5') or [])
        graph_ids = {g.get('chunk_id') for g in graph_top}
        # Would-add-only rate: did the graph surface anything NOT in old top-5?
        if graph_ids - old_top:
            would_add_only += 1
        for g in graph_top:
            for p in (g.get('path') or []):
                path_counter[p] += 1

    span_hours = 0.0
    if len(ts) >= 2:
        span_hours = (max(ts) - min(ts)).total_seconds() / 3600.0

    return {
        'records': n,
        'records_with_graph_hit': with_hit,
        'records_graph_empty': n - with_hit,
        'empty_rate': round((n - with_hit) / n, 3),
        'would_add_new_rate': round(would_add_only / n, 3),
        'mean_graph_candidates': (
            round(statistics.mean(cand_counts), 2) if cand_counts else 0.0
        ),
        'span_hours': round(span_hours, 2),
        'top_path_entities': path_counter.most_common(10),
    }


def render_human(stats: dict) -> str:
    if not stats or stats.get('records', 0) == 0:
        return 'No graph-shadow records. Is DEEK_CROSSLINK_SHADOW=true and retrieval happening?'
    lines = [
        f"Records logged:           {stats['records']}",
        f"With a graph hit:         {stats['records_with_graph_hit']} ({(1 - stats['empty_rate'])*100:.1f}%)",
        f"Empty walks:              {stats['records_graph_empty']} ({stats['empty_rate']*100:.1f}%)",
        f"Graph added new memories: {stats['would_add_new_rate']*100:.1f}% of queries",
        f"Mean candidates / query:  {stats['mean_graph_candidates']}",
        f"Time span:                {stats['span_hours']}h",
        '',
        'Top path entities (degenerate nodes cluster here — check for stop-list additions):',
    ]
    for p, n in stats.get('top_path_entities') or []:
        lines.append(f"    {p:30s}  {n}")
    if stats.get('empty_rate', 0) > 0.9:
        lines.append('')
        lines.append(
            'NOTE: over 90% of walks returned nothing. At small memory '
            'volume this is expected; revisit when volume grows.'
        )
    return '\n'.join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--log', type=str, default=None)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    path = Path(args.log) if args.log else _default_log()
    if not path.exists():
        print(f'No graph-shadow log at {path}', file=sys.stderr)
        return 1
    records = _read(path)
    stats = analyse(records)
    if args.json:
        print(json.dumps(stats, indent=2, default=str))
    else:
        print(render_human(stats))
    return 0


if __name__ == '__main__':
    sys.exit(main())
