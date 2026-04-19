#!/usr/bin/env python3
"""Lint check: flag new module-scope process.env.* reads in web routes.

Per docs/audit/IDENTITY_ISOLATION_AUDIT_2026-04.md audit finding F3 /
recommendation R3. The 25 existing route handlers already read
process.env at module scope — SWC folds those values into the bundle at
build time, which is why deploy/build-deek-web.sh must pass
--build-arg DEEK_API_KEY=<real-key>. Every NEW route file that follows
the same pattern inherits the same vulnerability.

This script is informational, not a blocker — it runs against a PR's
changed files and prints a review note. The reviewer's job is to
confirm the build-arg plumbing covers any new env var, or refactor the
handler to not read from process.env at all (e.g., read a static
config file shipped with the image).

Usage:
    python scripts/lint_web_env_reads.py web/src/app/api/**/route.ts

Exit codes:
    0 — no new module-scope process.env reads (or none in listed files)
    1 — at least one file has a module-scope process.env read (advisory)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


# Matches process.env.X at the top level (not inside a function).
# We detect top-level by checking the line's leading indentation is zero
# AND the line begins a statement (const/let/var/export). This is
# deliberately naive — meant to catch the obvious pattern, not parse TS.
MODULE_SCOPE_ENV = re.compile(
    r'^\s*(export\s+)?(const|let|var)\s+\w+\s*=.*process\.env\.',
    re.MULTILINE,
)


def scan(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text(encoding='utf-8')
    except Exception as exc:
        print(f'{path}: cannot read — {exc}', file=sys.stderr)
        return []
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        # Module scope = zero leading whitespace only.
        stripped = line.lstrip()
        if stripped != line:
            continue  # indented: inside a function or block
        if MODULE_SCOPE_ENV.match(line):
            hits.append((i, line.rstrip()))
    return hits


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: lint_web_env_reads.py <file> [<file> ...]', file=sys.stderr)
        return 2

    flagged = 0
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.is_file() or p.suffix not in ('.ts', '.tsx'):
            continue
        hits = scan(p)
        if not hits:
            continue
        flagged += 1
        print(f'\n{p}:')
        for line_no, line in hits:
            print(f'  :{line_no}  {line.strip()}')
        print('  -> module-scope process.env read. SWC may inline this at build')
        print('    time. Confirm deploy/build-deek-web.sh passes the var as a')
        print('    --build-arg, OR refactor to read inside the request handler')
        print('    (noting that SWC will still fold it; the refactor is a')
        print('    reviewer-facing signal, not a runtime defence). See audit R3.')

    if flagged == 0:
        print('No module-scope process.env reads found in listed files.')
        return 0
    print(f'\n{flagged} file(s) with module-scope process.env reads — advisory.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
