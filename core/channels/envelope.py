from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum


class Channel(str, Enum):
    VSCODE = 'vscode'
    WEB = 'web'
    WHATSAPP = 'whatsapp'


class MessageRole(str, Enum):
    USER = 'user'
    ASSISTANT = 'assistant'
    SYSTEM = 'system'
    TOOL_RESULT = 'tool_result'


@dataclass
class MessageEnvelope:
    """
    Standard message format used by all channels.
    All channel handlers convert their native format to this
    before passing to the agent core.
    """
    content: str
    channel: Channel
    project_id: str
    session_id: str
    role: MessageRole = MessageRole.USER
    timestamp: datetime = field(default_factory=datetime.utcnow)

    # Optional context from the channel
    active_file: Optional[str] = None
    selected_text: Optional[str] = None
    active_directory: Optional[str] = None

    # Tool approval response (when user approves/rejects a tool call)
    tool_approval: Optional[dict] = None
    # {'tool_call_id': str, 'approved': bool, 'modified_input': dict}

    metadata: dict = field(default_factory=dict)


@dataclass
class AgentResponse:
    """
    Standard response from agent core to all channels.
    Each channel renders this in its own way.
    """
    content: str
    session_id: str
    project_id: str

    # If agent wants to use a tool, it populates this.
    # Channel renders an approval UI before executing.
    pending_tool_call: Optional[dict] = None
    # {
    #   'tool_call_id': str,
    #   'tool_name': str,
    #   'description': str,
    #   'diff_preview': str,
    #   'input': dict,
    #   'risk_level': str,   # 'safe'|'review'|'destructive'
    #   'auto_approve': bool,
    # }

    model_used: str = ''
    tokens_used: int = 0
    cost_usd: float = 0.0

    timestamp: datetime = field(default_factory=datetime.utcnow)
