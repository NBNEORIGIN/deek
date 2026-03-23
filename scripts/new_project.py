#!/usr/bin/env python3
"""
Scaffold a new CLAW project configuration.

Usage:
    python scripts/new_project.py --id myproject --name "My Project" --path /path/to/codebase
"""
import argparse
import json
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Create a new CLAW project config')
    parser.add_argument('--id', required=True, dest='project_id', help='Project ID (slug, no spaces)')
    parser.add_argument('--name', required=True, help='Human-readable project name')
    parser.add_argument('--path', required=True, help='Absolute path to codebase')
    args = parser.parse_args()

    project_dir = Path('projects') / args.project_id

    if project_dir.exists():
        print(f"Error: projects/{args.project_id} already exists")
        return

    project_dir.mkdir(parents=True)

    # Copy template config and update it
    config = {
        "name": args.name,
        "description": f"CLAW agent for {args.name}",
        "codebase_path": args.path,
        "project_type": "coding",
        "permissions": [
            "read_file", "search_code", "run_tests",
            "edit_file", "create_file"
        ],
        "model_preferences": {
            "force_model": None,
            "prefer_local_for": ["fix", "add", "update", "test"],
            "prefer_api_for": ["architect", "review", "security"]
        },
        "indexing": {
            "auto_reindex": False,
            "exclude_paths": [],
            "include_extensions": []
        }
    }

    (project_dir / 'config.json').write_text(
        json.dumps(config, indent=2), encoding='utf-8'
    )

    # Copy core.md template
    template_core = Path('projects/_template/core.md')
    if template_core.exists():
        shutil.copy(template_core, project_dir / 'core.md')

    # Create .clawignore
    (project_dir / '.clawignore').write_text(
        "# Files to exclude from indexing\nmigrations\nnode_modules\n.next\ndist\nbuild\n",
        encoding='utf-8'
    )

    print(f"Created projects/{args.project_id}/")
    print()
    print("Next steps:")
    print(f"  1. Edit projects/{args.project_id}/core.md")
    print(f"     — describe the project, rules, vocabulary, architecture")
    print(f"     — keep under 2000 tokens")
    print(f"  2. Edit projects/{args.project_id}/config.json")
    print(f"     — verify codebase_path is correct")
    print(f"     — adjust permissions as needed")
    print(f"  3. python scripts/index_project.py --project {args.project_id}")


if __name__ == '__main__':
    main()
