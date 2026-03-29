"""
Command execution tools — run tests, Django management commands.
All execution is sandboxed to the project root.
"""
import os
import subprocess
from pathlib import Path
from .registry import Tool, RiskLevel


def _run_tests(project_root: str, test_path: str = '') -> str:
    """Run pytest for the project. Returns stdout + stderr."""
    cmd = ['python', '-m', 'pytest', '-v', '--tb=short']
    if test_path:
        cmd.append(test_path)

    try:
        result = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
        # Truncate very long test output
        lines = output.splitlines()
        if len(lines) > 200:
            lines = lines[:200]
            lines.append('... (truncated — see full output in terminal)')
        return '\n'.join(lines)
    except subprocess.TimeoutExpired:
        return 'ERROR: Tests timed out after 120 seconds'
    except FileNotFoundError:
        return 'ERROR: pytest not found. Is the virtualenv activated?'


_BLOCKED_PATTERNS = [
    'rm -rf /', 'format c:', 'del /f /s /q c:\\',
    'shutdown', 'reboot', 'dd if=',
    # Never let CLAW kill its own web UI or API server
    'taskkill /pid 1 ', 'stop-process',
]


def _is_dangerous(command: str) -> bool:
    cmd = command.lower()
    return any(p in cmd for p in _BLOCKED_PATTERNS)


def _run_command(
    project_root: str,
    command: str,
    working_dir: str = '',
    reason: str = '',
) -> str:
    """
    Run a shell command in the project root or specified subdirectory.
    Destructive risk — requires explicit user approval before execution.
    """
    if _is_dangerous(command):
        return f"ERROR: command blocked for safety: {command}"

    cwd = Path(project_root)
    if working_dir:
        cwd = (cwd / working_dir).resolve()
        if not os.path.normcase(str(cwd)).startswith(os.path.normcase(str(Path(project_root).resolve()))):
            return 'ERROR: working_dir is outside project root — rejected.'

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = result.stdout + result.stderr
        if len(output) > 4000:
            output = output[:4000] + '\n... (truncated)'
        return output or '(no output)'
    except subprocess.TimeoutExpired:
        return 'ERROR: Command timed out after 60 seconds'


def _run_migration(project_root: str, app_name: str = '') -> str:
    """
    Run Django makemigrations + migrate.
    Always run both — never one without the other.
    """
    results = []
    for cmd in [
        ['python', 'manage.py', 'makemigrations'] + ([app_name] if app_name else []),
        ['python', 'manage.py', 'migrate'] + ([app_name] if app_name else []),
    ]:
        try:
            result = subprocess.run(
                cmd, cwd=project_root,
                capture_output=True, text=True, timeout=60,
            )
            results.append(' '.join(cmd))
            results.append(result.stdout + result.stderr)
        except subprocess.TimeoutExpired:
            results.append(f'ERROR: {" ".join(cmd)} timed out')

    return '\n'.join(results)


run_tests_tool = Tool(
    name='run_tests',
    description=(
        'Run the project test suite with pytest. '
        'Specify test_path to run a specific file or directory. '
        'Leave empty to run all tests.'
    ),
    risk_level=RiskLevel.SAFE,
    fn=_run_tests,
    required_permission='run_tests',
)

run_command_tool = Tool(
    name='run_command',
    description=(
        'Run a shell command in the project directory. '
        'Use for build commands, npm scripts, etc. '
        'Will require explicit user approval before execution.'
    ),
    risk_level=RiskLevel.DESTRUCTIVE,
    fn=_run_command,
    required_permission='run_command',
)

run_migration_tool = Tool(
    name='run_migration',
    description=(
        'Run Django makemigrations and migrate. '
        'Specify app_name to migrate a single app, '
        'or leave empty to run all migrations.'
    ),
    risk_level=RiskLevel.REVIEW,
    fn=_run_migration,
    required_permission='run_migration',
)


def _check_server(project_root: str) -> str:
    """Check if Django or Next.js dev server is currently running."""
    try:
        import psutil
    except ImportError:
        return "psutil not installed — run: pip install psutil"

    servers = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = ' '.join(proc.info.get('cmdline') or [])
            if 'manage.py' in cmdline and 'runserver' in cmdline:
                servers.append(f"Django: PID {proc.info['pid']} — {cmdline[:100]}")
            elif 'next' in cmdline.lower() and 'dev' in cmdline:
                servers.append(f"Next.js: PID {proc.info['pid']} — {cmdline[:100]}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    if servers:
        return "Running servers:\n" + '\n'.join(servers)
    return "No Django or Next.js dev servers detected"


check_server_tool = Tool(
    name='check_server',
    description=(
        'Check if Django or Next.js dev server is currently running. '
        'Returns process info (PID, command line) for any found servers.'
    ),
    risk_level=RiskLevel.SAFE,
    fn=_check_server,
    required_permission='check_server',
)
