import os
import json
import asyncio
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
from contextlib import asynccontextmanager

load_dotenv()

from core.agent import ClawAgent
from core.channels.envelope import MessageEnvelope, Channel
from api.middleware.auth import verify_api_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan handler to auto-load all projects on startup."""
    # Load all projects on startup
    projects_dir = Path('projects')
    if projects_dir.exists():
        for project_dir in sorted(projects_dir.iterdir()):
            if project_dir.is_dir() and not project_dir.name.startswith('_'):
                config_path = project_dir / 'config.json'
                if config_path.exists():
                    try:
                        config = json.loads(config_path.read_text())
                        _agents[project_dir.name] = ClawAgent(
                            project_id=project_dir.name,
                            config=config,
                        )
                        print(f"[CLAW] Auto-loaded project: {project_dir.name}")
                    except Exception as e:
                        print(f"[CLAW] Failed to load project {project_dir.name}: {e}")
    
    yield
    
    # Cleanup on shutdown
    for project_id, agent in _agents.items():
        # Close any resources if needed
        pass


app = FastAPI(
    title="CLAW — Coding and Language Agent Workbench",
    version="0.1.0",
    description="Sovereign AI coding agent with permanent per-project context",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Ensure all unhandled exceptions return JSON so the web chat can display them."""
    tb = traceback.format_exc()
    print(f"[CLAW ERROR] {request.url.path}\n{tb}")
    return JSONResponse(
        status_code=500,
        content={"error": f"{type(exc).__name__}: {exc}", "detail": tb[-1000:]},
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
    image_base64: Optional[str] = None
    image_media_type: str = 'image/png'
    read_only: bool = False   # WiggumOrchestrator sets this for assess/plan passes
    max_tool_rounds: Optional[int] = None   # Override agent MAX_TOOL_ROUNDS per request


class IndexRequest(BaseModel):
    force: bool = False


class WiggumRequest(BaseModel):
    goal: str
    success_criteria: list[str]
    project: str = 'claw'
    session_id: Optional[str] = None
    max_iterations: int = 20
    auto_approve_review: bool = True   # Auto-execute file edits; human reviews final diff
    batch_mode: bool = False           # Queue REVIEW tools rather than blocking


class CompleteRequest(BaseModel):
    file_path: str
    prefix: str
    suffix: str = ''
    project: str = 'claw'
    language: str = 'python'


class BatchApprovalRequest(BaseModel):
    approved: list[str] = []
    rejected: list[str] = []


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
        image_base64=request.image_base64,
        image_media_type=request.image_media_type,
        read_only=request.read_only,
        max_tool_rounds=request.max_tool_rounds,
    )

    response = await agent.process(envelope)

    return {
        'content': response.content,
        'session_id': response.session_id,
        'pending_tool_call': response.pending_tool_call,
        'model_used': response.model_used,
        'tokens_used': response.tokens_used,
        'cost_usd': response.cost_usd,
        'tool_calls': response.executed_tool_calls,
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


@app.get("/debug/tools/{project_id}")
async def debug_tools(project_id: str):
    """Show exactly which tools are registered and exposed for a project."""
    try:
        agent = get_agent(project_id)
        all_registered = list(agent.tools._tools.keys())
        exposed = [t['name'] for t in agent.tools.describe_for_model(agent.config)]
        permissions = agent.config.get('permissions', [])
        return {
            'project_id': project_id,
            'registered_tools': all_registered,
            'exposed_to_model': exposed,
            'permissions_in_config': permissions,
            'config_path': f'projects/{project_id}/config.json',
        }
    except Exception as e:
        return {'error': str(e)}


@app.get("/health")
async def health():
    """Health check — includes Ollama model status and VRAM advisory."""
    from core.models.ollama_client import OllamaClient, _vram_for, _VRAM_AVAILABLE_GB

    ollama_base = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
    ollama_preferred = os.getenv('OLLAMA_MODEL_PREFERRED', 'deepseek-coder-v2:16b')
    ollama_fallback = os.getenv('OLLAMA_MODEL', 'qwen2.5-coder:7b')

    ollama_reachable = False
    ollama_model_ready = False
    ollama_active_model = ollama_fallback
    ollama_vram_warning = False
    installed_models: list[str] = []

    try:
        import httpx
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f'{ollama_base}/api/tags')
            if r.status_code == 200:
                ollama_reachable = True
                installed_models = [m['name'] for m in r.json().get('models', [])]

                # Resolve which model would actually be used
                def is_pulled(name: str) -> bool:
                    return any(name in m for m in installed_models)

                ollama_active_model = (
                    ollama_preferred if is_pulled(ollama_preferred) else ollama_fallback
                )
                ollama_model_ready = is_pulled(ollama_active_model)
                ollama_vram_warning = _vram_for(ollama_active_model) > _VRAM_AVAILABLE_GB
    except Exception:
        pass

    return {
        'status': 'ok',
        'ollama_available': ollama_reachable,
        'ollama_active_model': ollama_active_model,
        'ollama_model_ready': ollama_model_ready,
        'ollama_vram_warning': ollama_vram_warning,
        'ollama_installed_models': installed_models,
        # Keep legacy key for backwards compat
        'ollama_model': ollama_active_model,
        'ollama_model_ready': ollama_model_ready,
        'anthropic_key_set': bool(os.getenv('ANTHROPIC_API_KEY')),
        'projects_loaded': list(_agents.keys()),
    }


# ─── WIGGUM endpoints ────────────────────────────────────────────────────────

# In-memory run registry (resets on restart; persist to SQLite in a later iteration)
_wiggum_runs: dict[str, dict] = {}


@app.post("/wiggum")
async def start_wiggum(
    req: WiggumRequest,
    _: bool = Depends(verify_api_key),
):
    """
    Start a WIGGUM outer loop in the background.

    The orchestrator drives CLAW toward `goal` by iterating:
      assess state → plan next task → execute task → repeat

    Returns immediately with a run_id. Poll GET /wiggum/{run_id} for status.

    Example:
        POST /wiggum
        {
          "goal": "Add a test suite to CLAW",
          "success_criteria": [
            "tests/ directory exists with at least 10 tests",
            "pytest passes with 0 failures",
            "tests cover /health, /chat, and tool dispatch"
          ],
          "project": "claw"
        }
    """
    from core.wiggum import WiggumOrchestrator

    run_id = str(uuid.uuid4())
    # Create a dedicated agent instance for this WIGGUM run.
    # Do NOT reuse the shared _agents dict — the thread gets its own loop
    # and fresh async clients to avoid cross-loop contamination.
    config_path = Path(f'projects/{req.project}/config.json')
    if not config_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{req.project}' not found")
    wiggum_agent = ClawAgent(
        project_id=req.project,
        config=json.loads(config_path.read_text()),
    )

    orchestrator = WiggumOrchestrator(
        goal=req.goal,
        success_criteria=req.success_criteria,
        project_id=req.project,
        session_id=req.session_id,
        max_iterations=req.max_iterations,
        agent=wiggum_agent,  # dedicated instance — thread-safe, own event loop
        auto_approve_review=req.auto_approve_review,
        batch_mode=req.batch_mode,
    )

    _wiggum_runs[run_id] = {
        'run_id': run_id,
        'status': 'running',
        'started_at': datetime.utcnow().isoformat(),
        'goal': req.goal,
        'project': req.project,
        'max_iterations': req.max_iterations,
    }

    def _run_in_thread():
        """
        Run the WIGGUM loop in a dedicated thread with its own event loop.
        This completely isolates the long-running orchestrator from the
        FastAPI event loop so health checks and status polls stay fast.
        """
        import threading
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(orchestrator.run())
            _wiggum_runs[run_id].update(result)
        except Exception as e:
            tb = traceback.format_exc()
            print(f'[wiggum] Run {run_id} failed: {e}\n{tb}')
            _wiggum_runs[run_id]['status'] = 'error'
            _wiggum_runs[run_id]['error'] = str(e)
        finally:
            loop.close()

    import threading
    t = threading.Thread(target=_run_in_thread, daemon=True, name=f'wiggum-{run_id[:8]}')
    t.start()

    return {
        'run_id': run_id,
        'status': 'started',
        'goal': req.goal,
        'project': req.project,
        'poll_url': f'/wiggum/{run_id}',
    }


@app.get("/wiggum/self-test")
async def wiggum_self_test(_: bool = Depends(verify_api_key)):
    """
    Lightweight self-assessment of CLAW's core components.
    Returns a JSON object with an `assessment` field summarising health.
    """
    checks = {}

    # 1. Tool registry
    try:
        from core.tools.registry import ToolRegistry, TOOL_SCHEMAS
        reg = ToolRegistry()
        tool_count = len(TOOL_SCHEMAS)
        checks['tool_registry'] = {'status': 'ok', 'tool_schemas': tool_count}
    except Exception as e:
        checks['tool_registry'] = {'status': 'error', 'detail': str(e)}

    # 2. Memory store (SQLite)
    try:
        from core.memory.store import MemoryStore
        data_dir = os.getenv('CLAW_DATA_DIR', './data')
        store = MemoryStore('self-test', data_dir)
        store.close()
        checks['memory_store'] = {'status': 'ok', 'backend': 'sqlite'}
    except Exception as e:
        checks['memory_store'] = {'status': 'error', 'detail': str(e)}

    # 3. Context engine
    try:
        from core.context.engine import ContextEngine
        config_path = Path('projects/claw/config.json')
        if config_path.exists():
            cfg = json.loads(config_path.read_text())
            engine = ContextEngine(project_id='claw', config=cfg)
            checks['context_engine'] = {'status': 'ok'}
        else:
            checks['context_engine'] = {'status': 'skip', 'detail': 'claw config not found'}
    except Exception as e:
        checks['context_engine'] = {'status': 'error', 'detail': str(e)}

    # 4. API reachability (Anthropic key present)
    anthropic_key = bool(os.getenv('ANTHROPIC_API_KEY'))
    openai_key = bool(os.getenv('OPENAI_API_KEY'))
    checks['api_keys'] = {
        'status': 'ok' if (anthropic_key or openai_key) else 'warn',
        'anthropic': anthropic_key,
        'openai': openai_key,
    }

    # 5. Projects loaded
    checks['projects'] = {
        'status': 'ok',
        'loaded_agents': list(_agents.keys()),
        'projects_dir_exists': Path('projects').exists(),
    }

    overall = 'ok' if all(
        c.get('status') in ('ok', 'skip', 'warn') for c in checks.values()
    ) else 'degraded'

    return {
        'assessment': overall,
        'checks': checks,
        'timestamp': datetime.utcnow().isoformat(),
    }


@app.get("/wiggum/{run_id}")
async def get_wiggum_run(
    run_id: str,
    _: bool = Depends(verify_api_key),
):
    """Get status and results for a specific WIGGUM run."""
    if run_id not in _wiggum_runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return _wiggum_runs[run_id]


@app.get("/wiggum")
async def list_wiggum_runs(
    _: bool = Depends(verify_api_key),
):
    """List all WIGGUM runs in reverse chronological order."""
    runs = sorted(
        _wiggum_runs.values(),
        key=lambda r: r.get('started_at', ''),
        reverse=True,
    )
    return {'runs': runs}


# ─── POST /complete — inline completions (Tier 1/2 only) ────────────────────

@app.post("/complete")
async def complete(
    req: CompleteRequest,
    _: bool = Depends(verify_api_key),
):
    """
    Inline code completion endpoint for the VS Code extension.
    Hard-capped at Tier 2 — never routes to Claude.
    Returns empty string if no cheap tier is available (graceful degradation).
    """
    import asyncio as _asyncio
    from core.models.router import ModelChoice, route as _route

    # Load project core.md as system context
    core_md_path = Path(f'projects/{req.project}/core.md')
    system = (
        core_md_path.read_text(encoding='utf-8')
        if core_md_path.exists()
        else f'You are a {req.language} coding assistant.'
    )

    # Build a concise completion prompt
    prefix_snippet = req.prefix[-500:] if len(req.prefix) > 500 else req.prefix
    suffix_snippet = req.suffix[:200] if req.suffix else ''
    prompt = (
        f'Complete the following {req.language} code. '
        f'Output ONLY the completion text — no explanation, no markdown fences.\n\n'
        f'File: {req.file_path}\n\n'
        f'<prefix>\n{prefix_snippet}\n</prefix>\n'
        + (f'<suffix>\n{suffix_snippet}\n</suffix>\n' if suffix_snippet else '')
    )

    # Determine routing — hard cap at Tier 2
    ollama_base = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
    ollama_available = False
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=2) as hx:
            r = await hx.get(f'{ollama_base}/api/tags')
            ollama_available = r.status_code == 200
    except Exception:
        pass

    deepseek_key = os.getenv('DEEPSEEK_API_KEY', '')

    try:
        if ollama_available:
            from core.models.ollama_client import OllamaClient
            model_name = os.getenv('OLLAMA_MODEL_PREFERRED', 'qwen2.5-coder:7b')
            ollama = OllamaClient(base_url=ollama_base, model=model_name)
            text, _, _ = await _asyncio.wait_for(
                ollama.chat(system=system, history=[], message=prompt),
                timeout=3.0,
            )
            return {'completion': text or '', 'tier': 1}

        if deepseek_key:
            from core.models.deepseek_client import DeepSeekClient
            ds = DeepSeekClient(api_key=deepseek_key)
            text, _, _ = await _asyncio.wait_for(
                ds.chat(system=system, history=[], message=prompt),
                timeout=3.0,
            )
            return {'completion': text or '', 'tier': 2}

    except (_asyncio.TimeoutError, Exception):
        pass

    # Neither Tier 1 nor Tier 2 available — return empty gracefully
    return {'completion': '', 'tier': 0}


# ─── GET /projects/{project}/core ────────────────────────────────────────────

@app.get("/projects/{project_id}/core")
async def get_project_core(
    project_id: str,
    _: bool = Depends(verify_api_key),
):
    """Return core.md content, last_updated timestamp, and session_count."""
    core_path = Path(f'projects/{project_id}/core.md')
    if not core_path.exists():
        raise HTTPException(status_code=404, detail=f"core.md not found for '{project_id}'")

    content = core_path.read_text(encoding='utf-8')
    stat = core_path.stat()
    last_updated = datetime.utcfromtimestamp(stat.st_mtime).isoformat()

    # Count session log entries
    session_count = content.count('## Session ')

    return {
        'content': content,
        'last_updated': last_updated,
        'session_count': session_count,
        'project_id': project_id,
    }


# ─── WIGGUM batch approvals ──────────────────────────────────────────────────

@app.get("/wiggum/{run_id}/approvals")
async def get_wiggum_approvals(
    run_id: str,
    _: bool = Depends(verify_api_key),
):
    """Return all pending approvals for a WIGGUM run with unified diffs."""
    if run_id not in _wiggum_runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    run = _wiggum_runs[run_id]
    return {
        'run_id': run_id,
        'pending_approvals': run.get('pending_approvals', []),
    }


@app.post("/wiggum/{run_id}/approvals/batch")
async def batch_approve_wiggum(
    run_id: str,
    req: BatchApprovalRequest,
    _: bool = Depends(verify_api_key),
):
    """
    Approve or reject a batch of queued REVIEW tool calls.
    Approved entries execute in order; rejected entries are logged as skipped.
    """
    if run_id not in _wiggum_runs:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    run = _wiggum_runs[run_id]
    approvals = run.get('pending_approvals', [])
    executed = []
    skipped = []

    for entry in approvals:
        entry_id = entry.get('id', '')
        if entry_id in req.approved:
            # Execute the queued tool call
            tool_name = entry.get('tool_name', '')
            tool_input = entry.get('tool_input', {})
            project_id = run.get('project', 'claw')
            try:
                agent = get_agent(project_id)
                tool_call = {'name': tool_name, 'input': tool_input}
                result = await agent._execute_tool(tool_call)
                entry['status'] = 'approved'
                entry['result'] = result
                executed.append(entry_id)
            except Exception as exc:
                entry['status'] = 'error'
                entry['error'] = str(exc)
        elif entry_id in req.rejected:
            entry['status'] = 'rejected'
            skipped.append(entry_id)

    return {
        'run_id': run_id,
        'executed': executed,
        'skipped': skipped,
        'total_approvals': len(approvals),
    }


# ─── WIGGUM list / get (existing, kept here) ─────────────────────────────────

# Register WhatsApp proxy route
from api.routes.whatsapp import router as whatsapp_router
app.include_router(whatsapp_router)
