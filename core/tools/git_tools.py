"""
Git operations for CLAW agent.
Read operations (status, log, diff) are SAFE — auto-approved.
Write operations (add, commit, branch, stash) are REVIEW — require approval.
Push is DESTRUCTIVE — explicit confirmation required.
"""
import subprocess
from .registry import Tool, RiskLevel


def _run_git(args: list[str], cwd: str) -> dict:
    """Run a git command and return structured result."""
    try:
        result = subprocess.run(
            ['git'] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            'success': result.returncode == 0,
            'stdout': result.stdout.strip(),
            'stderr': result.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {'success': False, 'stdout': '', 'stderr': 'Git command timed out'}
    except FileNotFoundError:
        return {'success': False, 'stdout': '', 'stderr': 'Git not found in PATH'}


def _git_status(project_root: str) -> str:
    result = _run_git(['status', '--short', '--branch'], project_root)
    if not result['success']:
        return f"Git error: {result['stderr']}"
    return result['stdout'] or "Working tree clean"


def _git_diff(
    project_root: str,
    staged: bool = False,
    file_path: str = '',
) -> str:
    args = ['diff']
    if staged:
        args.append('--staged')
    if file_path:
        args.extend(['--', file_path])
    result = _run_git(args, project_root)
    if not result['success']:
        return f"Git error: {result['stderr']}"
    return result['stdout'] or "No changes"


def _git_log(project_root: str, limit: int = 10) -> str:
    result = _run_git(
        ['log', f'--max-count={limit}', '--oneline', '--graph', '--decorate'],
        project_root,
    )
    if not result['success']:
        return f"Git error: {result['stderr']}"
    return result['stdout'] or "No commits yet"


def _git_add(project_root: str, file_path: str = '.') -> str:
    result = _run_git(['add', file_path], project_root)
    if not result['success']:
        return f"Git error: {result['stderr']}"
    staged = _run_git(['diff', '--staged', '--stat'], project_root)
    return f"Staged: {file_path}\n{staged['stdout']}"


def _git_commit(project_root: str, message: str = '') -> str:
    if not message:
        return "Error: commit message required"
    result = _run_git(['commit', '-m', message], project_root)
    if not result['success']:
        return f"Git error: {result['stderr']}"
    return result['stdout']


def _git_push(
    project_root: str,
    remote: str = 'origin',
    branch: str = '',
) -> str:
    args = ['push', remote]
    if branch:
        args.append(branch)
    result = _run_git(args, project_root)
    if not result['success']:
        return f"Git push failed: {result['stderr']}"
    return result['stdout'] or "Pushed successfully"


def _git_branch(
    project_root: str,
    action: str = 'list',
    name: str = '',
) -> str:
    if action == 'list':
        result = _run_git(['branch', '-a'], project_root)
    elif action == 'create':
        if not name:
            return "Error: name required for create"
        result = _run_git(['checkout', '-b', name], project_root)
    elif action == 'switch':
        if not name:
            return "Error: name required for switch"
        result = _run_git(['checkout', name], project_root)
    else:
        return f"Unknown action: {action}. Use list|create|switch"
    if not result['success']:
        return f"Git error: {result['stderr']}"
    return result['stdout']


def _git_stash(
    project_root: str,
    action: str = 'push',
    message: str = '',
) -> str:
    args = ['stash', action]
    if action == 'push' and message:
        args.extend(['-m', message])
    result = _run_git(args, project_root)
    if not result['success']:
        return f"Git error: {result['stderr']}"
    return result['stdout'] or f"Stash {action} complete"


# ── Tool definitions ─────────────────────────────────────────────────────────

git_status_tool = Tool(
    name='git_status',
    description=(
        'Get current git status — shows modified, staged, and untracked files. '
        'Use before committing to see what has changed.'
    ),
    risk_level=RiskLevel.SAFE,
    fn=_git_status,
    required_permission='git_status',
)

git_diff_tool = Tool(
    name='git_diff',
    description=(
        'Show git diff of changes. Unstaged by default. '
        'Pass staged=true for staged changes. '
        'Pass file_path to diff a specific file.'
    ),
    risk_level=RiskLevel.SAFE,
    fn=_git_diff,
    required_permission='git_diff',
)

git_log_tool = Tool(
    name='git_log',
    description=(
        'Show recent git commit history with branch graph. '
        'Pass limit to control how many commits to show (default 10).'
    ),
    risk_level=RiskLevel.SAFE,
    fn=_git_log,
    required_permission='git_log',
)

git_add_tool = Tool(
    name='git_add',
    description=(
        'Stage files for commit. '
        'Pass file_path="." to stage all changes, '
        'or a specific path to stage one file.'
    ),
    risk_level=RiskLevel.REVIEW,
    fn=_git_add,
    required_permission='git_add',
)

git_commit_tool = Tool(
    name='git_commit',
    description=(
        'Commit staged changes with a message. '
        'Follow the project commit convention: '
        'feat(scope): description or fix(scope): description.'
    ),
    risk_level=RiskLevel.REVIEW,
    fn=_git_commit,
    required_permission='git_commit',
)

git_push_tool = Tool(
    name='git_push',
    description=(
        'Push commits to remote repository. '
        'Defaults to origin. '
        'Pass remote and branch to override.'
    ),
    risk_level=RiskLevel.DESTRUCTIVE,
    fn=_git_push,
    required_permission='git_push',
)

git_branch_tool = Tool(
    name='git_branch',
    description=(
        'List, create, or switch git branches. '
        'action=list (default) | create | switch. '
        'Pass name for create/switch.'
    ),
    risk_level=RiskLevel.REVIEW,
    fn=_git_branch,
    required_permission='git_branch',
)

git_stash_tool = Tool(
    name='git_stash',
    description=(
        'Stash or restore uncommitted changes. '
        'action=push (default) | pop | list. '
        'Pass message for named stash.'
    ),
    risk_level=RiskLevel.REVIEW,
    fn=_git_stash,
    required_permission='git_stash',
)
