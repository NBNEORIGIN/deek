"""
deploy_wiki.py — Auto-commit and push new wiki articles, then trigger Hetzner sync.

Called by the CairnWikiDeploy scheduled task (every 30 min) or directly
after wiki generation completes.

Flow:
  1. Check for new/modified wiki/modules/*.md files in git
  2. If any found, commit + push to GitHub
  3. POST to Hetzner Cairn /admin/wiki-sync (triggers git pull + embed)

Environment:
  CLAW_API_KEY           — Cairn API key (same on all instances)
  CAIRN_HETZNER_URL      — e.g. https://cairn.nbnesigns.co.uk or http://178.104.1.152:8765
  CAIRN_HETZNER_API_KEY  — API key for Hetzner Cairn (can be same as CLAW_API_KEY)
"""

from __future__ import annotations

import os
import subprocess
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_ROOT / '.env', override=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)


def _run(cmd: list[str], cwd: Path = _ROOT) -> tuple[int, str]:
    """Run a shell command, return (returncode, combined output)."""
    result = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def get_changed_wiki_files() -> list[str]:
    """Return list of new or modified wiki/modules/*.md files (unstaged + untracked)."""
    code, out = _run(['git', 'status', '--porcelain', 'wiki/modules/'])
    if code != 0:
        log.warning('git status failed: %s', out)
        return []
    files = []
    for line in out.splitlines():
        status = line[:2].strip()
        path = line[3:].strip()
        if path.endswith('.md') and status in ('M', 'A', '??', 'AM', 'MM'):
            files.append(path)
    return files


def commit_and_push(changed_files: list[str]) -> bool:
    """Stage, commit, and push the changed wiki files. Returns True on success."""
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    # Stage only the wiki files (never `git add .`)
    for f in changed_files:
        code, out = _run(['git', 'add', f])
        if code != 0:
            log.error('git add failed for %s: %s', f, out)
            return False

    # Commit
    msg = f'chore(wiki): auto-deploy {len(changed_files)} article(s) [{ts}]'
    code, out = _run(['git', 'commit', '-m', msg])
    if code != 0:
        if 'nothing to commit' in out:
            log.info('Nothing new to commit.')
            return True
        log.error('git commit failed: %s', out)
        return False
    log.info('Committed: %s', msg)

    # Push
    code, out = _run(['git', 'push', 'origin', 'master'])
    if code != 0:
        log.error('git push failed: %s', out)
        return False
    log.info('Pushed to origin/master')
    return True


def notify_hetzner() -> bool:
    """Trigger wiki sync on Hetzner via SSH → internal API call.

    The Cairn API on Hetzner is not publicly exposed, so we SSH in and
    call localhost:8765 from within the server. This also means no nginx
    changes are needed and the endpoint is never internet-accessible.
    """
    hetzner_host = os.getenv('CAIRN_HETZNER_HOST', 'root@178.104.1.152')
    api_key = os.getenv('CAIRN_HETZNER_API_KEY') or os.getenv('CLAW_API_KEY', 'claw-dev-key-change-in-production')

    log.info('Notifying Hetzner Cairn via SSH: %s', hetzner_host)
    # git pull on the HOST first (wiki/modules is volume-mounted into container),
    # then call /admin/wiki-sync to embed the new files
    remote_cmd = (
        'cd /opt/nbne/cairn'
        ' && git pull --ff-only origin master'
        f' && curl -s -X POST http://localhost:8765/admin/wiki-sync'
        f' -H "X-API-Key: {api_key}"'
        f' --max-time 180'
    )
    cmd = [
        'ssh',
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'ConnectTimeout=10',
        hetzner_host,
        remote_cmd,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=200)
        if result.returncode != 0:
            log.warning('SSH failed: %s', result.stderr.strip())
            return False
        output = result.stdout.strip()
        try:
            data = json.loads(output)
            log.info(
                'Hetzner sync: git=%s, embedded=%d, skipped=%d, errors=%d',
                data.get('git_pull', '?'),
                data.get('embedded', 0),
                data.get('skipped', 0),
                data.get('errors', 0),
            )
        except Exception:
            log.info('Hetzner response: %s', output[:200])
        return True
    except subprocess.TimeoutExpired:
        log.warning('Hetzner SSH timed out')
        return False
    except Exception as exc:
        log.warning('Hetzner sync failed: %s', exc)
        return False


def main() -> int:
    log.info('=== Cairn Wiki Deploy ===')

    changed = get_changed_wiki_files()
    if not changed:
        log.info('No new or modified wiki articles — nothing to deploy')
        return 0

    log.info('Changed articles (%d): %s', len(changed), ', '.join(changed))

    if not commit_and_push(changed):
        log.error('Commit/push failed — aborting')
        return 1

    # Notify Hetzner to sync (best-effort)
    notify_hetzner()

    log.info('Wiki deploy complete')
    return 0


if __name__ == '__main__':
    sys.exit(main())
