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
        'query_amazon_intel',
        'get_module_snapshot', 'search_emails', 'search_wiki',
    ],
    'readonly': [
        'read_file', 'search_code', 'query_amazon_intel',
        'get_module_snapshot', 'search_emails', 'search_wiki',
    ],
    'ops': [
        'read_file', 'search_code', 'run_command',
        'edit_file', 'create_file',
        'get_module_snapshot', 'search_emails', 'search_wiki',
    ],
    'creative': [
        'read_file', 'search_code', 'edit_file', 'create_file',
        'generate_video', 'generate_image',
        'search_wiki',
    ],
    'business': [
        'read_file', 'search_code',
        'query_amazon_intel',
        'get_module_snapshot', 'search_emails', 'search_wiki',
        'retrieve_similar_decisions',
        'search_crm',
        'analyze_enquiry',
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
    'generate_image': {
        'type': 'object',
        'properties': {
            'prompt': {
                'type': 'string',
                'description': 'Detailed description of the image to generate',
            },
            'output_path': {
                'type': 'string',
                'description': 'Optional: absolute path for the output PNG file',
            },
            'width': {
                'type': 'integer',
                'description': 'Image width in pixels (multiple of 64, default 1024)',
                'default': 1024,
            },
            'height': {
                'type': 'integer',
                'description': 'Image height in pixels (multiple of 64, default 1024)',
                'default': 1024,
            },
            'num_inference_steps': {
                'type': 'integer',
                'description': 'Steps — 4 is fast/good, up to 8 for more detail',
                'default': 4,
            },
            'num_images': {
                'type': 'integer',
                'description': 'Number of image variations to generate (default 1)',
                'default': 1,
            },
            'seed': {
                'type': 'integer',
                'description': 'Random seed for reproducible results',
            },
        },
        'required': ['prompt'],
    },
    # ── Git tools ────────────────────────────────────────────────────────────
    'git_status': {
        'type': 'object',
        'properties': {},
        'required': [],
    },
    'git_diff': {
        'type': 'object',
        'properties': {
            'staged': {
                'type': 'boolean',
                'description': 'Show staged changes (default: false)',
            },
            'file_path': {
                'type': 'string',
                'description': 'Specific file to diff (optional)',
            },
        },
        'required': [],
    },
    'git_log': {
        'type': 'object',
        'properties': {
            'limit': {
                'type': 'integer',
                'description': 'Number of commits to show (default: 10)',
            },
        },
        'required': [],
    },
    'git_add': {
        'type': 'object',
        'properties': {
            'file_path': {
                'type': 'string',
                'description': 'File or path to stage. Use "." for all changes',
            },
        },
        'required': [],
    },
    'git_commit': {
        'type': 'object',
        'properties': {
            'message': {
                'type': 'string',
                'description': 'Commit message — follow project convention e.g. feat(scope): description',
            },
        },
        'required': ['message'],
    },
    'git_push': {
        'type': 'object',
        'properties': {
            'remote': {
                'type': 'string',
                'description': 'Remote name (default: origin)',
            },
            'branch': {
                'type': 'string',
                'description': 'Branch name (optional, uses current branch)',
            },
        },
        'required': [],
    },
    'git_branch': {
        'type': 'object',
        'properties': {
            'action': {
                'type': 'string',
                'description': 'list | create | switch (default: list)',
            },
            'name': {
                'type': 'string',
                'description': 'Branch name (required for create/switch)',
            },
        },
        'required': [],
    },
    'git_stash': {
        'type': 'object',
        'properties': {
            'action': {
                'type': 'string',
                'description': 'push | pop | list (default: push)',
            },
            'message': {
                'type': 'string',
                'description': 'Stash description (optional, for push)',
            },
        },
        'required': [],
    },
    # ── Web tools ─────────────────────────────────────────────────────────────
    'web_fetch': {
        'type': 'object',
        'properties': {
            'url': {
                'type': 'string',
                'description': 'URL to fetch (http/https only)',
            },
        },
        'required': ['url'],
    },
    'web_check_status': {
        'type': 'object',
        'properties': {
            'url': {
                'type': 'string',
                'description': 'URL to health-check',
            },
        },
        'required': ['url'],
    },
    'web_search': {
        'type': 'object',
        'properties': {
            'query': {
                'type': 'string',
                'description': 'Search query — good for docs, error messages, library info',
            },
        },
        'required': ['query'],
    },
    # ── Amazon Intelligence ──────────────────────────────────────────────────
    'query_amazon_intel': {
        'type': 'object',
        'properties': {
            'sql': {
                'type': 'string',
                'description': (
                    'PostgreSQL SELECT query against ami_* tables. '
                    'Tables: ami_sku_mapping, ami_flatfile_data, '
                    'ami_business_report_data, ami_advertising_data, '
                    'ami_listing_snapshots, ami_weekly_reports. '
                    'See tool description for full schema.'
                ),
            },
            'limit': {
                'type': 'integer',
                'description': 'Max rows to return (default 50)',
                'default': 50,
            },
        },
        'required': ['sql'],
    },
    # ── Cairn federation + memory ────────────────────────────────────────────
    'get_module_snapshot': {
        'type': 'object',
        'properties': {
            'module': {
                'type': 'string',
                'description': (
                    'Name of the module to fetch (e.g. "manufacture", '
                    '"crm", "ledger", "render"). Omit to list all '
                    'registered modules.'
                ),
            },
        },
        'required': [],
    },
    'search_emails': {
        'type': 'object',
        'properties': {
            'query': {
                'type': 'string',
                'description': (
                    'Free-text search term — sender name, subject keyword, '
                    'topic, company, etc.'
                ),
            },
            'limit': {
                'type': 'integer',
                'description': 'Max results to return (default 5, max 20)',
                'default': 5,
            },
        },
        'required': ['query'],
    },
    'search_wiki': {
        'type': 'object',
        'properties': {
            'query': {
                'type': 'string',
                'description': (
                    'Free-text search term — process, SOP, module name, '
                    'supplier, decision, etc.'
                ),
            },
            'limit': {
                'type': 'integer',
                'description': 'Max results to return (default 5, max 20)',
                'default': 5,
            },
        },
        'required': ['query'],
    },
    'retrieve_similar_decisions': {
        'type': 'object',
        'properties': {
            'query': {
                'type': 'string',
                'description': (
                    'Free-text description of the new situation — a quote '
                    'request, a dispute, a production choice, a pricing '
                    'question.'
                ),
            },
            'limit': {
                'type': 'integer',
                'description': 'Max past decisions to return (default 5, max 10)',
                'default': 5,
            },
            'sources': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': (
                    'Optional source_type filter — one or more of: '
                    'dispute, b2b_quote, email, m_number, xero, amazon, '
                    'principle. Omit to search all sources.'
                ),
            },
        },
        'required': ['query'],
    },
    'search_crm': {
        'type': 'object',
        'properties': {
            'query': {
                'type': 'string',
                'description': (
                    'Free-text search term — client name, project name, '
                    'material, topic, lesson keyword.'
                ),
            },
            'limit': {
                'type': 'integer',
                'description': 'Max results to return (default 5, max 20)',
                'default': 5,
            },
            'types': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': (
                    "Optional source_type filter — one or more of: "
                    "'project', 'client', 'material', 'kb' (lessons), "
                    "'quote', 'email'. Omit to search all."
                ),
            },
        },
        'required': ['query'],
    },
    'analyze_enquiry': {
        'type': 'object',
        'properties': {
            'enquiry': {
                'type': 'string',
                'description': (
                    'The full enquiry text — paste the email, phone '
                    'note, or quote request verbatim (including '
                    'sender, subject, body if available). The tool '
                    'will retrieve matching CRM context + counterfactual '
                    'memory + wiki policy + rate card, classify job '
                    'size, and synthesise a size-calibrated brief '
                    'with citations. Call this whenever the user '
                    'asks to "analyse", "assess", "review", "look at", '
                    '"how should I handle", or "help me respond to" '
                    'any client enquiry or pastes an email-shaped '
                    'message asking for advice.'
                ),
            },
            'focus': {
                'type': 'string',
                'description': (
                    'Optional focus hint like "pricing", "dispute", '
                    '"scope", "timeline" to bias retrieval toward a '
                    'specific archetype.'
                ),
            },
        },
        'required': ['enquiry'],
    },
    # ── Server check ─────────────────────────────────────────────────────────
    'check_server': {
        'type': 'object',
        'properties': {},
        'required': [],
    },
    # ── Video (existing, kept here for reference) ─────────────────────────────
    'generate_video': {
        'type': 'object',
        'properties': {
            'prompt': {
                'type': 'string',
                'description': 'Detailed description of the video to generate',
            },
            'output_path': {
                'type': 'string',
                'description': 'Optional: absolute path for the output MP4 file',
            },
            'negative_prompt': {
                'type': 'string',
                'description': 'Things to avoid in the video',
            },
            'num_frames': {
                'type': 'integer',
                'description': 'Number of frames (17=2s, 33=4s, 49=6s). Must be 4k+1.',
                'default': 49,
            },
            'num_inference_steps': {
                'type': 'integer',
                'description': 'Quality/speed trade-off (20–50). Default 25.',
                'default': 25,
            },
            'guidance_scale': {
                'type': 'number',
                'description': 'Prompt adherence (4–10). Default 6.',
                'default': 6.0,
            },
            'seed': {
                'type': 'integer',
                'description': 'Random seed for reproducible results',
            },
        },
        'required': ['prompt'],
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
