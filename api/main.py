import os
import json
import asyncio
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from typing import Optional
from dotenv import load_dotenv
from contextlib import asynccontextmanager

load_dotenv()

from core.agent import DeekAgent, GenerationStopped, GenerationTimedOut
from core.channels.envelope import MessageEnvelope, Channel
from core.skills.skill_loader import SkillLoader
from core.skills.skill_classifier import SkillClassifier
from api.middleware.auth import verify_api_key

# ── Absolute paths — resolved from this file, never from CWD ────────────────
_API_DIR      = Path(__file__).parent                    # D:\deek\api
_CLAW_ROOT    = _API_DIR.parent                          # D:\deek
_PROJECTS_ROOT = _CLAW_ROOT / 'projects'                 # D:\deek\projects

# Module-level test result cache — populated when run_tests tool runs
_test_cache: dict = {}
_status_summary_cache: dict[str, object] = {
    'data': None,
    'expires_at': 0.0,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan handler — auto-loads every project that has a config.json."""
    print(f'[DEEK startup] Scanning: {_PROJECTS_ROOT.absolute()}')
    active_watchers = []
    if _PROJECTS_ROOT.exists():
        candidates = [
            d for d in sorted(_PROJECTS_ROOT.iterdir())
            if d.is_dir() and not d.name.startswith('_')
        ]
        print(f'[DEEK startup] Found: {[d.name for d in candidates]}')
        for project_dir in candidates:
            config_path = project_dir / 'config.json'
            if config_path.exists():
                try:
                    config = json.loads(config_path.read_text())
                    agent = DeekAgent(
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
                                    f'[DEEK startup] subproject '
                                    f'{sp["name"]} failed: {sp_err}'
                                )
                    codebase_path = config.get('codebase_path')
                    db_url = os.getenv('DATABASE_URL', '')
                    watcher_enabled = (os.getenv('DEEK_ENABLE_WATCHER') or os.getenv('CLAW_ENABLE_WATCHER', '')).lower() in {
                        '1', 'true', 'yes', 'on',
                    }
                    if watcher_enabled and codebase_path and db_url:
                        try:
                            from core.context.indexer import CodeIndexer
                            from core.context.watcher import FileWatcher

                            watcher = FileWatcher(
                                path=codebase_path,
                                indexer=CodeIndexer(
                                    project_id=project_dir.name,
                                    codebase_path=codebase_path,
                                    db_url=db_url,
                                ),
                                loop=asyncio.get_running_loop(),
                                context_engine=agent.context,
                                project_id=project_dir.name,
                            )
                            watcher.start()
                            agent._watcher = watcher
                            active_watchers.append(watcher)
                        except Exception as watcher_err:
                            print(
                                f'[DEEK startup] watcher disabled for '
                                f'{project_dir.name}: {watcher_err}'
                            )
                    print(f'[DEEK startup] Loaded: {project_dir.name}')
                except Exception as e:
                    print(f'[DEEK startup] Failed {project_dir.name}: {e}')
    else:
        print(f'[DEEK startup] Projects dir not found: {_PROJECTS_ROOT.absolute()}')

    # ── Skill system init ────────────────────────────────────────────────
    try:
        skill_loader = SkillLoader(projects_root=str(_PROJECTS_ROOT))
        all_skills = skill_loader.load_all_skills()
        app.state.skill_loader = skill_loader
        app.state.skill_classifier_ready = False

        # Try to initialise classifier with Ollama embedder
        try:
            from core.models.ollama_client import OllamaClient
            embedder = OllamaClient(
                base_url=os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434'),
                model='nomic-embed-text',
            )
            classifier = SkillClassifier(skill_loader, embedder)
            await classifier.initialise()
            app.state.skill_classifier = classifier
            app.state.skill_classifier_ready = True
            print(f'[DEEK startup] Skill classifier ready — {len(all_skills)} skills')
        except Exception as cls_err:
            print(f'[DEEK startup] Skill classifier unavailable (exact-match only): {cls_err}')
            app.state.skill_classifier = None

        # Upgrade agents to use the shared loader + classifier
        from core.skills.manager import SkillManager
        for pid, agent in _agents.items():
            agent.skills = SkillManager(
                skill_loader=skill_loader,
                skill_classifier=app.state.skill_classifier,
                project_id=pid,
            )
    except Exception as skill_err:
        print(f'[DEEK startup] Skill system failed: {skill_err}')
        app.state.skill_loader = None
        app.state.skill_classifier = None
        app.state.skill_classifier_ready = False

    # ── Amazon Intelligence schema ─────────────────────────────────────
    try:
        from core.amazon_intel.db import (
            ensure_schema as ami_ensure_schema,
            migrate_ami_schema as ami_migrate_schema,
        )
        ami_ensure_schema()
        # Apply column-level migrations after the base schema. Safe to re-run
        # (each ALTER is try/except-swallowed in migrate_ami_schema).
        ami_migrate_schema()
        print('[DEEK startup] Amazon Intel schema ready')
    except Exception as ami_err:
        print(f'[DEEK startup] Amazon Intel schema failed: {ami_err}')

    # ── Etsy Intelligence schema ───────────────────────────────────────
    try:
        from core.etsy_intel.db import ensure_schema as etsy_ensure_schema
        etsy_ensure_schema()
        print('[DEEK startup] Etsy Intel schema ready')
    except Exception as etsy_err:
        print(f'[DEEK startup] Etsy Intel schema failed: {etsy_err}')

    # ── Email Ingestion schema ─────────────────────────────────────────
    try:
        from core.email_ingest.db import ensure_schema as email_ensure_schema
        email_ensure_schema()
        print('[DEEK startup] Email Ingest schema ready')
    except Exception as email_err:
        print(f'[DEEK startup] Email Ingest schema failed: {email_err}')

    # ── Wiki Generation schema ─────────────────────────────────────────
    try:
        from core.wiki_gen.db import ensure_schema as wiki_gen_ensure_schema
        wiki_gen_ensure_schema()
        print('[DEEK startup] Wiki Generation schema ready')
    except Exception as wiki_gen_err:
        print(f'[DEEK startup] Wiki Generation schema failed: {wiki_gen_err}')

    # ── Counterfactual Intelligence schema ─────────────────────────────
    try:
        from core.intel.db import ensure_schema as intel_ensure_schema
        intel_ensure_schema()
        print('[DEEK startup] Counterfactual Intel schema ready')
    except Exception as intel_err:
        print(f'[DEEK startup] Counterfactual Intel schema failed: {intel_err}')

    # ── Auto-index empty projects ───────────────────────────────────────
    skip_auto_index = (os.getenv('DEEK_SKIP_AUTO_INDEX') or os.getenv('CAIRN_SKIP_AUTO_INDEX', '')).lower() in {
        '1', 'true', 'yes',
    }
    if not skip_auto_index:
        db_url = os.getenv('DATABASE_URL', '')
        if db_url:
            for pid, agent in _agents.items():
                await _auto_index_if_empty(pid, agent, db_url)

    # ── Scheduled reindex background task ───────────────────────────────
    reindex_hours = int(os.getenv('DEEK_REINDEX_INTERVAL_HOURS') or os.getenv('CAIRN_REINDEX_INTERVAL_HOURS', '24'))
    _reindex_task = None
    if reindex_hours > 0 and _agents:
        _reindex_task = asyncio.create_task(
            _scheduled_reindex_loop(_agents, interval_hours=reindex_hours)
        )

    # ── Module snapshot federation poll loop ───────────────────────────
    _snapshot_task = None
    try:
        from api.routes.deek_federation import snapshot_poll_loop
        _snapshot_task = asyncio.create_task(snapshot_poll_loop())
        print('[DEEK startup] Module snapshot poll loop started')
    except Exception as fed_err:
        print(f'[DEEK startup] Snapshot poll loop disabled: {fed_err}')

    # ── Seed default staff profiles + start briefing scheduler ──────────
    _briefing_task = None
    try:
        from api.routes.ambient import seed_default_staff_profiles
        seed_default_staff_profiles()
        print('[DEEK startup] Default staff profiles seeded')
    except Exception as seed_err:
        print(f'[DEEK startup] Staff profile seed skipped: {seed_err}')
    try:
        _briefing_task = asyncio.create_task(_briefing_scheduler_loop())
        print('[DEEK startup] Briefing scheduler loop started')
    except Exception as sched_err:
        print(f'[DEEK startup] Briefing scheduler disabled: {sched_err}')

    # ── Identity layer: probe modules + emit startup log block ──────────
    try:
        from core.identity import assembler as _identity_assembler
        from core.identity import probe as _identity_probe
        await _identity_probe.start(run_initial=True)

        _modules = _identity_assembler.get_modules()
        _reachable = _identity_probe.get_reachable_modules()
        _errors = _identity_probe.get_errors()
        _names = [m.name for m in _modules]
        _unreach_lines = []
        for m in _modules:
            if m.name not in _reachable:
                _unreach_lines.append(
                    f"{m.name} ({_errors.get(m.name, 'unknown')} at {m.health_url})"
                )
        print('================ DEEK IDENTITY LOADED ================')
        print('identity: DEEK_IDENTITY.md + DEEK_MODULES.yaml')
        print(f'hash:     {_identity_assembler.get_identity_hash()}')
        print(f'modules declared: {len(_modules)} — {", ".join(_names)}')
        print(f'modules reachable: {len(_reachable)}/{len(_modules)}')
        if _unreach_lines:
            print(f'unreachable: {"; ".join(_unreach_lines)}')
        else:
            print('unreachable: (none)')
        print('=====================================================')
    except Exception as id_err:
        # Identity-broken Deek is worse than offline — fail loudly.
        print(f'[DEEK startup] FATAL: identity layer failed to load: {id_err}')
        raise

    yield

    if _reindex_task and not _reindex_task.done():
        _reindex_task.cancel()
    if _snapshot_task and not _snapshot_task.done():
        _snapshot_task.cancel()
    if _briefing_task and not _briefing_task.done():
        _briefing_task.cancel()
    try:
        from core.identity import probe as _identity_probe
        await _identity_probe.stop()
    except Exception:
        pass

    for watcher in active_watchers:
        try:
            watcher.stop()
        except Exception:
            pass


def _resolve_codebase_path(project_id: str, config: dict) -> str | None:
    """Resolve the codebase path for indexing, handling git repos.

    If ``codebase_path`` exists on the local filesystem, use it directly.
    If it doesn't exist but ``git_repo`` is configured, clone or pull the
    repo into ``/app/data/repos/{project_id}/`` and return that path.
    Returns None if neither approach yields a usable path.
    """
    import subprocess

    codebase_path = config.get('codebase_path', '')
    if codebase_path and Path(codebase_path).exists():
        return codebase_path

    git_repo = config.get('git_repo', '')
    if not git_repo:
        return None

    data_dir = os.getenv('DEEK_DATA_DIR') or os.getenv('CLAW_DATA_DIR', './data')
    checkout_dir = Path(data_dir) / 'repos' / project_id
    git_branch = config.get('git_branch', 'master')

    # Inject PAT for private repos if available
    pat = os.getenv('GITHUB_PAT', '').strip()
    clone_url = git_repo
    if pat and 'github.com' in clone_url and '@' not in clone_url:
        clone_url = clone_url.replace('https://github.com', f'https://{pat}@github.com')

    try:
        if (checkout_dir / '.git').exists():
            # Pull latest changes
            result = subprocess.run(
                ['git', 'pull', '--ff-only'],
                cwd=str(checkout_dir),
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                print(f'[Deek] git pull failed for {project_id}: {result.stderr.strip()}')
            else:
                print(f'[Deek] git pull complete: {project_id}')
        else:
            # Clone fresh
            checkout_dir.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ['git', 'clone', '--depth=1', '-b', git_branch,
                 clone_url, str(checkout_dir)],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                print(f'[Deek] git clone failed for {project_id}: {result.stderr.strip()}')
                return None
            print(f'[Deek] git clone complete: {project_id}')
        return str(checkout_dir) if (checkout_dir / '.git').exists() else None
    except Exception as e:
        print(f'[Deek] git checkout failed for {project_id}: {e}')
        # Fall back to existing checkout if available
        if (checkout_dir / '.git').exists():
            return str(checkout_dir)
        return None


async def _auto_index_if_empty(
    project_id: str,
    agent: DeekAgent,
    db_url: str,
) -> None:
    """
    Check chunk count for this project.
    If zero: run full index automatically (cloning via git if needed).
    If > 0: skip — scheduled reindex handles updates.
    Never blocks startup — logs and continues on error.
    """
    try:
        count = await _project_index_count(project_id)
        if count is None:
            print(f'[Deek] Project {project_id} — DB unavailable, skipping auto-index')
            return

        if count > 0:
            print(f'[Deek] Project {project_id} already indexed — {count} files')
            return

        codebase_path = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _resolve_codebase_path(project_id, agent.config),
        )
        if not codebase_path:
            print(f'[Deek] Project {project_id} — no codebase_path or git_repo, skipping auto-index')
            return

        print(f'[Deek] Project {project_id} has no indexed content — auto-indexing now...')

        from core.context.indexer import CodeIndexer, IndexerError

        indexer = CodeIndexer(
            project_id=project_id,
            codebase_path=codebase_path,
            db_url=db_url,
        )

        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: indexer.index_project(force_reindex=True),
        )

        print(
            f'[Deek] Auto-index complete: {project_id} — '
            f'{result["chunks_created"]} chunks from {result["indexed"]} files'
        )
    except Exception as e:
        print(f'[Deek] Auto-index failed for {project_id}: {e}')


async def _briefing_scheduler_loop() -> None:
    """Daily briefing generator loop.

    Runs continuously, wakes up once a minute, and fires the briefing
    generator at 06:45 UTC on weekdays. Safe to run every minute because
    generate_daily_briefings() inserts new rows; running it twice would
    create duplicate briefings — so we also track last-fire date and
    skip if we've already fired today.
    """
    from datetime import datetime as _dt, time as _time, timezone as _tz
    from api.routes.ambient import generate_daily_briefings

    last_fired: str = ''  # YYYY-MM-DD of last fire
    fire_at = _time(hour=6, minute=45)

    while True:
        try:
            now = _dt.now(tz=_tz.utc)
            today = now.strftime('%Y-%m-%d')
            # Fire if we've crossed the fire time today and haven't fired yet
            if last_fired != today and now.time() >= fire_at:
                try:
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, generate_daily_briefings,
                    )
                    print(
                        f"[Deek] Daily briefings generated: "
                        f"{len(result.get('generated', []))} emitted, "
                        f"{len(result.get('skipped', []))} skipped"
                    )
                except Exception as exc:
                    print(f'[Deek] Daily briefing generation failed: {exc}')
                last_fired = today
        except Exception as outer:
            print(f'[Deek] Briefing scheduler loop error: {outer}')
        await asyncio.sleep(60)


async def _scheduled_reindex_loop(
    agents: dict[str, DeekAgent],
    interval_hours: int = 6,
) -> None:
    """
    Background task started in lifespan handler.
    Runs full reindex for all projects every interval_hours.
    For projects with git_repo configured, pulls latest code first.
    """
    while True:
        await asyncio.sleep(interval_hours * 3600)
        for project_id, agent in agents.items():
            try:
                db_url = os.getenv('DATABASE_URL', '')
                if not db_url:
                    continue

                codebase_path = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: _resolve_codebase_path(project_id, agent.config),
                )
                if not codebase_path:
                    continue

                print(f'[Deek] Scheduled reindex: {project_id}')

                from core.context.indexer import CodeIndexer

                indexer = CodeIndexer(
                    project_id=project_id,
                    codebase_path=codebase_path,
                    db_url=db_url,
                )

                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda pid=project_id, idx=indexer: idx.index_project(force_reindex=False),
                )

                print(
                    f'[Deek] Scheduled reindex complete: {project_id} — '
                    f'{result["chunks_created"]} chunks'
                )
            except Exception as e:
                print(f'[Deek] Scheduled reindex failed {project_id}: {e}')

        # ── Wiki freshness check ────────────────────────────────────
        await _check_wiki_freshness()


async def _check_wiki_freshness() -> None:
    """Re-embed any wiki articles whose file mtime is newer than their DB entry."""
    wiki_root = _CLAW_ROOT / 'wiki'
    if not wiki_root.exists():
        return

    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        return

    try:
        import psycopg2
        from pgvector.psycopg2 import register_vector
        conn = psycopg2.connect(db_url, connect_timeout=5)
        register_vector(conn)
    except Exception:
        return

    from core.wiki.embeddings import get_embed_fn
    embed_fn = get_embed_fn()
    if not embed_fn:
        conn.close()
        return

    stale = 0
    for md_file in wiki_root.rglob('*.md'):
        if md_file.name == 'index.md':
            continue

        rel_path = str(md_file.relative_to(_CLAW_ROOT)).replace('\\', '/')
        file_mtime = md_file.stat().st_mtime

        with conn.cursor() as cur:
            cur.execute(
                """SELECT indexed_at FROM claw_code_chunks
                   WHERE project_id = 'deek' AND file_path = %s AND chunk_type = 'wiki'""",
                (rel_path,),
            )
            row = cur.fetchone()

        # Needs embedding if: not in DB, or file is newer than indexed_at
        needs_update = False
        if not row:
            needs_update = True
        elif row[0]:
            indexed_ts = row[0].timestamp()
            if file_mtime > indexed_ts:
                needs_update = True

        if not needs_update:
            continue

        content = md_file.read_text(encoding='utf-8')
        if not content.strip():
            continue

        try:
            embedding = embed_fn(content[:6000])
        except Exception:
            continue

        import hashlib
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        chunk_name = md_file.stem
        for line in content.split('\n'):
            if line.startswith('# '):
                chunk_name = line[2:].strip()
                break

        with conn.cursor() as cur:
            cur.execute(
                """DELETE FROM claw_code_chunks
                   WHERE project_id = 'deek' AND file_path = %s AND chunk_type = 'wiki'""",
                (rel_path,),
            )
            cur.execute(
                """INSERT INTO claw_code_chunks
                   (project_id, file_path, chunk_content, chunk_type, chunk_name,
                    content_hash, embedding, indexed_at)
                   VALUES (%s, %s, %s, 'wiki', %s, %s, %s::vector, NOW())""",
                ('deek', rel_path, content, chunk_name, content_hash, embedding),
            )
        conn.commit()
        stale += 1

    conn.close()
    if stale > 0:
        print(f'[Deek] Wiki freshness: re-embedded {stale} stale articles')


# In-memory index run registry — tracks manual and auto index operations
_index_runs: dict[str, dict] = {}


app = FastAPI(
    title="Deek — Sovereign AI Agent",
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
    print(f"[DEEK ERROR] {request.url.path}\n{tb}")
    return JSONResponse(
        status_code=500,
        content={"error": f"{type(exc).__name__}: {exc}", "detail": tb[-1000:]},
    )

# Agent registry — one instance per project, loaded on first request
_agents: dict[str, DeekAgent] = {}


def _default_retrieval_mode() -> str:
    if _agents:
        modes = {agent.context.retrieval_mode for agent in _agents.values()}
        return next(iter(modes)) if len(modes) == 1 else 'mixed'

    if os.getenv('DATABASE_URL', ''):
        try:
            import rank_bm25  # noqa: F401
            return 'hybrid'
        except Exception:
            return 'cosine'
    return 'keyword'


def _bm25_available() -> bool:
    try:
        from core.memory.retriever import BM25Okapi
        return BM25Okapi is not None
    except Exception:
        return False


def get_agent(project_id: str) -> DeekAgent:
    # Normalise empty / missing project IDs to the catch-all general agent
    if not project_id or project_id == 'general':
        project_id = 'general'

    if project_id not in _agents:
        config_path = _PROJECTS_ROOT / project_id / 'config.json'
        if not config_path.exists():
            if project_id == 'general':
                # Standalone chat mode — no codebase, but full tool access
                # (memory, wiki, CRM, analyzer, amazon intel, etc.)
                config = {
                    'name': 'General',
                    'description': 'Standalone Deek agent — no project-specific codebase',
                    'codebase': {},
                }
                agent = DeekAgent(project_id='general', config=config)
                _agents['general'] = agent
                return agent
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Project '{project_id}' not found. "
                    f"Create {config_path} "
                    f"and {_PROJECTS_ROOT / project_id / 'core.md'}"
                ),
            )
        config = json.loads(config_path.read_text())
        agent = DeekAgent(project_id=project_id, config=config)
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
    model_config = ConfigDict(protected_namespaces=())

    content: str
    project_id: str = 'general'
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
    skill_ids: list[str] = []              # Manually activated skills
    model_override: Optional[str] = None  # 'auto'|'local'|'deepseek'|'sonnet'|'opus'


class StopRequest(BaseModel):
    project_id: str = 'general'
    session_id: str


class SubprojectCreateRequest(BaseModel):
    name: str
    display_name: str
    description: str = ''


class IndexRequest(BaseModel):
    force: bool = False


class WiggumRequest(BaseModel):
    goal: str
    success_criteria: list[str]
    project: str = 'deek'
    session_id: Optional[str] = None
    max_iterations: int = 20
    auto_approve_review: bool = True   # Auto-execute file edits; human reviews final diff
    batch_mode: bool = False           # Queue REVIEW tools rather than blocking


class CompleteRequest(BaseModel):
    file_path: str
    prefix: str
    suffix: str = ''
    project: str = 'deek'
    language: str = 'python'


class MemoryWriteRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    project: str
    query: str
    decision: str
    rejected: str = ''
    outcome: str = 'committed'  # committed | partial | failed | deferred
    model: str = ''
    files_changed: list[str] = []
    session_id: Optional[str] = None
    delegation_decision: str = ''  # Rule 1 (CLAUDE.md STEP 2b): one-sentence justification of who did the work and why.


class CostLogEntry(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_gbp: float = 0.0


class MemoryUpdateRequest(BaseModel):
    description: Optional[str] = None
    reasoning: Optional[str] = None
    query: Optional[str] = None
    rejected: Optional[str] = None
    decision_type: Optional[str] = None
    files_affected: Optional[list[str]] = None


class CostLogRequest(BaseModel):
    session_id: str
    prompt_summary: str
    project: str
    costs: list[CostLogEntry]
    total_cost_gbp: float


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
        skill_ids=request.skill_ids,
        model_override=request.model_override,
    )

    try:
        response = await agent.process(envelope)
    except GenerationStopped:
        agent.clear_stop(request.session_id)
        return {
            'content': '_Generation stopped._',
            'session_id': request.session_id,
            'pending_tool_call': None,
            'model_used': '',
            'tokens_used': 0,
            'cost_usd': 0.0,
            'tool_calls': [],
            'metadata': {'stopped': True},
        }
    except GenerationTimedOut as exc:
        agent.clear_stop(request.session_id)
        return {
            'content': str(exc),
            'session_id': request.session_id,
            'pending_tool_call': None,
            'model_used': '',
            'tokens_used': 0,
            'cost_usd': 0.0,
            'tool_calls': [],
            'metadata': {'timed_out': True},
        }

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


@app.post("/chat/stop")
async def chat_stop(
    request: StopRequest,
    _: bool = Depends(verify_api_key),
):
    agent = get_agent(request.project_id)
    agent.request_stop(request.session_id)
    return {'status': 'stopping', 'session_id': request.session_id}


# ─── SSE streaming chat endpoint ─────────────────────────────────────────────

@app.get("/chat/stream")
async def chat_stream(
    project: str = 'general',
    session_id: str = '',
    message: str = '',
    mentions: Optional[str] = None,      # JSON-encoded list[MentionedContext]
    skill_ids: Optional[str] = None,     # JSON-encoded list[str]
    model_override: Optional[str] = None,
    subproject_id: Optional[str] = None,
    image_b64: Optional[str] = None,
    image_media_type: str = 'image/png',
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
    parsed_skill_ids: list[str] = []
    if mentions:
        try:
            parsed_mentions = _json.loads(mentions)
        except Exception:
            pass
    if skill_ids:
        try:
            parsed_skill_ids = [str(item) for item in _json.loads(skill_ids)]
        except Exception:
            parsed_skill_ids = [
                item.strip() for item in skill_ids.split(',') if item.strip()
            ]

    agent = get_agent(project)

    envelope = MessageEnvelope(
        content=message,
        channel=Channel.WEB,
        project_id=project,
        session_id=session_id,
        subproject_id=subproject_id,
        mentions=parsed_mentions,
        skill_ids=parsed_skill_ids,
        model_override=model_override,
        image_base64=image_b64,
        image_media_type=image_media_type,
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


class ChatStreamRequest(BaseModel):
    """POST body for /chat/stream — avoids URL length limits."""
    model_config = ConfigDict(protected_namespaces=())

    project: str
    session_id: str
    message: str
    channel: str = 'web'
    mentions: Optional[str] = None
    skill_ids: Optional[str] = None
    model_override: Optional[str] = None
    subproject_id: Optional[str] = None


@app.post("/chat/stream")
async def chat_stream_post(
    body: ChatStreamRequest,
    _: bool = Depends(verify_api_key),
):
    """POST variant of SSE stream — accepts message in body, no URL length limit."""
    import json as _json

    parsed_mentions = []
    parsed_skill_ids: list[str] = []
    if body.mentions:
        try:
            parsed_mentions = _json.loads(body.mentions)
        except Exception:
            pass
    if body.skill_ids:
        try:
            parsed_skill_ids = [str(item) for item in _json.loads(body.skill_ids)]
        except Exception:
            parsed_skill_ids = [
                item.strip() for item in body.skill_ids.split(',') if item.strip()
            ]

    agent = get_agent(body.project)

    envelope = MessageEnvelope(
        content=body.message,
        channel=Channel(body.channel),
        project_id=body.project,
        session_id=body.session_id,
        subproject_id=body.subproject_id,
        mentions=parsed_mentions,
        skill_ids=parsed_skill_ids,
        model_override=body.model_override,
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


@app.get("/projects/{project}/skills")
async def get_skills(
    project: str,
    _: bool = Depends(verify_api_key),
):
    """List all disk-backed skills for a project."""
    agent = get_agent(project)
    return {
        'skills': [
            {
                'skill_id': skill.skill_id,
                'display_name': skill.display_name,
                'description': skill.description,
                'subproject_id': skill.subproject_id,
                'has_decisions': skill.decisions_path.exists(),
            }
            for skill in agent.skills.list_skills()
        ]
    }


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
    session = agent.memory.get_session(session_id, project_id=project)
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
    session = agent.memory.get_session(
        session_id,
        project_id=project,
        subproject_id=f'{project}:{subproject}',
    )
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return session


class SessionUpdateRequest(BaseModel):
    title: Optional[str] = None
    subproject_id: Optional[str] = None


@app.patch("/projects/{project}/sessions/{session_id}")
async def update_session(
    project: str,
    session_id: str,
    body: SessionUpdateRequest,
    _: bool = Depends(verify_api_key),
):
    """Rename a session or move it to a different subproject."""
    agent = get_agent(project)
    kwargs = {}
    if body.title is not None:
        kwargs['title'] = body.title
    if body.subproject_id is not None:
        kwargs['subproject_id'] = body.subproject_id
    if not kwargs:
        raise HTTPException(status_code=400, detail="No fields to update")
    ok = agent.memory.update_session(session_id, **kwargs)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return {'session_id': session_id, 'updated': True}


@app.post("/projects/{project}/sessions/{session_id}/archive")
async def archive_session(
    project: str,
    session_id: str,
    _: bool = Depends(verify_api_key),
):
    """Archive a session (move to archived_sessions table)."""
    agent = get_agent(project)
    try:
        agent.memory.archive_session(session_id, summary='Manually archived')
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {'session_id': session_id, 'archived': True}


@app.delete("/projects/{project}/sessions/{session_id}")
async def delete_session(
    project: str,
    session_id: str,
    _: bool = Depends(verify_api_key),
):
    """Permanently delete a session and all its messages."""
    agent = get_agent(project)
    ok = agent.memory.delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return {'session_id': session_id, 'deleted': True}


@app.post("/projects/{project}/sessions/retitle")
async def retitle_sessions(
    project: str,
    _: bool = Depends(verify_api_key),
):
    """One-off migration: recompute titles for sessions polluted with
    context-injection blocks (e.g. '[PERSONALITY] You are the NB…').

    Idempotent — skips any session whose stored title is already clean.
    Returns {'scanned': N, 'updated': M}.
    """
    agent = get_agent(project)
    result = agent.memory.retitle_sessions(project_id=project)
    return {'project': project, **result}


@app.get("/projects/{project}/files")
def get_project_files(
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
            conn = psycopg2.connect(db_url, connect_timeout=1)
            try:
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
            finally:
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
                SKIP = {'.git', '__pycache__', 'node_modules', '.venv', '.next', 'dist', 'build', 'coverage'}
                EXTS = {'.py', '.ts', '.tsx', '.js', '.jsx', '.json', '.md', '.yaml', '.yml'}
                MAX_WALK = 2000  # Safety cap to prevent hangs on huge codebases
                stack = [base]
                dirs_visited = 0
                while stack and dirs_visited < MAX_WALK:
                    current = stack.pop()
                    dirs_visited += 1
                    try:
                        entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
                    except Exception:
                        continue
                    for entry in entries:
                        if entry.name in SKIP:
                            continue
                        if entry.is_dir():
                            stack.append(entry)
                            continue
                        if entry.suffix in EXTS:
                            rel = str(entry.relative_to(base)).replace('\\', '/')
                            if q is None or q.lower() in rel.lower():
                                files.append(rel)
                files = files[:500]

    return {'files': files, 'count': len(files)}


@app.get("/projects/{project}/symbols")
def get_project_symbols(
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
        conn = psycopg2.connect(db_url, connect_timeout=1)
        try:
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
        finally:
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
    """Trigger codebase re-indexing for a project. Returns immediately with run_id."""
    from core.context.indexer import CodeIndexer

    config_path = _PROJECTS_ROOT / project_id / 'config.json'
    if not config_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    config = json.loads(config_path.read_text())
    codebase_path = _resolve_codebase_path(project_id, config)
    if not codebase_path:
        raise HTTPException(
            status_code=400,
            detail="config.json must include 'codebase_path' or 'git_repo'",
        )

    db_url = os.getenv('DATABASE_URL', '')
    run_id = f'idx_{uuid.uuid4().hex[:12]}'
    run_record = {
        'run_id': run_id,
        'project': project_id,
        'status': 'running',
        'files_processed': 0,
        'files_total': 0,
        'chunks_created': 0,
        'started_at': datetime.utcnow().isoformat(),
        'completed_at': None,
        'error': None,
    }
    _index_runs[run_id] = run_record

    async def _run_index():
        try:
            indexer = CodeIndexer(
                project_id=project_id,
                codebase_path=codebase_path,
                db_url=db_url,
            )

            def _progress(processed, total, chunks):
                run_record['files_processed'] = processed
                run_record['files_total'] = total
                run_record['chunks_created'] = chunks

            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: indexer.index_project(
                    force_reindex=body.force,
                    progress_callback=_progress,
                ),
            )

            run_record['status'] = 'complete'
            run_record['files_processed'] = result['indexed'] + result['skipped']
            run_record['files_total'] = result['total_files']
            run_record['chunks_created'] = result['chunks_created']
            run_record['completed_at'] = datetime.utcnow().isoformat()
        except Exception as e:
            run_record['status'] = 'failed'
            run_record['error'] = str(e)
            run_record['completed_at'] = datetime.utcnow().isoformat()

    asyncio.create_task(_run_index())
    return {
        'run_id': run_id,
        'project': project_id,
        'status': 'started',
        'message': 'Reindex started in background',
    }


@app.get("/projects/{project_id}/index/{run_id}")
async def index_status(
    project_id: str,
    run_id: str,
    _: bool = Depends(verify_api_key),
):
    """Check status of a reindex run."""
    record = _index_runs.get(run_id)
    if not record or record['project'] != project_id:
        raise HTTPException(status_code=404, detail=f"Index run '{run_id}' not found")
    return record


@app.get("/projects/{project_id}/cost")
async def get_cost_summary(
    project_id: str,
    _: bool = Depends(verify_api_key),
):
    """API cost breakdown for a project."""
    from core.memory.store import MemoryStore
    data_dir = os.getenv('DEEK_DATA_DIR') or os.getenv('CLAW_DATA_DIR', './data')
    store = MemoryStore(project_id, data_dir)
    return {
        'project_id': project_id,
        'breakdown': store.get_cost_summary(),
    }


@app.get("/cost/today")
async def get_today_cost(
    since: Optional[str] = None,
    _: bool = Depends(verify_api_key),
):
    """
    Total spend across ALL projects since midnight UTC today (or ?since= date).
    Broken down by provider: anthropic | openai | deepseek | local
    """
    from datetime import date as _date

    # Default: midnight UTC today
    if since:
        since_dt = since if 'T' in since else f'{since}T00:00:00'
    else:
        since_dt = datetime.combine(_date.today(), datetime.min.time()).isoformat()

    data_dir = os.getenv('DEEK_DATA_DIR') or os.getenv('CLAW_DATA_DIR', './data')

    # If no agents loaded yet, scan projects dir directly
    agents_to_query: dict[str, DeekAgent] = dict(_agents)
    if not agents_to_query and _PROJECTS_ROOT.exists():
        for project_dir in sorted(_PROJECTS_ROOT.iterdir()):
            config_path = project_dir / 'config.json'
            if project_dir.is_dir() and not project_dir.name.startswith('_') and config_path.exists():
                try:
                    config = json.loads(config_path.read_text())
                    agents_to_query[project_dir.name] = DeekAgent(
                        project_id=project_dir.name,
                        config=config,
                    )
                except Exception:
                    pass

    # Gather per-project rows
    all_rows: list[dict] = []
    for project_id, agent in agents_to_query.items():
        for row in agent.memory.get_spend_since(since_dt):
            all_rows.append({**row, 'project_id': project_id})

    # Map model name → provider bucket
    def _provider(model: str) -> str:
        m = (model or '').lower()
        if any(x in m for x in ('claude', 'anthropic')):
            return 'anthropic'
        if any(x in m for x in ('gpt', 'o1', 'openai')):
            return 'openai'
        if 'deepseek' in m:
            return 'deepseek'
        return 'local'  # Ollama / qwen / llama

    # Aggregate by provider
    by_provider: dict[str, dict] = {}
    total_cost = 0.0
    for row in all_rows:
        p = _provider(row['model'])
        bucket = by_provider.setdefault(p, {
            'provider': p, 'calls': 0, 'tokens': 0, 'cost_usd': 0.0, 'models': [],
        })
        bucket['calls']    += row['calls']
        bucket['tokens']   += row['tokens'] or 0
        bucket['cost_usd'] = round(bucket['cost_usd'] + row['cost_usd'], 6)
        if row['model'] and row['model'] not in bucket['models']:
            bucket['models'].append(row['model'])
        total_cost += row['cost_usd']

    return {
        'since': since_dt,
        'total_cost_usd': round(total_cost, 6),
        'by_provider': list(by_provider.values()),
        'by_project': all_rows,
    }


# ─── Deek Protocol Endpoints ─────────────────────────────────────────────
# These four endpoints expose retrieval, memory write-back, and cost logging
# as standalone HTTP calls — required by the MCP server and deek.ps1 wrapper.


@app.get("/retrieve")
async def retrieve_codebase_context(
    query: str,
    project: str,
    limit: int = 10,
    hybrid: bool = True,
    _: bool = Depends(verify_api_key),
):
    """
    Hybrid BM25 + pgvector retrieval of code chunks.
    The primary memory-retrieval endpoint for the Deek Protocol (Step 1).
    """
    agent = get_agent(project)

    if not os.getenv('DATABASE_URL', ''):
        return {
            'chunks': [],
            'total': 0,
            'project': project,
            'query': query,
            'error': 'DATABASE_URL not set — pgvector retrieval unavailable',
        }

    try:
        loop = asyncio.get_event_loop()
        chunks = await loop.run_in_executor(
            None,
            lambda: agent.context.retrieve_tier2(
                task=query,
                embedding_fn=agent._embed,
                subproject_id=None,
            ),
        )
    except Exception as exc:
        return {
            'chunks': [],
            'total': 0,
            'project': project,
            'query': query,
            'error': f'Retrieval failed: {exc}',
        }

    # Trim to requested limit and normalise keys for MCP spec
    trimmed = chunks[:limit]
    results = [
        {
            'file_path': c.get('file', ''),
            'content': c.get('content', ''),
            'score': c.get('score', 0),
            'retrieval_method': c.get('match_quality', 'unknown'),
            'chunk_type': c.get('chunk_type', ''),
        }
        for c in trimmed
    ]
    return {
        'chunks': results,
        'total': len(results),
        'project': project,
        'query': query,
    }


@app.get("/memory/retrieve")
async def retrieve_chat_history(
    query: str,
    project: str,
    limit: int = 10,
    outcome_filter: Optional[str] = None,
    _: bool = Depends(verify_api_key),
):
    """
    Search past development decisions and chat history from memory.
    The second retrieval endpoint for the Deek Protocol (Step 1).
    """
    from core.memory.store import MemoryStore
    data_dir = os.getenv('DEEK_DATA_DIR') or os.getenv('CLAW_DATA_DIR', './data')
    store = MemoryStore(project, data_dir)

    try:
        results = store.search_decisions(query)
    finally:
        store.close()

    # Apply outcome filter if provided
    if outcome_filter:
        results = [r for r in results if r.get('type', '') == outcome_filter]

    # Trim to limit
    results = results[:limit]

    # Normalise to MCP spec shape
    entries = [
        {
            'query': r.get('query', '') or query,
            'decision': r.get('description', ''),
            'rejected': r.get('rejected', ''),
            'outcome': r.get('type', ''),
            'model': r.get('model_used', ''),
            'files_changed': r.get('files', []),
            'created_at': r.get('timestamp', ''),
        }
        for r in results
    ]
    return {
        'entries': entries,
        'total': len(entries),
        'project': project,
    }


@app.post("/memory/write")
async def write_memory(
    body: MemoryWriteRequest,
    _: bool = Depends(verify_api_key),
):
    """
    Write a memory entry after completing a task.
    The write-back endpoint for the Deek Protocol (Step 4).
    """
    from core.memory.store import MemoryStore
    data_dir = os.getenv('DEEK_DATA_DIR') or os.getenv('CLAW_DATA_DIR', './data')
    store = MemoryStore(body.project, data_dir)

    session_id = body.session_id or f'cairn_{uuid.uuid4().hex[:12]}'

    try:
        store.record_decision(
            session_id=session_id,
            decision_type=body.outcome,
            description=body.decision,
            reasoning=body.rejected,
            files_affected=body.files_changed,
            project=body.project,
            query=body.query,
            rejected=body.rejected,
            model_used=body.model,
            delegation_decision=body.delegation_decision,
        )
    finally:
        store.close()

    # Also embed into pgvector for semantic retrieval
    semantic_embedded = False
    try:
        semantic_embedded = _embed_memory_to_pgvector(
            project=body.project,
            session_id=session_id,
            query=body.query,
            decision=body.decision,
            rejected=body.rejected,
            outcome=body.outcome,
        )
    except Exception as exc:
        logger.warning("Memory semantic embedding failed (non-fatal): %s", exc)

    return {
        'id': session_id,
        'project': body.project,
        'outcome': body.outcome,
        'semantic_embedded': semantic_embedded,
        'written_at': datetime.utcnow().isoformat() + 'Z',
    }


def _embed_memory_to_pgvector(
    project: str,
    session_id: str,
    query: str,
    decision: str,
    rejected: str,
    outcome: str,
) -> bool:
    """Embed a memory write-back entry into claw_code_chunks for semantic search."""
    import hashlib

    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        return False

    # Build the text to embed — same fields that make it useful for retrieval
    parts = [
        f"Task: {query}" if query else '',
        f"Decision: {decision}" if decision else '',
        f"Rejected: {rejected}" if rejected else '',
        f"Outcome: {outcome}" if outcome else '',
    ]
    content = '\n'.join(p for p in parts if p)
    if not content.strip():
        return False

    content_hash = hashlib.sha256(content.encode()).hexdigest()
    file_path = f'memory/{project}/{session_id}'
    chunk_name = query[:200] if query else decision[:200]

    try:
        import psycopg2
        from pgvector.psycopg2 import register_vector
        conn = psycopg2.connect(db_url, connect_timeout=5)
        register_vector(conn)
    except Exception:
        return False

    try:
        from core.wiki.embeddings import get_embed_fn
        embed_fn = get_embed_fn()
        if not embed_fn:
            conn.close()
            return False

        # Check dedup
        with conn.cursor() as cur:
            cur.execute(
                """SELECT content_hash FROM claw_code_chunks
                   WHERE project_id = %s AND file_path = %s AND chunk_type = 'memory'""",
                (project, file_path),
            )
            existing = cur.fetchone()
            if existing and existing[0] == content_hash:
                conn.close()
                return True  # Already embedded

        embedding = embed_fn(content[:6000])

        with conn.cursor() as cur:
            cur.execute(
                """DELETE FROM claw_code_chunks
                   WHERE project_id = %s AND file_path = %s AND chunk_type = 'memory'""",
                (project, file_path),
            )
            cur.execute(
                """INSERT INTO claw_code_chunks
                   (project_id, file_path, chunk_content, chunk_type, chunk_name,
                    content_hash, embedding, indexed_at)
                   VALUES (%s, %s, %s, 'memory', %s, %s, %s::vector, NOW())""",
                (project, file_path, content, chunk_name, content_hash, embedding),
            )
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        logger.warning("pgvector memory embed error: %s", exc)
        try:
            conn.close()
        except Exception:
            pass
        return False


@app.get("/memory/entries")
async def list_memory_entries(
    project: str,
    limit: int = 50,
    offset: int = 0,
    q: Optional[str] = None,
    _: bool = Depends(verify_api_key),
):
    """List memory entries with pagination and optional search."""
    from core.memory.store import MemoryStore
    data_dir = os.getenv('DEEK_DATA_DIR') or os.getenv('CLAW_DATA_DIR', './data')
    store = MemoryStore(project, data_dir)
    try:
        entries, total = store.list_decisions(limit=limit, offset=offset, query_filter=q or '')
    finally:
        store.close()
    return {'entries': entries, 'total': total, 'project': project}


@app.get("/memory/entries/{entry_id}")
async def get_memory_entry(
    entry_id: int,
    project: str,
    _: bool = Depends(verify_api_key),
):
    """Get a single memory entry by ID."""
    from core.memory.store import MemoryStore
    data_dir = os.getenv('DEEK_DATA_DIR') or os.getenv('CLAW_DATA_DIR', './data')
    store = MemoryStore(project, data_dir)
    try:
        entry = store.get_decision(entry_id)
    finally:
        store.close()
    if not entry:
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
    return entry


@app.put("/memory/entries/{entry_id}")
async def update_memory_entry(
    entry_id: int,
    project: str,
    body: MemoryUpdateRequest,
    _: bool = Depends(verify_api_key),
):
    """Update an existing memory entry. Only provided fields are changed."""
    from core.memory.store import MemoryStore
    data_dir = os.getenv('DEEK_DATA_DIR') or os.getenv('CLAW_DATA_DIR', './data')
    store = MemoryStore(project, data_dir)
    try:
        updated = store.update_decision(
            decision_id=entry_id,
            description=body.description,
            reasoning=body.reasoning,
            query=body.query,
            rejected=body.rejected,
            decision_type=body.decision_type,
            files_affected=body.files_affected,
        )
    finally:
        store.close()
    if not updated:
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
    return {'updated': True, 'id': entry_id}


@app.delete("/memory/entries/{entry_id}")
async def delete_memory_entry(
    entry_id: int,
    project: str,
    _: bool = Depends(verify_api_key),
):
    """Delete a memory entry."""
    from core.memory.store import MemoryStore
    data_dir = os.getenv('DEEK_DATA_DIR') or os.getenv('CLAW_DATA_DIR', './data')
    store = MemoryStore(project, data_dir)
    try:
        deleted = store.delete_decision(entry_id)
    finally:
        store.close()
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
    return {'deleted': True, 'id': entry_id}


@app.post("/costs/log")
async def log_cost(
    body: CostLogRequest,
    _: bool = Depends(verify_api_key),
):
    """
    Log the cost of every model used in a prompt.
    The cost-logging endpoint for the Deek Protocol (Step 4b).
    Writes to both SQLite (via add_message) and CSV.
    """
    from core.memory.store import MemoryStore
    data_dir = os.getenv('DEEK_DATA_DIR') or os.getenv('CLAW_DATA_DIR', './data')
    store = MemoryStore(body.project, data_dir)

    now = datetime.utcnow().isoformat() + 'Z'

    try:
        # Write each model's cost as a conversation entry with role='cost'
        # This leverages the existing cost tracking via get_spend_since()
        for entry in body.costs:
            store.add_message(
                session_id=body.session_id,
                role='assistant',  # cost tracking piggybacks on assistant rows
                content=f'[cost-log] {body.prompt_summary}',
                channel='cairn-protocol',
                model_used=entry.model,
                tokens_used=entry.tokens_in + entry.tokens_out,
                cost_usd=entry.cost_gbp,  # stored as cost_usd column but is GBP
            )
    finally:
        store.close()

    # Also append to CSV
    csv_path = Path(data_dir) / 'cost_log.csv'
    csv_existed = csv_path.exists()
    try:
        with open(csv_path, 'a', encoding='utf-8') as f:
            if not csv_existed:
                f.write(
                    'timestamp,session_id,project,prompt_summary,'
                    'model,tokens_in,tokens_out,cost_gbp,total_cost_gbp\n'
                )
            for entry in body.costs:
                f.write(
                    f'{now},{body.session_id},{body.project},'
                    f'{body.prompt_summary},{entry.model},'
                    f'{entry.tokens_in},{entry.tokens_out},'
                    f'{entry.cost_gbp},{body.total_cost_gbp}\n'
                )
    except Exception:
        pass  # CSV is best-effort; SQLite is the primary store

    return {
        'logged': True,
        'session_id': body.session_id,
        'total_cost_gbp': body.total_cost_gbp,
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


# ── Embedding model status cache (60s TTL) ───────────────────────────────────
_embedding_model_cache: dict | None = None
_embedding_model_cache_ts: float = 0.0
_EMBEDDING_CACHE_TTL = 60.0


async def _embedding_model_status() -> dict:
    """Return embedding model availability with 60s cache."""
    global _embedding_model_cache, _embedding_model_cache_ts
    now = time.monotonic()
    if _embedding_model_cache and (now - _embedding_model_cache_ts) < _EMBEDDING_CACHE_TTL:
        return _embedding_model_cache

    model_name = os.getenv('OLLAMA_EMBED_MODEL', 'nomic-embed-text')
    ollama_base = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
    result: dict = {
        'name': model_name,
        'available': False,
        'ollama_running': False,
        'latency_ms': None,
    }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            # Check if Ollama is running
            tags_r = await client.get(f'{ollama_base}/api/tags')
            if tags_r.status_code == 200:
                result['ollama_running'] = True
                installed = [m['name'] for m in tags_r.json().get('models', [])]
                if any(model_name in m for m in installed):
                    # Test embedding latency
                    t0 = time.monotonic()
                    embed_r = await client.post(
                        f'{ollama_base}/api/embeddings',
                        json={'model': model_name, 'prompt': 'test'},
                    )
                    if embed_r.status_code == 200 and embed_r.json().get('embedding'):
                        result['available'] = True
                        result['latency_ms'] = round((time.monotonic() - t0) * 1000, 1)
    except Exception:
        pass

    _embedding_model_cache = result
    _embedding_model_cache_ts = now
    return result


async def _all_project_chunk_counts() -> dict[str, int]:
    """Query pgvector for chunk counts grouped by project_id."""
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        return {}
    try:
        import psycopg2 as _pg

        def _query():
            conn = _pg.connect(db_url, connect_timeout=1)
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT project_id, COUNT(*) FROM claw_code_chunks GROUP BY project_id'
                )
                rows = cur.fetchall()
            conn.close()
            return {row[0]: row[1] for row in rows}

        return await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, _query),
            timeout=2.0,
        )
    except Exception:
        return {}


@app.get("/identity/status")
async def identity_status():
    """Deek identity + module reachability. No auth (internal)."""
    from core.identity import assembler as _identity_assembler
    from core.identity import probe as _identity_probe
    probe_status = _identity_probe.get_probe_status()
    modules = _identity_assembler.get_modules()
    return {
        'identity_hash': _identity_assembler.get_identity_hash(),
        'loaded_module_count': len(modules),
        'declared_modules': [m.name for m in modules],
        'reachability': probe_status['modules'],
        'last_probe': probe_status['last_probe'],
    }


@app.get("/identity/prompt")
async def identity_prompt():
    """Exact system-prompt prefix the next request would use.

    Gated behind DEEK_DEBUG=true — the prefix can contain sensitive
    configuration hints and shouldn't be public.
    """
    if os.getenv('DEEK_DEBUG', '').lower() not in {'1', 'true', 'yes', 'on'}:
        raise HTTPException(status_code=404, detail='not found')
    from core.identity import assembler as _identity_assembler
    from core.identity import probe as _identity_probe
    prefix = _identity_assembler.get_system_prompt_prefix(
        reachable=_identity_probe.get_reachable_modules(),
        errors=_identity_probe.get_errors(),
    )
    return {'prefix': prefix, 'hash': _identity_assembler.get_identity_hash()}


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
        async with httpx.AsyncClient(timeout=1) as client:
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

    # ── Embedding model status (cached 60s) ─────────────────────────────
    embedding_model = await _embedding_model_status()

    # ── Per-project index status ──────────────────────────────────────────
    index_status = {}
    chunk_counts = await _all_project_chunk_counts()
    for pid, agent in _agents.items():
        watcher = getattr(agent, '_watcher', None)
        chunks = chunk_counts.get(pid, 0)
        last_reindex = None
        if watcher and hasattr(watcher, 'last_reindex_at') and watcher.last_reindex_at:
            last_reindex = watcher.last_reindex_at.isoformat()
        index_status[pid] = {
            'chunks': chunks,
            'indexed': chunks > 0,
            'watcher_active': bool(watcher and getattr(watcher, '_active', False)),
            'last_reindex': last_reindex,
        }

    # Circuit breaker state (the router's view of Ollama health)
    from core.models import ollama_health as _ollama_health_mod
    ollama_circuit = _ollama_health_mod.snapshot()
    ollama_context_limit = _ollama_health_mod.local_context_limit()
    hardware_profile = os.getenv('DEEK_HARDWARE_PROFILE', 'dev_desktop')

    return {
        'status': 'ok',
        'retrieval_mode': _default_retrieval_mode(),
        'bm25_available': _bm25_available(),
        'ollama_available': ollama_reachable,
        'ollama_active_model': ollama_active_model,
        'ollama_model_ready': ollama_model_ready,
        'ollama_vram_warning': ollama_vram_warning,
        'ollama_installed_models': installed_models,
        'ollama_circuit': ollama_circuit,
        'ollama_context_limit': ollama_context_limit,
        'hardware_profile': hardware_profile,
        # Keep legacy key for backwards compat
        'ollama_model': ollama_active_model,
        'ollama_model_ready': ollama_model_ready,
        'anthropic_key_set': bool(os.getenv('ANTHROPIC_API_KEY')),
        'deepseek_key_set': bool(os.getenv('DEEPSEEK_API_KEY')),
        'openai_key_set': bool(os.getenv('OPENAI_API_KEY')),
        'projects_loaded': list(_agents.keys()),
        'skills_loaded': sum(len(agent.skills.list_skills()) for agent in _agents.values()),
        'skill_classifier_ready': getattr(app.state, 'skill_classifier_ready', False),
        'embedding_model': embedding_model,
        'index_status': index_status,
    }


@app.post("/admin/test-embedding")
async def test_embedding(_: bool = Depends(verify_api_key)):
    """End-to-end embedding pipeline test — tries a real embed call."""
    model_name = os.getenv('OLLAMA_EMBED_MODEL', 'nomic-embed-text')
    ollama_base = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
    result: dict = {
        'success': False,
        'model': model_name,
        'dim': None,
        'latency_ms': None,
        'error': None,
    }
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            t0 = time.monotonic()
            r = await client.post(
                f'{ollama_base}/api/embeddings',
                json={'model': model_name, 'prompt': 'Hello world embedding test'},
            )
            latency = round((time.monotonic() - t0) * 1000, 1)
            if r.status_code == 200:
                embedding = r.json().get('embedding', [])
                if embedding:
                    result['success'] = True
                    result['dim'] = len(embedding)
                    result['latency_ms'] = latency
                else:
                    result['error'] = 'Empty embedding returned'
            else:
                result['error'] = f'HTTP {r.status_code}: {r.text[:200]}'
    except Exception as e:
        result['error'] = str(e)
    return result


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

    The orchestrator drives DEEK toward `goal` by iterating:
      assess state → plan next task → execute task → repeat

    Returns immediately with a run_id. Poll GET /wiggum/{run_id} for status.

    Example:
        POST /wiggum
        {
          "goal": "Add a test suite to DEEK",
          "success_criteria": [
            "tests/ directory exists with at least 10 tests",
            "pytest passes with 0 failures",
            "tests cover /health, /chat, and tool dispatch"
          ],
          "project": "deek"
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
    wiggum_agent = DeekAgent(
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
    Lightweight self-assessment of DEEK's core components.
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
        data_dir = os.getenv('DEEK_DATA_DIR') or os.getenv('CLAW_DATA_DIR', './data')
        store = MemoryStore('self-test', data_dir)
        store.close()
        checks['memory_store'] = {'status': 'ok', 'backend': 'sqlite'}
    except Exception as e:
        checks['memory_store'] = {'status': 'error', 'detail': str(e)}

    # 3. Context engine
    try:
        from core.context.engine import ContextEngine
        config_path = Path('projects/deek/config.json')
        if config_path.exists():
            cfg = json.loads(config_path.read_text())
            engine = ContextEngine(project_id='deek', config=cfg)
            checks['context_engine'] = {'status': 'ok'}
        else:
            checks['context_engine'] = {'status': 'skip', 'detail': 'deek config not found'}
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
# Image analysis via Claude Vision (used by web-business upload routes)
# ─────────────────────────────────────────────────────────────────────────────

class ImageAnalysisRequest(BaseModel):
    image_base64: str
    media_type: str = 'image/png'
    filename: str = 'image.png'
    prompt: str = 'Describe this image in detail. If it contains text, transcribe all visible text.'


@app.post("/analyze-image")
async def analyze_image(
    body: ImageAnalysisRequest,
    _: bool = Depends(verify_api_key),
):
    """Analyze an image using Claude Vision. Returns text description."""
    import anthropic

    api_key = os.getenv('ANTHROPIC_API_KEY', '')
    if not api_key:
        raise HTTPException(status_code=503, detail='ANTHROPIC_API_KEY not set')

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model='claude-sonnet-4-20250514',
        max_tokens=1024,
        messages=[{
            'role': 'user',
            'content': [
                {
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': body.media_type,
                        'data': body.image_base64,
                    },
                },
                {'type': 'text', 'text': body.prompt},
            ],
        }],
    )

    text_block = next((b for b in response.content if b.type == 'text'), None)
    description = text_block.text if text_block else ''

    return {'description': description, 'filename': body.filename}


# ─────────────────────────────────────────────────────────────────────────────
# Full-pipeline agent self-test
# Exercises the complete chat → tool loop → answer path using read-only tools.
# Safe to run at any time — no files are written, no git operations.
# ─────────────────────────────────────────────────────────────────────────────

_SELF_TEST_PROMPT = """\
You are running a structured self-test of the DEEK system. \
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

## DEEK Self-Test Report
### Files: PASS / FAIL (list any missing)
### Structure: PASS / FAIL
### Subprojects: PASS / FAIL
### Server: RUNNING / UNREACHABLE
### Git: CLEAN / DIRTY (list changed files if dirty)
### Overall: PASS / FAIL
"""


@app.get("/agent-self-test")
async def agent_self_test(
    project: str = 'deek',
    _: bool = Depends(verify_api_key),
):
    """
    Full-pipeline self-test: sends a read-only assessment prompt to the DEEK
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
            project_id = run.get('project', 'deek')
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


def _read_eval_cache() -> dict:
    """Read last DEEK eval result from cache file, or return nulls."""
    cache_path = _CLAW_ROOT / 'data' / 'eval_cache.json'
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except Exception:
            pass
    return {'passed': None, 'failed': None, 'suite': None, 'last_run': None}


async def _ollama_status_fast() -> dict:
    """Lightweight Ollama status check (reuses health logic, 3s timeout)."""
    from core.models.ollama_client import _vram_for, _VRAM_AVAILABLE_GB
    ollama_base = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
    preferred = os.getenv('OLLAMA_MODEL_PREFERRED', 'qwen2.5-coder:7b')
    fallback  = os.getenv('OLLAMA_MODEL', 'qwen2.5-coder:7b')
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=1) as hx:
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
            conn = _pg.connect(db_url, connect_timeout=1)
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
            timeout=1.0,
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
    now = time.monotonic()
    cached = _status_summary_cache.get('data')
    expires_at = float(_status_summary_cache.get('expires_at', 0.0) or 0.0)
    if cached is not None and now < expires_at:
        return cached

    # Run independent queries concurrently
    git = _git_info()
    tests = _read_test_cache()
    eval_results = _read_eval_cache()
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
            'retrieval_mode': agent.context.retrieval_mode,
            'skill_count': len(agent.skills.list_skills()),
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
                        'retrieval_mode': None,
                        'skill_count': 0,
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

    # Skills summary
    skill_loader = getattr(app.state, 'skill_loader', None)
    skills_info = []
    if skill_loader:
        for s in skill_loader.all_skills():
            skills_info.append({
                'skill_id': s.skill_id,
                'project_id': s.project_id,
                'display_name': s.display_name,
                'triggers': s.triggers[:5],
                'subproject_id': s.subproject_id,
            })

    payload = {
        'api_status': 'ok',
        **git,
        'test_results': tests,
        'eval_results': eval_results,
        'api_keys': {
            'anthropic': bool(os.getenv('ANTHROPIC_API_KEY')),
            'deepseek': bool(os.getenv('DEEPSEEK_API_KEY')),
            'openai': bool(os.getenv('OPENAI_API_KEY')),
        },
        'ollama': ollama,
        'projects': projects_info,
        'skills': skills_info,
        'skill_classifier_ready': getattr(app.state, 'skill_classifier_ready', False),
        'wiggum_runs': wiggum_summary,
        'pending_approvals': pending,
        'generated_at': datetime.utcnow().isoformat(),
    }

    _status_summary_cache['data'] = payload
    _status_summary_cache['expires_at'] = now + 5.0
    return payload


# ─── WIGGUM list / get (existing, kept here) ─────────────────────────────────

# Register WhatsApp proxy route
from api.routes.whatsapp import router as whatsapp_router
app.include_router(whatsapp_router)

# Register Amazon Intelligence routes
from api.routes.amazon_intel import router as ami_router
app.include_router(ami_router)

# Register Amazon Analytics routes (Sprint 1 Revenue Truth Engine)
from api.routes.analytics import router as ami_analytics_router
app.include_router(ami_analytics_router)

# Register Etsy Intelligence routes
from api.routes.etsy_intel import router as etsy_router
app.include_router(etsy_router)

# Register Wiki Layer routes
from api.routes.wiki import router as wiki_router
app.include_router(wiki_router)

# Register Render catalogue context routes
from api.routes.render import router as render_router
app.include_router(render_router)

# Register Email Ingestion routes
from api.routes.email import router as email_router
app.include_router(email_router)

# Register Wiki Generation routes
from api.routes.wiki_gen import router as wiki_gen_router
app.include_router(wiki_gen_router)

# Register Admin routes (wiki-sync, operational endpoints)
from api.routes.admin import router as admin_router
app.include_router(admin_router)

# Register Deek Social routes (drafting + proof-reading assistant for Jo)
from api.routes.social import router as social_router
app.include_router(social_router)

# Register Deek module federation routes — dual-mount for rename transition
from api.routes.deek_federation import router as federation_router
app.include_router(federation_router, prefix="/api/deek")
app.include_router(federation_router, prefix="/api/cairn")  # legacy alias

# Register ambient + voice Phase 0 routes (morning-number, ambient, tasks)
from api.routes.ambient import router as ambient_router
app.include_router(ambient_router, prefix="/api/deek")

# Register Counterparty Risk proxy routes (Phase 0)
from api.routes.counterparty_risk import router as counterparty_risk_router
app.include_router(counterparty_risk_router)

# Register cross-module delegation routes (deek_delegate MCP tool backend)
from api.routes.delegation import router as delegation_router
app.include_router(delegation_router)

# Register Deek Analyze route (enquiry analysis endpoint for CRM)
from api.routes.analyze import router as analyze_router
app.include_router(analyze_router)

# Best-effort: ensure social_* tables exist on startup. Failure here must
# not block API startup — the /social/migrate endpoint can repair if needed.
try:
    from core.social.db import ensure_schema as _ensure_social_schema
    _ensure_social_schema()
except Exception as _social_schema_exc:  # pragma: no cover
    print(f'[Deek] social schema bootstrap skipped: {_social_schema_exc}')

# Best-effort: ensure cairn_delegation_log table exists on startup.
try:
    from core.delegation.log import ensure_table as _ensure_delegation_table
    _ensure_delegation_table()
except Exception as _delegation_schema_exc:  # pragma: no cover
    print(f'[Deek] delegation log bootstrap skipped: {_delegation_schema_exc}')
