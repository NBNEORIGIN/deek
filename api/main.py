import os
import json
import asyncio
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

from core.agent import ClawAgent
from core.channels.envelope import MessageEnvelope, Channel
from api.middleware.auth import verify_api_key

app = FastAPI(
    title="CLAW — Coding and Language Agent Workbench",
    version="0.1.0",
    description="Sovereign AI coding agent with permanent per-project context",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten to specific origins in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# Agent registry — one instance per project, loaded on first request
_agents: dict[str, ClawAgent] = {}


def get_agent(project_id: str) -> ClawAgent:
    if project_id not in _agents:
        config_path = Path('projects') / project_id / 'config.json'
        if not config_path.exists():
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Project '{project_id}' not found. "
                    f"Create projects/{project_id}/config.json "
                    f"and projects/{project_id}/core.md"
                ),
            )
        config = json.loads(config_path.read_text())
        _agents[project_id] = ClawAgent(
            project_id=project_id,
            config=config,
        )
    return _agents[project_id]


# ─── Request/Response models ────────────────────────────────────────────────

class ChatRequest(BaseModel):
    content: str
    project_id: str
    session_id: str
    channel: str = 'web'
    active_file: Optional[str] = None
    selected_text: Optional[str] = None
    tool_approval: Optional[dict] = None


class IndexRequest(BaseModel):
    force: bool = False


# ─── Routes ─────────────────────────────────────────────────────────────────

@app.post("/chat")
async def chat(
    request: ChatRequest,
    _: bool = Depends(verify_api_key),
):
    """Main chat endpoint. All three interfaces hit this."""
    agent = get_agent(request.project_id)

    envelope = MessageEnvelope(
        content=request.content,
        channel=Channel(request.channel),
        project_id=request.project_id,
        session_id=request.session_id,
        active_file=request.active_file,
        selected_text=request.selected_text,
        tool_approval=request.tool_approval,
    )

    response = await agent.process(envelope)

    return {
        'content': response.content,
        'session_id': response.session_id,
        'pending_tool_call': response.pending_tool_call,
        'model_used': response.model_used,
        'tokens_used': response.tokens_used,
        'cost_usd': response.cost_usd,
    }


@app.get("/projects")
async def list_projects(_: bool = Depends(verify_api_key)):
    """List all configured projects and their readiness state."""
    projects_dir = Path('projects')
    if not projects_dir.exists():
        return {'projects': []}

    projects = []
    for p in sorted(projects_dir.iterdir()):
        if p.is_dir() and not p.name.startswith('_'):
            config_path = p / 'config.json'
            core_path = p / 'core.md'
            config = {}
            if config_path.exists():
                try:
                    config = json.loads(config_path.read_text())
                except json.JSONDecodeError:
                    pass
            projects.append({
                'id': p.name,
                'name': config.get('name', p.name),
                'has_config': config_path.exists(),
                'has_core_md': core_path.exists(),
                'ready': config_path.exists() and core_path.exists(),
            })
    return {'projects': projects}


@app.post("/projects/{project_id}/index")
async def index_project(
    project_id: str,
    body: IndexRequest = IndexRequest(),
    _: bool = Depends(verify_api_key),
):
    """Trigger codebase re-indexing for a project."""
    from core.context.indexer import CodeIndexer

    config_path = Path('projects') / project_id / 'config.json'
    if not config_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    config = json.loads(config_path.read_text())
    codebase_path = config.get('codebase_path')
    if not codebase_path:
        raise HTTPException(
            status_code=400,
            detail="config.json must include 'codebase_path'",
        )

    db_url = os.getenv('DATABASE_URL', '')

    async def _run_index():
        indexer = CodeIndexer(
            project_id=project_id,
            codebase_path=codebase_path,
            db_url=db_url,
        )
        await asyncio.to_thread(indexer.index_project, body.force)

    asyncio.create_task(_run_index())
    return {'status': 'indexing started', 'project_id': project_id}


@app.get("/projects/{project_id}/cost")
async def get_cost_summary(
    project_id: str,
    _: bool = Depends(verify_api_key),
):
    """API cost breakdown for a project."""
    from core.memory.store import MemoryStore
    data_dir = os.getenv('CLAW_DATA_DIR', './data')
    store = MemoryStore(project_id, data_dir)
    return {
        'project_id': project_id,
        'breakdown': store.get_cost_summary(),
    }


@app.get("/health")
async def health():
    """Health check — includes Ollama model status."""
    ollama_model = os.getenv('OLLAMA_MODEL', 'qwen2.5-coder:7b')
    ollama_ok = False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(
                f"{os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')}/api/tags"
            )
            data = r.json()
            models = [m['name'] for m in data.get('models', [])]
            ollama_ok = any(ollama_model in m for m in models)
    except Exception:
        pass

    return {
        'status': 'ok',
        'ollama_model': ollama_model,
        'ollama_available': ollama_ok,
        'anthropic_key_set': bool(os.getenv('ANTHROPIC_API_KEY')),
        'projects_loaded': list(_agents.keys()),
    }


# Register WhatsApp proxy route
from api.routes.whatsapp import router as whatsapp_router
app.include_router(whatsapp_router)
