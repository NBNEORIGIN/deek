"""
upload_manuals_from_drive.py — one-shot bootstrap.

Walks a local folder (default: D:\\Google Drive\\My Drive\\001 NBNE\\002 BLANKS)
and POSTs each file to the live /api/deek/manuals/upload endpoint on
Hetzner, deriving the machine name from the parent subfolder.

Run this ONCE from your PC after the /admin/manuals UI is live to seed
the corpus with whatever's currently in Google Drive. After that,
add new manuals via the web UI rather than dropping them in Google
Drive (the web becomes the canonical source — see ARCHITECTURE in the
ingest_manuals.py docstring).

Usage:
    # Dry-run — see what it would upload, no network calls
    python scripts/upload_manuals_from_drive.py --dry-run

    # Actually upload
    python scripts/upload_manuals_from_drive.py --commit

    # Override defaults
    python scripts/upload_manuals_from_drive.py \\
        --folder "D:/somewhere/manuals" \\
        --api https://deek.nbnesigns.co.uk \\
        --commit

Auth: uses DEEK_API_KEY from the local .env (or DEEK_API_KEY env var).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / '.env')
except ImportError:
    pass

DEFAULT_FOLDER = r'D:\Google Drive\My Drive\001 NBNE\002 BLANKS'
DEFAULT_API = 'https://deek.nbnesigns.co.uk'

SUPPORTED_EXTS = {
    '.pdf', '.docx', '.txt', '.md', '.log',
    '.png', '.jpg', '.jpeg', '.heic', '.gif', '.webp', '.bmp',
}
MAX_BYTES = 50 * 1024 * 1024


def _derive_machine(path: Path, root: Path) -> str:
    """First subdirectory under root → that's the machine. Files
    dropped directly in root land under '_unsorted' so you can fix
    them up via the UI later."""
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return '_unsorted'
    parts = rel.parts
    if len(parts) <= 1:
        return '_unsorted'
    return parts[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        prog='python scripts/upload_manuals_from_drive.py',
        description=__doc__.strip().split('\n')[0],
    )
    parser.add_argument('--folder', default=DEFAULT_FOLDER,
                        help=f'Source folder (default: {DEFAULT_FOLDER})')
    parser.add_argument('--api', default=DEFAULT_API,
                        help=f'API base URL (default: {DEFAULT_API})')
    parser.add_argument('--api-key', default=None,
                        help='Override DEEK_API_KEY env var.')
    parser.add_argument('--dry-run', action='store_true',
                        help='List files + machines but do not POST.')
    parser.add_argument('--commit', action='store_true',
                        help='Actually upload. Required to do anything.')
    parser.add_argument('--limit', type=int, default=0,
                        help='Stop after N successful uploads (0 = no limit).')
    args = parser.parse_args()

    if args.dry_run and args.commit:
        print('--dry-run and --commit are mutually exclusive', file=sys.stderr)
        return 2
    dry = not args.commit

    api = args.api.rstrip('/')
    api_key = (args.api_key or os.getenv('DEEK_API_KEY') or
               os.getenv('CAIRN_API_KEY') or
               os.getenv('CLAW_API_KEY') or '').strip()
    if not dry and not api_key:
        print('DEEK_API_KEY not set — cannot upload. Pass --api-key or set the env var.',
              file=sys.stderr)
        return 1

    root = Path(args.folder).expanduser()
    if not root.is_dir():
        print(f'folder not found or not a directory: {root}', file=sys.stderr)
        return 2

    files = sorted(p for p in root.rglob('*') if p.is_file())
    print(f'[bootstrap] root={root}')
    print(f'[bootstrap] api={api}')
    print(f'[bootstrap] dry={dry}')
    print(f'[bootstrap] {len(files)} files found')
    print()

    # httpx is in pyproject already; requests would also work but
    # keeping deps consistent.
    import httpx

    n_ok = 0
    n_skip = 0
    n_err = 0
    n_chunks = 0

    for path in files:
        ext = path.suffix.lower()
        if ext not in SUPPORTED_EXTS:
            print(f'  SKIP {path.relative_to(root)} — unsupported ext {ext}')
            n_skip += 1
            continue
        size = path.stat().st_size
        if size > MAX_BYTES:
            print(f'  SKIP {path.relative_to(root)} — over {MAX_BYTES // (1024 * 1024)} MB ({size // (1024 * 1024)} MB)')
            n_skip += 1
            continue
        machine = _derive_machine(path, root)

        if dry:
            print(f'  DRY  [{machine}] {path.relative_to(root)} ({size // 1024} KB)')
            n_ok += 1
        else:
            try:
                with open(path, 'rb') as fh:
                    files_part = {'file': (path.name, fh, 'application/octet-stream')}
                    data_part = {'machine': machine}
                    # Generous timeout — vision OCR on a multi-page scanned
                    # PDF can take a couple of minutes server-side.
                    r = httpx.post(
                        f'{api}/api/deek/manuals/upload',
                        files=files_part,
                        data=data_part,
                        headers={'X-API-Key': api_key},
                        timeout=httpx.Timeout(connect=10, read=300, write=60, pool=10),
                    )
            except Exception as exc:
                print(f'  ERR  {path.relative_to(root)} — {type(exc).__name__}: {exc}')
                n_err += 1
                continue
            if r.status_code != 200:
                print(f'  ERR  {path.relative_to(root)} — HTTP {r.status_code} {r.text[:120]}')
                n_err += 1
                continue
            try:
                payload = r.json()
            except Exception:
                payload = {}
            ch = payload.get('chunks', 0)
            emb = payload.get('embedded', 0)
            print(f'  OK   [{machine}] {path.relative_to(root)} — {ch} chunks ({emb} embedded)')
            n_ok += 1
            n_chunks += ch
            # Very brief breather between calls to avoid hammering
            # vision OCR if multiple image-heavy PDFs hit Anthropic
            # simultaneously.
            time.sleep(0.2)
            if args.limit and n_ok >= args.limit:
                print(f'[bootstrap] reached --limit {args.limit}, stopping early')
                break

    print()
    print('───── bootstrap summary ─────')
    print(f'  uploaded:   {n_ok}')
    print(f'  skipped:    {n_skip}')
    print(f'  errored:    {n_err}')
    if not dry:
        print(f'  chunks total: {n_chunks}')
    if dry:
        print('  (dry-run — re-run with --commit to actually upload.)')
    return 0 if n_err == 0 else 3


if __name__ == '__main__':
    sys.exit(main())
