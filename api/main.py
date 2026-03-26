import os
import json
import asyncio
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
from contextlib import asynccontextmanager

load_dotenv()

from core.agent import ClawAgent
from core.channels.envelope import MessageEnvelope, Channel
from api.middleware.auth import verify_api_key

# ── Absolute paths — resolved from this file, never from CWD ────────────────
_API_DIR      = Path(__file__).parent                    # D:\claw\api
_CLAW_ROOT    = _API_DIR.parent                          # D:\claw
_PROJECTS_ROOT = _CLAW_ROOT / 'projects'                 # D:\claw\projects

# Module-level test result cache — populated when run_tests tool runs
_test_cache: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan handler — auto-loads every project that has a config.json."""
    print(f'[CLAW startup] Scanning: {_PROJECTS_ROOT.absolute()}')
    if _PROJECTS_ROOT.exists():
        candidates = [
            d for d in sorted(_PROJECTS_ROOT.iterdir())
            if d.is_dir() and not d.name.startswith('_')
        ]
        print(f'[CLAW startup] Found: {[d.name for d in candidates]}')
        for project_dir in candidates:
            config_path = project_dir / 'config.json'
            if config_path.exists():
                try:
                    config = json.loads(config_path.read_text())
                    agent = ClawAgent(
                        project_id=project_dir.name,
                        config=config,
                    )
                    _agents[project_dir.name] = agent
                    # Seed subprojects declared in config.json
                    if 'subprojects' in config:
                        for sp in config['subprojects']:
                            try:
                                agent.memory.create_subproject(
                                    project_id=project_dir.name,
                                    name=sp['name'],
                                    display_name=sp['display_name'],
                                    description=sp.get('description', ''),
                                )
                            except Exception as sp_err:
                                print(
                                    f'[CLAW startup] subproject '
                                    f'{sp["name"]} failed: {sp_err}'
                                )
                    print(f'[CLAW startup] Loaded: {project_dir.name}')
                except Exception as e:
                    print(f'[CLAW startup] Failed {project_dir.name}: {e}')
    else:
        print(f'[CLAW startup] Projects dir not found: {_PROJECTS_ROOT.absolute()}')

    yield


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
        config_path = _PROJECTS_ROOT / project_id / 'config.json'
        if not config_path.exists():
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Project '{project_id}' not found. "
                    f"Create {config_path} "
                    f"and {_PROJECTS_ROOT / project_id / 'core.md'}"
                ),
            )
        config = json.loads(config_path.read_text())
        agent = ClawAgent(project_id=project_id, config=config)
        # Seed subprojects from config when creating agent on demand
        if 'subprojects' in config:
            for sp in config['subprojects']:
                try:
                    agent.memory.create_subproject(
                        project_id=project_id,
                        name=sp['name'],
                        display_name=sp['display_name'],
                        description=sp.get('description', ''),
                    )
                except Exception:
                    pass
        _agents[project_id] = agent
    return _agents[project_id]


# ─── Request/Response models ────────────────────────────────────────────────

class MentionedContext(BaseModel):
    type: str     # file | folder | symbol | session | core | web
    value: str    # path, symbol name, session_id, or search query
    display: str  # short label shown in the pill UI


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
    subproject_id: Optional[str] = None    # Scope session to a subproject
    mentions: list[MentionedContext] = []  # @ mentioned context items
    model_override: Optional[str] = None  # 'auto'|'local'|'deepseek'|'sonnet'|'opus'


class SubprojectCreateRequest(BaseModel):
    name: str
    display_name: str
    description: str = ''


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
        subproject_id=request.subproject_id,
        mentions=[m.model_dump() for m in request.mentions],
        model_override=request.model_override,
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
        'metadata': response.metadata,
    }


# ─── SSE streaming chat endpoint ─────────────────────────────────────────────

@app.get("/chat/stream")
async def chat_stream(
    project: str,
    session_id: str,
    message: str,
    mentions: Optional[str] = None,      # JSON-encoded list[MentionedContext]
    model_override: Optional[str] = None,
    _: bool = Depends(verify_api_key),
):
    """
    Server-Sent Events stream for real-time agent activity.

    Emits JSON events as the agent works:
      routing     — tier selected, model name
      tokens      — estimated context tokens
      tool_start  — SAFE tool about to run
      tool_end    — SAFE tool finished (duration_ms, result_chars)
      tool_queued — REVIEW tool encountered (approval needed via POST /chat)
      complete    — final response (response, cost_usd, model_used, metadata)
      error       — unhandled exception
      done        — stream end sentinel

    Falls back gracefully: if the agent raises, an 'error' event is emitted
    and the stream closes cleanly.
    """
    import json as _json

    parsed_mentions = []
    if mentions:
        try:
            parsed_mentions = _json.loads(mentions)
        except Exception:
            pass

    agent = get_agent(project)

    envelope = MessageEnvelope(
        content=message,
        channel=Channel.WEB,
        project_id=project,
        session_id=session_id,
        mentions=parsed_mentions,
        model_override=model_override,
    )

    async def event_generator():
        try:
            async for event in agent.process_streaming(envelope):
                yield f'data: {_json.dumps(event)}\n\n'
        except Exception as exc:
            yield f'data: {_json.dumps({"type": "error", "message": str(exc)})}\n\n'
        finally:
            yield 'data: {"type": "done"}\n\n'

    return StreamingResponse(
        event_generator(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        },
    )


# ─── Subproject endpoints ─────────────────────────────────────────────────────

@app.get("/projects/{project}/subprojects")
async def get_subprojects(
    project: str,
    _: bool = Depends(verify_api_key),
):
    """List all subprojects for a project."""
    agent = get_agent(project)
    return {'subprojects': agent.memory.get_subprojects(project)}


@app.post("/projects/{project}/subprojects")
async def create_subproject(
    project: str,
    body: SubprojectCreateRequest,
    _: bool = Depends(verify_api_key),
):
    """Create a new subproject. Idempotent — safe to call multiple times."""
    agent = get_agent(project)
    sp = agent.memory.create_subproject(
        project_id=project,
        name=body.name,
        display_name=body.display_name,
        description=body.description,
    )
    return sp


# ─── Session endpoints ───────────────────────────────────────────────────────

@app.get("/projects/{project}/sessions")
async def get_sessions(
    project: str,
    subproject: Optional[str] = None,
    _: bool = Depends(verify_api_key),
):
    """List sessions for a project, optionally scoped to a subproject."""
    agent = get_agent(project)
    sessions = agent.memory.get_session_list(
        project_id=project,
        subproject_id=subproject,
    )
    return {'sessions': sessions}


@app.get("/projects/{project}/sessions/{session_id}")
async def get_session(
    project: str,
    session_id: str,
    _: bool = Depends(verify_api_key),
):
    """Return a single session including all messages."""
    agent = get_agent(project)
    session = agent.memory.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return session


@app.get("/projects/{project}/subprojects/{subproject}/sessions")
async def get_subproject_sessions(
    project: str,
    subproject: str,
    _: bool = Depends(verify_api_key),
):
    """List sessions scoped to a specific subproject."""
    agent = get_agent(project)
    sessions = agent.memory.get_session_list(
        project_id=project,
        subproject_id=f'{project}:{subproject}',
    )
    return {'sessions': sessions}


@app.get("/projects/{project}/subprojects/{subproject}/sessions/{session_id}")
async def get_subproject_session(
    project: str,
    subproject: str,
    session_id: str,
    _: bool = Depends(verify_api_key),
):
    """Return a session belonging to a specific subproject."""
    agent = get_agent(project)
    session = agent.memory.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return session


@app.get("/projects/{project}/files")
async def get_project_files(
    project: str,
    q: Optional[str] = None,
    _: bool = Depends(verify_api_key),
):
    """
    Return all indexed file paths for a project.
    Optional ?q= filter applied as a case-insensitive substring match.
    Falls back to walking the codebase directory if pgvector is unavailable.
    """
    db_url = os.getenv('DATABASE_URL', '')
    files: list[str] = []

    if db_url:
        try:
            import psycopg2
            conn = psycopg2.connect(db_url)
            with conn.cursor() as cur:
                if q:
                    cur.execute(
                        "SELECT DISTINCT file_path FROM claw_code_chunks "
                        "WHERE project_id=%s AND file_path ILIKE %s ORDER BY file_path",
                        (project, f'%{q}%'),
                    )
                else:
                    cur.execute(
                        "SELECT DISTINCT file_path FROM claw_code_chunks "
                        "WHERE project_id=%s ORDER BY file_path",
                        (project,),
                    )
                files = [row[0] for row in cur.fetchall()]
            conn.close()
        except Exception:
            pass

    if not files:
        # Fallback: walk codebase_path from config
        config_path = _PROJECTS_ROOT / project / 'config.json'
        if config_path.exists():
            config = json.loads(config_path.read_text())
            codebase = config.get('codebase_path', '')
            if codebase:
                base = Path(codebase)
                SKIP = {'.git', '__pycache__', 'node_modules', '.venv', '.next', 'dist'}
                EXTS = {'.py', '.ts', '.tsx', '.js', '.jsx', '.json', '.md', '.yaml', '.yml'}
                for p in sorted(base.rglob('*')):
                    if p.is_file() and p.suffix in EXTS:
                        if not any(part in SKIP for part in p.parts):
                            rel = str(p.relative_to(base)).replace('\\', '/')
                            if q is None or q.lower() in rel.lower():
                                files.append(rel)
                files = files[:500]

    return {'files': files, 'count': len(files)}


@app.get("/projects/{project}/symbols")
async def get_project_symbols(
    project: str,
    q: str = '',
    _: bool = Depends(verify_api_key),
):
    """
    Search pgvector index for symbol names (function/class definitions).
    Requires ?q= search term. Returns symbol name + file it's in.
    """
    db_url = os.getenv('DATABASE_URL', '')
    symbols: list[dict] = []

    if not db_url:
        return {'symbols': symbols}

    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            pattern = f'%{q}%'
            cur.execute(
                """
                SELECT DISTINCT ON (chunk_name) chunk_name, file_path, chunk_type
                FROM claw_code_chunks
                WHERE project_id=%s
                  AND chunk_type IN ('function', 'class', 'method')
                  AND chunk_name ILIKE %s
                ORDER BY chunk_name, file_path
                LIMIT 30
                """,
                (project, pattern),
            )
            symbols = [
                {'name': row[0], 'file': row[1], 'type': row[2]}
                for row in cur.fetchall()
            ]
        conn.close()
    except Exception:
        pass

    return {'symbols': symbols}


@app.get("/projects")
async def list_projects(_: bool = Depends(verify_api_key)):
    """List all configured projects and their readiness state."""
    if not _PROJECTS_ROOT.exists():
        return {'projects': []}

    projects = []
    for p in sorted(_PROJECTS_ROOT.iterdir()):
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

    config_path = _PROJECTS_ROOT / project_id / 'config.json'
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
        'deepseek_key_set': bool(os.getenv('DEEPSEEK_API_KEY')),
        'openai_key_set': bool(os.getenv('OPENAI_API_KEY')),
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
        'projects_dir_exists': _PROJECTS_ROOT.exists(),
    }

    overall = 'ok' if all(
        c.get('status') in ('ok', 'skip', 'warn') for c in checks.values()
    ) else 'degraded'

    return {
        'assessment': overall,
        'checks': checks,
        'timestamp': datetime.utcnow().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Full-pipeline agent self-test
# Exercises the complete chat → tool loop → answer path using read-only tools.
# Safe to run at any time — no files are written, no git operations.
# ─────────────────────────────────────────────────────────────────────────────

_SELF_TEST_PROMPT = """\
You are running a structured self-test of the CLAW system. \
Use your tools to answer each of the following checks. \
Do NOT modify any files or run any commands that write to disk or network.

CHECKS TO COMPLETE:

1. FILES — Verify these files exist and are non-empty by reading the first \
few lines of each:
   - core/agent.py
   - core/memory/store.py
   - core/tools/registry.py
   - api/main.py

2. STRUCTURE — Search the codebase for the string "_run_tool_loop" and confirm \
it exists in core/agent.py.

3. SUBPROJECTS — Search for "create_subproject" and confirm it exists in \
core/memory/store.py.

4. SERVER — Check whether the API is reachable at http://localhost:8765/health.

5. GIT — Run git_status and report whether the working tree is clean.

After completing all checks, produce a structured report:

## CLAW Self-Test Report
### Files: PASS / FAIL (list any missing)
### Structure: PASS / FAIL
### Subprojects: PASS / FAIL
### Server: RUNNING / UNREACHABLE
### Git: CLEAN / DIRTY (list changed files if dirty)
### Overall: PASS / FAIL
"""


@app.get("/agent-self-test")
async def agent_self_test(
    project: str = 'claw',
    _: bool = Depends(verify_api_key),
):
    """
    Full-pipeline self-test: sends a read-only assessment prompt to the CLAW
    agent and returns the response. Exercises the complete tool loop.

    Safe to run at any time — read_only=True prevents any file writes.
    """
    try:
        agent = get_agent(project)
    except HTTPException:
        return {
            'passed': False,
            'error': f"Project '{project}' not loaded",
            'timestamp': datetime.utcnow().isoformat(),
        }

    session_id = f'self-test-{uuid.uuid4().hex[:8]}'

    envelope = MessageEnvelope(
        content=_SELF_TEST_PROMPT,
        channel=Channel.WEB,
        project_id=project,
        session_id=session_id,
        read_only=True,
        max_tool_rounds=10,
    )

    try:
        response = await agent.process(envelope)
    except Exception as exc:
        return {
            'passed': False,
            'error': str(exc),
            'timestamp': datetime.utcnow().isoformat(),
        }

    answer = response.content or ''
    passed = (
        len(answer.strip()) > 50          # got a real answer
        and 'FAIL' not in answer.upper()  # no explicit failures
        and bool(response.executed_tool_calls)  # tools were actually used
    )

    return {
        'passed': passed,
        'answer': answer,
        'tool_calls_made': [t['tool_name'] for t in response.executed_tool_calls],
        'tool_call_count': len(response.executed_tool_calls),
        'model_used': response.model_used,
        'cost_usd': response.cost_usd,
        'session_id': session_id,
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


# ─── GET /status/summary — single-request developer dashboard ───────────────

def _git_info() -> dict:
    """Return last commit hash, message, and timestamp via git log."""
    try:
        import subprocess as _sp
        result = _sp.run(
            ['git', 'log', '-1', '--format=%h|%s|%ci'],
            cwd=str(_CLAW_ROOT),
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and '|' in result.stdout:
            h, msg, ts = result.stdout.strip().split('|', 2)
            return {'commit_hash': h.strip(), 'commit_message': msg.strip(),
                    'commit_time': ts.strip()}
    except Exception:
        pass
    return {'commit_hash': None, 'commit_message': None, 'commit_time': None}


def _read_test_cache() -> dict:
    """Read last pytest result from cache file, or return nulls."""
    cache_path = _CLAW_ROOT / 'data' / 'test_cache.json'
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except Exception:
            pass
    return {'passed': None, 'failed': None, 'errors': None, 'last_run': None}


async def _ollama_status_fast() -> dict:
    """Lightweight Ollama status check (reuses health logic, 3s timeout)."""
    from core.models.ollama_client import _vram_for, _VRAM_AVAILABLE_GB
    ollama_base = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
    preferred = os.getenv('OLLAMA_MODEL_PREFERRED', 'qwen2.5-coder:7b')
    fallback  = os.getenv('OLLAMA_MODEL', 'qwen2.5-coder:7b')
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=3) as hx:
            r = await hx.get(f'{ollama_base}/api/tags')
            if r.status_code == 200:
                installed = [m['name'] for m in r.json().get('models', [])]
                def _pulled(n): return any(n in m for m in installed)
                active = preferred if _pulled(preferred) else fallback
                return {
                    'available': True,
                    'active_model': active,
                    'installed_models': installed,
                    'vram_warning': _vram_for(active) > _VRAM_AVAILABLE_GB,
                }
    except Exception:
        pass
    return {'available': False, 'active_model': None, 'installed_models': [], 'vram_warning': False}


async def _project_index_count(project_id: str) -> int | None:
    """Query pgvector for chunk count — returns None if DB unavailable."""
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        return None
    try:
        import asyncio as _asyncio
        import psycopg2 as _pg

        def _query():
            conn = _pg.connect(db_url)
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT COUNT(DISTINCT file_path) FROM claw_code_chunks WHERE project_id=%s',
                    (project_id,),
                )
                count = cur.fetchone()[0]
            conn.close()
            return count

        return await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, _query),
            timeout=3.0,
        )
    except Exception:
        return None


@app.get('/status/summary')
async def status_summary(_: bool = Depends(verify_api_key)):
    """
    Single-endpoint developer dashboard. Aggregates:
      git info, test results, API keys, Ollama, projects, WIGGUM runs.
    Frontend calls this once; no fan-out requests needed.
    """
    # Run independent queries concurrently
    git = _git_info()
    tests = _read_test_cache()
    ollama, *index_counts = await asyncio.gather(
        _ollama_status_fast(),
        *[_project_index_count(pid) for pid in sorted(_agents)],
    )

    # Projects info
    projects_info = []
    for i, (pid, agent) in enumerate(sorted(_agents.items())):
        codebase_path = agent.config.get('codebase_path', '')
        watcher = getattr(agent, '_watcher', None)
        last_reindex = None
        if watcher and hasattr(watcher, 'last_reindex_at') and watcher.last_reindex_at:
            last_reindex = watcher.last_reindex_at.isoformat()
        projects_info.append({
            'name': pid,
            'loaded': True,
            'codebase_exists': bool(codebase_path and Path(codebase_path).exists()),
            'files_indexed': index_counts[i],
            'watcher_active': bool(watcher and getattr(watcher, '_active', False)),
            'last_reindex': last_reindex,
        })

    # Add any projects with config.json but not yet loaded
    if _PROJECTS_ROOT.exists():
        loaded_ids = set(_agents)
        for d in sorted(_PROJECTS_ROOT.iterdir()):
            if d.is_dir() and not d.name.startswith('_') and d.name not in loaded_ids:
                if (d / 'config.json').exists():
                    projects_info.append({
                        'name': d.name,
                        'loaded': False,
                        'codebase_exists': None,
                        'files_indexed': None,
                        'watcher_active': False,
                        'last_reindex': None,
                    })

    # WIGGUM runs — most recent 10
    recent_runs = sorted(
        _wiggum_runs.values(),
        key=lambda r: r.get('started_at', ''),
        reverse=True,
    )[:10]
    wiggum_summary = [
        {
            'run_id': r.get('run_id', ''),
            'goal': r.get('goal', '')[:80],
            'status': r.get('status', 'unknown'),
            'iterations': r.get('iterations', 0),
            'started_at': r.get('started_at', ''),
        }
        for r in recent_runs
    ]

    # Count pending approvals across all runs
    pending = sum(
        1
        for r in _wiggum_runs.values()
        for a in r.get('pending_approvals', [])
        if a.get('status', 'pending') == 'pending'
    )

    return {
        'api_status': 'ok',
        **git,
        'test_results': tests,
        'api_keys': {
            'anthropic': bool(os.getenv('ANTHROPIC_API_KEY')),
            'deepseek': bool(os.getenv('DEEPSEEK_API_KEY')),
            'openai': bool(os.getenv('OPENAI_API_KEY')),
        },
        'ollama': ollama,
        'projects': projects_info,
        'wiggum_runs': wiggum_summary,
        'pending_approvals': pending,
        'generated_at': datetime.utcnow().isoformat(),
    }


# ─── WIGGUM list / get (existing, kept here) ─────────────────────────────────

# Register WhatsApp proxy route
from api.routes.whatsapp import router as whatsapp_router
app.include_router(whatsapp_router)
