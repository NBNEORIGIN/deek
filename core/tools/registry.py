from dataclasses import dataclass
from typing import Callable, Optional
from enum import Enum


class RiskLevel(str, Enum):
    SAFE = 'safe'                # Read-only. Execute without approval.
    REVIEW = 'review'            # Modifies files. Show diff, require approval.
    DESTRUCTIVE = 'destructive'  # Deletes/runs commands. Explicit confirm.


@dataclass
class Tool:
    name: str
    description: str
    risk_level: RiskLevel
    fn: Callable
    required_permission: str


DEFAULT_PERMISSIONS = {
    'coding': [
        'read_file', 'search_code', 'run_tests',
        'edit_file', 'create_file', 'run_migration',
    ],
    'readonly': [
        'read_file', 'search_code',
    ],
    'ops': [
        'read_file', 'search_code', 'run_command',
        'edit_file', 'create_file',
    ],
}

TOOL_SCHEMAS: dict[str, dict] = {
    'read_file': {
        'type': 'object',
        'properties': {
            'file_path': {
                'type': 'string',
                'description': 'Path to file relative to project root',
            },
        },
        'required': ['file_path'],
    },
    'edit_file': {
        'type': 'object',
        'properties': {
            'file_path': {'type': 'string'},
            'old_str': {
                'type': 'string',
                'description': 'Exact string to replace',
            },
            'new_str': {
                'type': 'string',
                'description': 'Replacement string',
            },
            'reason': {
                'type': 'string',
                'description': 'Why this change is being made',
            },
        },
        'required': ['file_path', 'old_str', 'new_str'],
    },
    'create_file': {
        'type': 'object',
        'properties': {
            'file_path': {'type': 'string'},
            'content': {'type': 'string'},
            'reason': {'type': 'string'},
        },
        'required': ['file_path', 'content'],
    },
    'search_code': {
        'type': 'object',
        'properties': {
            'query': {
                'type': 'string',
                'description': 'Search query or regex pattern',
            },
            'file_pattern': {
                'type': 'string',
                'description': 'File glob pattern e.g. "*.py"',
            },
        },
        'required': ['query'],
    },
    'run_tests': {
        'type': 'object',
        'properties': {
            'test_path': {
                'type': 'string',
                'description': 'Test file or directory to run',
            },
        },
        'required': [],
    },
    'run_command': {
        'type': 'object',
        'properties': {
            'command': {'type': 'string'},
            'working_dir': {'type': 'string'},
            'reason': {'type': 'string'},
        },
        'required': ['command'],
    },
    'run_migration': {
        'type': 'object',
        'properties': {
            'app_name': {
                'type': 'string',
                'description': 'Django app name (blank = all)',
            },
        },
        'required': [],
    },
}


class ToolRegistry:

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def get_permitted_tools(self, project_config: dict) -> list[Tool]:
        """Return tools this project is permitted to use."""
        project_type = project_config.get('project_type', 'coding')
        default_permissions = DEFAULT_PERMISSIONS.get(
            project_type, DEFAULT_PERMISSIONS['coding']
        )
        permitted = project_config.get('permissions', default_permissions)
        return [
            tool for name, tool in self._tools.items()
            if name in permitted
        ]

    def describe_for_model(self, project_config: dict) -> list[dict]:
        """
        Return tool descriptions in the format expected by
        Claude's tool use API and Ollama's function calling.
        """
        tools = self.get_permitted_tools(project_config)
        return [
            {
                'name': tool.name,
                'description': tool.description,
                'input_schema': TOOL_SCHEMAS.get(tool.name, {'type': 'object', 'properties': {}}),
            }
            for tool in tools
        ]
