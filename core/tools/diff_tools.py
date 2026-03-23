"""
Diff generation utilities used by the agent and approval UI.
Not a registered tool — called internally by agent.py.
"""
import difflib


def generate_unified_diff(
    file_path: str, old_str: str, new_str: str
) -> str:
    """Generate a unified diff for display in the approval UI."""
    diff = difflib.unified_diff(
        old_str.splitlines(keepends=True),
        new_str.splitlines(keepends=True),
        fromfile=f'a/{file_path}',
        tofile=f'b/{file_path}',
        lineterm='',
    )
    return '\n'.join(diff)


def generate_create_diff(file_path: str, content: str) -> str:
    """Diff representation for a new file creation."""
    lines = content.splitlines(keepends=True)
    diff_lines = [
        f'--- /dev/null',
        f'+++ b/{file_path}',
        f'@@ -0,0 +1,{len(lines)} @@',
    ] + [f'+{line.rstrip()}' for line in lines]
    return '\n'.join(diff_lines)
