#!/usr/bin/env python3
"""
Index a project codebase into pgvector.

Run after:
    - First setup of a project
    - After major code changes
    - After a WIGGUM build loop completes

Usage:
    python scripts/index_project.py --project phloe
    python scripts/index_project.py --project phloe --force
    python scripts/index_project.py --project manufacturing --path /custom/path
"""
import argparse
import json
import os
import sys
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from core.context.indexer import CodeIndexer


def main():
    parser = argparse.ArgumentParser(
        description='Index a project codebase into pgvector'
    )
    parser.add_argument(
        '--project', required=True,
        help='Project ID (must match a directory in projects/)'
    )
    parser.add_argument(
        '--force', action='store_true',
        help='Force re-index all files, even unchanged ones'
    )
    parser.add_argument(
        '--path',
        help='Override codebase_path from config.json'
    )
    args = parser.parse_args()

    config_path = Path('projects') / args.project / 'config.json'
    if not config_path.exists():
        print(f"Error: No config found at {config_path}")
        print(f"Run: cp projects/_template/config.json projects/{args.project}/config.json")
        sys.exit(1)

    config = json.loads(config_path.read_text())
    codebase_path = args.path or config.get('codebase_path')

    if not codebase_path:
        print("Error: config.json must include 'codebase_path', or pass --path")
        sys.exit(1)

    if not Path(codebase_path).exists():
        print(f"Error: Codebase path not found: {codebase_path}")
        sys.exit(1)

    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        print("Error: DATABASE_URL not set. Check your .env file.")
        sys.exit(1)

    print(f"Project:  {args.project}")
    print(f"Codebase: {codebase_path}")
    print(f"Force:    {args.force}")
    print(f"DB:       {db_url.split('@')[1] if '@' in db_url else db_url}")
    print()

    indexer = CodeIndexer(
        project_id=args.project,
        codebase_path=codebase_path,
        db_url=db_url,
    )
    indexer.index_project(force_reindex=args.force)


if __name__ == '__main__':
    main()
