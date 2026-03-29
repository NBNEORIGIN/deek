"""
File read, write, and create tools.
All paths are validated against the project root before execution.
"""
import os
from pathlib import Path
from .registry import Tool, RiskLevel

# D:\claw\projects is always allowed so agents can read their own
# core.md, config.json, and skill definitions regardless of where
# their codebase_path points.
_PROJECTS_DIR = Path(__file__).resolve().parent.parent.parent / 'projects'


def _resolve_safe(project_root: Path, file_path: str) -> Path:
    """Resolve file_path against project_root, blocking traversal.

    Accepts both relative paths (``backend/urls.py``) and absolute paths
    (``D:/nbne_business/nbne_platform/backend/urls.py``) as long as the
    resolved result lives under either ``project_root`` or the shared
    ``projects/`` directory.  Uses ``os.path.normcase`` so drive-letter
    case and separator differences on Windows don't cause false rejections.
    """
    candidate = Path(file_path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (project_root / file_path).resolve()

    norm_resolved = os.path.normcase(str(resolved))
    allowed_roots = [
        os.path.normcase(str(project_root.resolve())),
        os.path.normcase(str(_PROJECTS_DIR)),
    ]
    if any(norm_resolved.startswith(r) for r in allowed_roots):
        return resolved

    raise PermissionError(
        f"Path '{file_path}' is outside project root "
        f"'{project_root.resolve()}' — rejected."
    )


def _read_file(project_root: str, file_path: str) -> str:
    target = _resolve_safe(Path(project_root), file_path)
    if not target.exists():
        return f"ERROR: File not found: {file_path}"
    return target.read_text(encoding='utf-8', errors='replace')


def _edit_file(
    project_root: str, file_path: str,
    old_str: str, new_str: str, reason: str = ''
) -> str:
    target = _resolve_safe(Path(project_root), file_path)
    if not target.exists():
        return f"ERROR: File not found: {file_path}"

    content = target.read_text(encoding='utf-8', errors='replace')
    if old_str not in content:
        return (
            f"ERROR: String not found in {file_path}. "
            f"Ensure old_str exactly matches file content."
        )

    new_content = content.replace(old_str, new_str, 1)
    target.write_text(new_content, encoding='utf-8')
    return f"OK: Edited {file_path}"


def _create_file(
    project_root: str, file_path: str, content: str, reason: str = ''
) -> str:
    target = _resolve_safe(Path(project_root), file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    return f"OK: Created {file_path}"


read_file_tool = Tool(
    name='read_file',
    description=(
        'Read the complete contents of a file. '
        'Use when you need to see a full file to make changes.'
    ),
    risk_level=RiskLevel.SAFE,
    fn=_read_file,
    required_permission='read_file',
)

edit_file_tool = Tool(
    name='edit_file',
    description=(
        'Replace a specific string in a file with new content. '
        'old_str must match exactly. '
        'Use for targeted edits — prefer small, focused changes.'
    ),
    risk_level=RiskLevel.REVIEW,
    fn=_edit_file,
    required_permission='edit_file',
)

create_file_tool = Tool(
    name='create_file',
    description=(
        'Create a new file with the given content. '
        'Will create parent directories if needed.'
    ),
    risk_level=RiskLevel.REVIEW,
    fn=_create_file,
    required_permission='create_file',
)
