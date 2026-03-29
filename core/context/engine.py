import os
import json
import logging
from pathlib import Path
from typing import Callable

from core.memory.assembler import MemoryAssembler

_REPO_ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


class ContextEngine:
    """
    Three-tier context loading for a project.

    Tier 1 — Core context (always loaded, ~2000 tokens)
        The hand-authored core.md for this project.
        Contains: what the app is, critical rules, domain vocabulary,
        architecture overview, file structure, common patterns.
        NEVER auto-generated. Written and maintained by the operator.
        Loaded on every request regardless of task.

    Tier 2 — Retrieved context (similarity search, ~8000 tokens)
        pgvector similarity search on the task description.
        Returns the most relevant code chunks from the indexed codebase.
        Updated automatically when files change.

    Tier 3 — On-demand context (explicit file load, unlimited)
        Agent can request a specific file in full.
        Used when Tier 2 chunks aren't sufficient.
        Always requires an explicit agent tool call — never auto-loaded.
    """

    MAX_TIER1_TOKENS = 2000
    MAX_TIER2_TOKENS = 8000
    SIMILARITY_THRESHOLD = 0.65  # Lowered from 0.75 — broader recall
    MAX_TIER2_CHUNKS = 20        # Increased from 12 — more context per query

    def __init__(self, project_id: str, db_url: str):
        self.project_id = project_id
        self.db_url = db_url
        self.project_dir = _REPO_ROOT / 'projects' / project_id
        self.core_md_path = self.project_dir / 'core.md'
        self._conn = None
        self.hybrid_retriever = None
        self.assembler = MemoryAssembler()

        if self.db_url:
            try:
                from core.memory.retriever import HybridRetriever
                self.hybrid_retriever = HybridRetriever(self)
            except Exception as exc:
                logger.warning(
                    "[context] hybrid retrieval unavailable for %s: %s",
                    self.project_id,
                    exc,
                )

    @property
    def retrieval_mode(self) -> str:
        if self.hybrid_retriever and self.hybrid_retriever.is_available:
            return 'hybrid'
        if self.db_url:
            return 'cosine'
        return 'keyword'

    def _get_connection(self):
        if not self._conn or self._conn.closed:
            import psycopg2
            from pgvector.psycopg2 import register_vector
            self._conn = psycopg2.connect(self.db_url, connect_timeout=5)
            register_vector(self._conn)
        return self._conn

    def load_tier1(self) -> str:
        """Load the core.md for this project. Always present in every prompt."""
        if not self.core_md_path.exists():
            raise FileNotFoundError(
                f"core.md not found for project '{self.project_id}'. "
                f"Create it at: {self.core_md_path}\n"
                f"Use projects/_template/core.md as a starting point."
            )
        content = self.core_md_path.read_text(encoding='utf-8')
        estimated_tokens = len(content) / 4
        if estimated_tokens > self.MAX_TIER1_TOKENS:
            print(
                f"WARNING: core.md for '{self.project_id}' is "
                f"~{int(estimated_tokens)} tokens. "
                f"Keep it under {self.MAX_TIER1_TOKENS} tokens."
            )
        return content

    def retrieve_tier2(
        self,
        task: str,
        embedding_fn: Callable,
        subproject_id: str | None = None,
    ) -> list[dict]:
        """
        Retrieve relevant chunks. Falls back to keyword search if
        embedding generation fails (e.g. Ollama unavailable).

        When subproject_id is provided, chunks are filtered to that
        subproject plus project-level chunks (subproject_id IS NULL).
        """
        if self.hybrid_retriever and self.hybrid_retriever.is_available:
            try:
                return self.hybrid_retriever.retrieve(
                    task,
                    embedding_fn,
                    subproject_id=subproject_id,
                )
            except Exception as e:
                logger.warning(
                    "Hybrid retrieval failed for %s (%s), "
                    "falling back to cosine/keyword",
                    self.project_id,
                    e,
                )
        try:
            return self._retrieve_by_embedding(task, embedding_fn, subproject_id)
        except Exception as e:
            print(f"Embedding retrieval failed ({e}), "
                  f"falling back to keyword search")
            return self._retrieve_by_keyword(task, subproject_id)

    def _retrieve_by_embedding(
        self,
        task: str,
        embedding_fn: Callable,
        subproject_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """pgvector similarity search with optional subproject scoping."""
        limit = limit or self.MAX_TIER2_CHUNKS
        task_embedding = embedding_fn(task)
        conn = self._get_connection()
        with conn.cursor() as cur:
            if subproject_id:
                cur.execute("""
                    SELECT
                        file_path,
                        chunk_content,
                        chunk_type,
                        chunk_name,
                        1 - (embedding <=> %s::vector) AS similarity
                    FROM claw_code_chunks
                    WHERE
                        project_id = %s
                        AND (subproject_id IS NULL OR subproject_id = %s)
                        AND 1 - (embedding <=> %s::vector) > %s
                    ORDER BY similarity DESC
                    LIMIT %s
                """, (
                    task_embedding,
                    self.project_id,
                    subproject_id,
                    task_embedding,
                    self.SIMILARITY_THRESHOLD,
                    limit,
                ))
            else:
                cur.execute("""
                    SELECT
                        file_path,
                        chunk_content,
                        chunk_type,
                        chunk_name,
                        1 - (embedding <=> %s::vector) AS similarity
                    FROM claw_code_chunks
                    WHERE
                        project_id = %s
                        AND 1 - (embedding <=> %s::vector) > %s
                    ORDER BY similarity DESC
                    LIMIT %s
                """, (
                    task_embedding,
                    self.project_id,
                    task_embedding,
                    self.SIMILARITY_THRESHOLD,
                    limit,
                ))
            rows = cur.fetchall()
        return [
            {
                'file': row[0],
                'content': row[1],
                'chunk_type': row[2],
                'chunk_name': row[3],
                'score': float(row[4]),
            }
            for row in rows
        ]

    def _retrieve_by_keyword(
        self,
        task: str,
        subproject_id: str | None = None,
    ) -> list[dict]:
        """
        Simple keyword search fallback when embeddings are unavailable.
        Extracts meaningful words from the task and does ILIKE search.
        """
        import re
        stopwords = {
            'this', 'that', 'with', 'from', 'have', 'will',
            'what', 'when', 'where', 'which', 'there', 'their',
        }
        words = [
            w for w in re.findall(r'\b[a-zA-Z]{4,}\b', task.lower())
            if w not in stopwords
        ][:5]

        if not words:
            return []

        try:
            conn = self._get_connection()
            conditions = ' OR '.join(
                [f"chunk_content ILIKE %s" for _ in words]
            )
            if subproject_id:
                subproject_clause = (
                    "AND (subproject_id IS NULL OR subproject_id = %s)"
                )
                params = (
                    [self.project_id, subproject_id]
                    + [f'%{w}%' for w in words]
                    + [self.MAX_TIER2_CHUNKS]
                )
            else:
                subproject_clause = ''
                params = (
                    [self.project_id]
                    + [f'%{w}%' for w in words]
                    + [self.MAX_TIER2_CHUNKS]
                )
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT file_path, chunk_content, chunk_type, chunk_name,
                           0.5 AS similarity
                    FROM claw_code_chunks
                    WHERE project_id = %s
                      {subproject_clause}
                      AND ({conditions})
                    ORDER BY length(chunk_content) ASC
                    LIMIT %s
                """, params)
                rows = cur.fetchall()
            return [
                {
                    'file': row[0],
                    'content': row[1],
                    'chunk_type': row[2],
                    'chunk_name': row[3],
                    'score': float(row[4]),
                }
                for row in rows
            ]
        except Exception as e:
            print(f"Keyword fallback also failed: {e}")
            return []

    def get_all_chunks(
        self,
        subproject_id: str | None = None,
    ) -> list[dict]:
        """
        Return all stored chunks for the current project scope.

        Used by the hybrid retriever to build an in-memory BM25 index while
        preserving the same project/subproject isolation as cosine retrieval.
        """
        if not self.db_url:
            return []

        try:
            conn = self._get_connection()
            with conn.cursor() as cur:
                if subproject_id:
                    cur.execute("""
                        SELECT file_path, chunk_content, chunk_type, chunk_name
                        FROM claw_code_chunks
                        WHERE project_id = %s
                          AND (subproject_id IS NULL OR subproject_id = %s)
                        ORDER BY file_path, id
                    """, (self.project_id, subproject_id))
                else:
                    cur.execute("""
                        SELECT file_path, chunk_content, chunk_type, chunk_name
                        FROM claw_code_chunks
                        WHERE project_id = %s
                        ORDER BY file_path, id
                    """, (self.project_id,))
                rows = cur.fetchall()
        except Exception as exc:
            logger.warning(
                "[context] get_all_chunks failed for %s: %s",
                self.project_id,
                exc,
            )
            return []

        return [
            {
                'file': row[0],
                'content': row[1],
                'chunk_type': row[2],
                'chunk_name': row[3],
            }
            for row in rows
        ]

    async def resolve_mentions(
        self,
        mentions: list[dict],
        project_id: str,
        config: dict,
    ) -> list[dict]:
        """
        Resolve @ mention dicts to content chunks.

        Each mention: {'type': str, 'value': str, 'display': str}
        Returns list of {'label': str, 'content': str} ready to inject.
        Called before pgvector retrieval — mentioned context is always included.
        """
        import asyncio
        results = []
        codebase_path = config.get('codebase_path', '.')

        for mention in mentions:
            mtype = mention.get('type', '')
            value = mention.get('value', '')
            display = mention.get('display', value)
            try:
                if mtype == 'file':
                    content = await asyncio.to_thread(
                        self._read_file_safe, codebase_path, value
                    )
                    results.append({'label': f'file: {display}', 'content': content})

                elif mtype == 'folder':
                    chunks = await asyncio.to_thread(
                        self._read_folder_safe, codebase_path, value, 20
                    )
                    for label, content in chunks:
                        results.append({'label': f'file: {label}', 'content': content})

                elif mtype == 'symbol':
                    chunks = self._search_symbol_pgvector(value, project_id)
                    for chunk in chunks:
                        results.append({
                            'label': f'symbol: {display} ({chunk["file"]})',
                            'content': chunk['content'],
                        })

                elif mtype == 'session':
                    content = self._read_session_content(value)
                    if content:
                        results.append({'label': f'session: {display}', 'content': content})

                elif mtype == 'core':
                    content = self.load_tier1()
                    results.append({'label': 'core.md', 'content': content})

                elif mtype == 'web':
                    content = await self._web_search_chunk(value)
                    if content:
                        results.append({'label': f'web: {display}', 'content': content})

            except Exception as exc:
                results.append({
                    'label': f'{mtype}: {display}',
                    'content': f'[Could not resolve mention: {exc}]',
                })

        return results

    def _read_file_safe(self, codebase_path: str, file_path: str) -> str:
        """Read a file, enforcing the project boundary."""
        base = Path(codebase_path).resolve()
        target = (base / file_path).resolve()
        if not str(target).startswith(str(base)):
            raise PermissionError(f"Path '{file_path}' escapes project root")
        if not target.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        content = target.read_text(encoding='utf-8', errors='replace')
        # Cap at 8000 chars to avoid blowing the context window with one file
        if len(content) > 8000:
            content = content[:8000] + '\n… [truncated at 8000 chars]'
        return content

    def _read_folder_safe(
        self, codebase_path: str, folder: str, limit: int
    ) -> list[tuple[str, str]]:
        """Return (rel_path, content) for up to `limit` files in a folder."""
        base = Path(codebase_path).resolve()
        target = (base / folder).resolve()
        if not str(target).startswith(str(base)):
            raise PermissionError(f"Folder '{folder}' escapes project root")
        if not target.is_dir():
            raise FileNotFoundError(f"Folder not found: {folder}")
        SKIP = {'.git', '__pycache__', 'node_modules', '.venv', '.next'}
        EXTS = {'.py', '.ts', '.tsx', '.js', '.json', '.md'}
        results = []
        for p in sorted(target.rglob('*')):
            if len(results) >= limit:
                break
            if p.is_file() and p.suffix in EXTS:
                if not any(part in SKIP for part in p.parts):
                    rel = str(p.relative_to(base)).replace('\\', '/')
                    try:
                        text = p.read_text(encoding='utf-8', errors='replace')[:3000]
                        results.append((rel, text))
                    except Exception:
                        pass
        return results

    def _search_symbol_pgvector(self, symbol: str, project_id: str) -> list[dict]:
        """Find chunks whose chunk_name matches the symbol name."""
        try:
            conn = self._get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT file_path, chunk_content
                    FROM claw_code_chunks
                    WHERE project_id=%s AND chunk_name ILIKE %s
                    ORDER BY length(chunk_content) ASC
                    LIMIT 3
                    """,
                    (project_id, f'%{symbol}%'),
                )
                return [{'file': r[0], 'content': r[1]} for r in cur.fetchall()]
        except Exception:
            return []

    def _read_session_content(self, session_id: str) -> str:
        """Read session messages from SQLite store."""
        import sqlite3
        import glob as _glob
        # Find any SQLite DB that might contain this session
        data_dir = Path(os.getenv('CLAW_DATA_DIR', './data'))
        dbs = list(data_dir.glob('*.db'))
        for db_path in dbs:
            try:
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT role, content FROM messages WHERE session_id=? ORDER BY timestamp ASC LIMIT 30",
                    (session_id,),
                ).fetchall()
                conn.close()
                if rows:
                    lines = [f'{r["role"]}: {r["content"][:200]}' for r in rows]
                    return '\n'.join(lines)
            except Exception:
                pass
        return ''

    async def _web_search_chunk(self, query: str) -> str:
        """Perform a web search and return results as text."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    'https://api.duckduckgo.com/',
                    params={'q': query, 'format': 'json', 'no_html': '1'},
                )
                data = r.json()
                abstract = data.get('AbstractText', '')
                topics = data.get('RelatedTopics', [])[:3]
                parts = []
                if abstract:
                    parts.append(abstract)
                for t in topics:
                    if isinstance(t, dict) and t.get('Text'):
                        parts.append(t['Text'])
                return '\n'.join(parts) if parts else f'[No results for: {query}]'
        except Exception as exc:
            return f'[Web search failed: {exc}]'

    def load_tier3(self, file_path: str) -> str:
        """
        Load a complete file on demand.
        Enforces project root boundary — cannot escape to parent dirs.
        """
        config = self._load_config()
        project_root = Path(config.get('codebase_path', '.')).resolve()
        candidate = Path(file_path)
        if candidate.is_absolute():
            target = candidate.resolve()
        else:
            target = (project_root / file_path).resolve()

        norm_target = os.path.normcase(str(target))
        projects_dir = os.path.normcase(str(_REPO_ROOT / 'projects'))
        allowed = [os.path.normcase(str(project_root)), projects_dir]
        if not any(norm_target.startswith(r) for r in allowed):
            raise PermissionError(
                f"File '{file_path}' is outside the project root. "
                f"Agents cannot access files outside their project."
            )

        if not target.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        return target.read_text(encoding='utf-8', errors='replace')

    def build_context_prompt(
        self,
        task: str,
        embedding_fn: Callable,
        subproject_id: str | None = None,
        resolved_mentions: list[dict] | None = None,
        skill_context: str | None = None,
        include_metadata: bool = False,
    ) -> str | tuple[str, dict]:
        """Assemble full context string from all available tiers.

        resolved_mentions, if provided, are injected first under an
        '=== EXPLICITLY MENTIONED CONTEXT ===' header so the model
        knows these were pinned by the user rather than retrieved.
        """
        context_files: set[str] = set()
        core_text = self.load_tier1()
        chunks = self.retrieve_tier2(task, embedding_fn, subproject_id)
        match_quality_counts = {
            'exact': 0,
            'semantic': 0,
            'exact+semantic': 0,
        }
        for chunk in chunks:
            match_quality = chunk.get('match_quality')
            if match_quality in match_quality_counts:
                match_quality_counts[match_quality] += 1
            context_files.add(chunk['file'])

        for chunk in resolved_mentions or []:
            label = chunk.get('label', '')
            if label.startswith('file: '):
                context_files.add(label.removeprefix('file: ').strip())

        distilled_core = self.assembler.distill_core_rules(core_text)
        trimmed_skill = self.assembler._trim_text_to_budget(  # noqa: SLF001
            skill_context or '',
            self.assembler.SKILL_BUDGET_TOKENS,
        )
        parts = ['# PROJECT CONTEXT\n', distilled_core, '\n\n']

        if trimmed_skill:
            parts.append('=== ACTIVE SKILLS ===\n')
            parts.append(trimmed_skill)
            parts.append('\n=== END ACTIVE SKILLS ===\n\n')

        if resolved_mentions:
            parts.append('=== EXPLICITLY MENTIONED CONTEXT ===\n')
            for chunk in resolved_mentions:
                parts.append(f"[{chunk['label']}]\n")
                parts.append(chunk['content'])
                parts.append('\n\n')
            parts.append('=== END MENTIONED CONTEXT ===\n\n')

        if chunks:
            parts.append('# RELEVANT CONTEXT\n')
            parts.append(
                f"# (Retrieved {len(chunks)} relevant sections "
                f"from {self.retrieval_mode} index)\n\n"
            )
            for chunk in chunks:
                quality_part = (
                    f"{chunk.get('match_quality')}, " if chunk.get('match_quality') else ''
                )
                parts.append(
                    f"## {chunk['file']} [{quality_part}score: {chunk['score']:.2f}]\n"
                )
                parts.append(f"```\n{chunk['content']}\n```\n\n")

        prompt = ''.join(parts)
        core_tokens = self.assembler.estimate_tokens(distilled_core)
        skill_tokens = self.assembler.estimate_tokens(trimmed_skill)
        mention_tokens = sum(
            self.assembler.estimate_tokens(f"[{chunk['label']}] {chunk['content']}")
            for chunk in (resolved_mentions or [])
        )
        retrieved_tokens = sum(
            self.assembler.estimate_tokens(chunk.get('content', ''))
            for chunk in chunks
        )
        total_tokens = core_tokens + skill_tokens + mention_tokens + retrieved_tokens
        budget_pct = min(
            100.0,
            round(
                (total_tokens / self.assembler.TOTAL_BUDGET_TOKENS) * 100,
                1,
            ),
        )
        if not include_metadata:
            return prompt

        return prompt, {
            'context_files': sorted(context_files),
            'context_file_count': len(context_files),
            'retrieved_chunk_count': len(chunks),
            'retrieval_mode': self.retrieval_mode,
            'resolved_mention_count': len(resolved_mentions or []),
            'match_quality_counts': match_quality_counts,
            'retrieved_files': [chunk['file'] for chunk in chunks[:8]],
            'assembly_tokens': {
                'core': core_tokens,
                'skill': skill_tokens,
                'mentions': mention_tokens,
                'retrieved': retrieved_tokens,
                'total': total_tokens,
                'budget_pct': budget_pct,
            },
        }

    def _load_config(self) -> dict:
        config_path = self.project_dir / 'config.json'
        if config_path.exists():
            return json.loads(config_path.read_text())
        return {}
