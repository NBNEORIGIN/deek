"""
Indexes a codebase into pgvector for Tier 2 retrieval.

Usage:
    python scripts/index_project.py --project phloe --path /path/to/repo

Run after:
    - First setup of a project
    - After significant new code is added
    - After a WIGGUM build loop completes

Chunking strategy:
    Python files:        chunk by function and class (AST)
    TypeScript/JS:       chunk by function and component (regex)
    Markdown/docs:       chunk by section (## headers)
    Other:               chunk by 100-line windows with 20-line overlap

Each chunk stored with:
    - file_path (relative to project root)
    - chunk_content
    - chunk_type ('function'|'class'|'component'|'section'|'window')
    - chunk_name (function/class name if applicable)
    - embedding (768-dim vector from nomic-embed-text via Ollama)
    - project_id
    - last_modified
"""
import os
import hashlib
from pathlib import Path
from typing import Generator
import psycopg2

INCLUDE_EXTENSIONS = {
    '.py', '.ts', '.tsx', '.js', '.jsx',
    '.md', '.json', '.yaml', '.yml',
    '.html', '.css', '.sql',
}

EXCLUDE_PATTERNS = {
    'node_modules', '.git', '__pycache__', '.venv',
    'venv', 'dist', 'build', '.next', 'migrations',
    '.codeium', '.windsurf', 'coverage', '.pytest_cache',
}

# nomic-embed-text context window is 2048 tokens.
# Code averages ~4 chars/token; minified CSS/HTML can be ~1 char/token.
# Use 1500 chars as a safe limit for all content types.
MAX_CHUNK_CHARS = 1500


class CodeIndexer:

    def __init__(self, project_id: str, codebase_path: str, db_url: str):
        from pgvector.psycopg2 import register_vector
        self.project_id = project_id
        self.codebase_path = Path(codebase_path)
        self.db_url = db_url
        self.conn = psycopg2.connect(db_url, connect_timeout=5)
        register_vector(self.conn)
        self._ensure_schema()

    def _ensure_schema(self):
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            self.conn.commit()

            cur.execute("""
                CREATE TABLE IF NOT EXISTS claw_code_chunks (
                    id SERIAL PRIMARY KEY,
                    project_id VARCHAR(100) NOT NULL,
                    file_path VARCHAR(500) NOT NULL,
                    chunk_content TEXT NOT NULL,
                    chunk_type VARCHAR(50) NOT NULL,
                    chunk_name VARCHAR(200),
                    content_hash VARCHAR(64) NOT NULL,
                    embedding vector(768),
                    last_modified TIMESTAMP,
                    indexed_at TIMESTAMP DEFAULT NOW(),
                    subproject_id VARCHAR(200)
                );
            """)
            self.conn.commit()

            # Defensive migration — add subproject_id if missing from existing table
            cur.execute("""
                ALTER TABLE claw_code_chunks
                    ADD COLUMN IF NOT EXISTS subproject_id VARCHAR(200);
            """)
            self.conn.commit()

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_claw_chunks_project
                    ON claw_code_chunks(project_id);
            """)
            self.conn.commit()

            # IVFFlat index — only useful once there are rows,
            # so skip if table is empty to avoid unnecessary blocking.
            cur.execute("SELECT COUNT(*) FROM claw_code_chunks;")
            row_count = cur.fetchone()[0]
            if row_count >= 100:
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_claw_chunks_embedding
                        ON claw_code_chunks
                        USING ivfflat (embedding vector_cosine_ops)
                        WITH (lists = 100);
                """)
                self.conn.commit()

    def embed(self, text: str) -> list[float]:
        """
        Generate embedding via nomic-embed-text through Ollama.
        nomic-embed-text is designed for code and documents.
        768 dimensions. Runs on CPU — no GPU competition with inference.
        Truncates to MAX_CHUNK_CHARS as a safety net against 500 errors.
        """
        import httpx
        if len(text) > MAX_CHUNK_CHARS:
            text = text[:MAX_CHUNK_CHARS]
        response = httpx.post(
            f"{os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')}"
            f"/api/embeddings",
            json={'model': 'nomic-embed-text', 'prompt': text},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()['embedding']

    def _reconnect(self):
        """Reconnect to the database if connection was lost."""
        from pgvector.psycopg2 import register_vector
        try:
            self.conn.close()
        except Exception:
            pass
        self.conn = psycopg2.connect(self.db_url, connect_timeout=5)
        register_vector(self.conn)

    def index_file(self, file_path: str, force_reindex: bool = True) -> dict:
        """
        Reindex a single file by absolute or relative path.
        Callable from a watchdog event handler for incremental updates.

        Returns {'status': 'indexed'|'skipped'|'error', 'chunks': int, 'error': str}
        """
        abs_path = Path(file_path)
        if not abs_path.is_absolute():
            abs_path = self.codebase_path / file_path
        if not abs_path.exists():
            return {'status': 'error', 'chunks': 0, 'error': 'File not found'}

        ext = abs_path.suffix.lower()
        if ext not in INCLUDE_EXTENSIONS:
            return {'status': 'skipped', 'chunks': 0, 'error': ''}

        try:
            rel_path = str(abs_path.relative_to(self.codebase_path)).replace('\\', '/')
        except ValueError:
            rel_path = str(abs_path).replace('\\', '/')

        try:
            content = abs_path.read_text(encoding='utf-8', errors='replace')
            content_hash = hashlib.sha256(content.encode()).hexdigest()

            if self.conn.closed:
                self._reconnect()

            if not force_reindex and self._is_indexed(rel_path, content_hash):
                return {'status': 'skipped', 'chunks': 0, 'error': ''}

            self._delete_file_chunks(rel_path)
            chunks = list(self._chunk_file(abs_path, content))
            for chunk in chunks:
                embedding = self.embed(chunk['content'])
                self._store_chunk(
                    file_path=rel_path,
                    content=chunk['content'],
                    chunk_type=chunk['type'],
                    chunk_name=chunk.get('name'),
                    content_hash=content_hash,
                    embedding=embedding,
                    last_modified=abs_path.stat().st_mtime,
                )
            self.conn.commit()
            return {'status': 'indexed', 'chunks': len(chunks), 'error': ''}
        except Exception as exc:
            try:
                self.conn.rollback()
            except Exception:
                self._reconnect()
            return {'status': 'error', 'chunks': 0, 'error': str(exc)}

    def index_project(self, force_reindex: bool = False):
        """
        Index all files in the project codebase.
        Commits after each file so partial progress is preserved.
        """
        indexed = 0
        skipped = 0
        errors = 0

        for file_path in self._walk_codebase():
            try:
                rel_path = str(
                    file_path.relative_to(self.codebase_path)
                ).replace('\\', '/')
                content = file_path.read_text(
                    encoding='utf-8', errors='replace'
                )
                content_hash = hashlib.sha256(content.encode()).hexdigest()

                # Reconnect if connection dropped
                if self.conn.closed:
                    self._reconnect()

                if not force_reindex and self._is_indexed(
                    rel_path, content_hash
                ):
                    skipped += 1
                    continue

                self._delete_file_chunks(rel_path)

                chunks = list(self._chunk_file(file_path, content))
                for chunk in chunks:
                    embedding = self.embed(chunk['content'])
                    self._store_chunk(
                        file_path=rel_path,
                        content=chunk['content'],
                        chunk_type=chunk['type'],
                        chunk_name=chunk.get('name'),
                        content_hash=content_hash,
                        embedding=embedding,
                        last_modified=file_path.stat().st_mtime,
                    )

                # Commit after each file — preserves progress if conn drops
                self.conn.commit()
                indexed += 1
                print(f"  OK {rel_path} ({len(chunks)} chunks)", flush=True)

            except Exception as e:
                errors += 1
                print(f"  ERR {file_path}: {e}", flush=True)
                # Try to rollback and continue
                try:
                    self.conn.rollback()
                except Exception:
                    self._reconnect()

        print(
            f"\nIndexing complete: "
            f"{indexed} indexed, {skipped} skipped, {errors} errors",
            flush=True,
        )

    def _walk_codebase(self) -> Generator[Path, None, None]:
        clawignore = self._load_clawignore()
        for root, dirs, files in os.walk(self.codebase_path):
            dirs[:] = [
                d for d in dirs
                if d not in EXCLUDE_PATTERNS
                and not any(pattern in d for pattern in clawignore)
            ]
            for filename in files:
                filepath = Path(root) / filename
                if filepath.suffix in INCLUDE_EXTENSIONS:
                    yield filepath

    def _chunk_file(
        self, file_path: Path, content: str
    ) -> Generator[dict, None, None]:
        suffix = file_path.suffix
        if suffix == '.py':
            yield from self._chunk_python(content)
        elif suffix in {'.ts', '.tsx', '.js', '.jsx'}:
            yield from self._chunk_typescript(content)
        elif suffix == '.md':
            yield from self._chunk_markdown(content)
        else:
            yield from self._chunk_window(content)

    def _chunk_python(self, content: str) -> Generator[dict, None, None]:
        import ast
        try:
            tree = ast.parse(content)
            lines = content.splitlines()
            for node in ast.walk(tree):
                if isinstance(node, (
                    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef
                )):
                    start = node.lineno - 1
                    end = node.end_lineno
                    if end - start < 5:
                        continue
                    chunk_content = '\n'.join(lines[start:end])
                    node_type = type(node).__name__.lower()
                    if 'async' in node_type:
                        node_type = 'async_function'
                    elif 'functiondef' in node_type:
                        node_type = 'function'
                    # Large nodes (e.g. huge classes) get windowed instead
                    if len(chunk_content) > MAX_CHUNK_CHARS:
                        yield from self._chunk_window(chunk_content)
                    else:
                        yield {
                            'content': chunk_content,
                            'type': node_type,
                            'name': node.name,
                        }
        except SyntaxError:
            yield from self._chunk_window(content)

    def _chunk_markdown(self, content: str) -> Generator[dict, None, None]:
        import re
        sections = re.split(r'\n(?=## )', content)
        for section in sections:
            section = section.strip()
            if not section:
                continue
            name_match = re.match(r'## (.+)', section)
            name = name_match.group(1) if name_match else None
            # Long sections (e.g. huge README blocks) get windowed
            if len(section) > MAX_CHUNK_CHARS:
                yield from self._chunk_window(section)
            else:
                yield {'content': section, 'type': 'section', 'name': name}

    def _chunk_typescript(
        self, content: str
    ) -> Generator[dict, None, None]:
        import re
        pattern = re.compile(
            r'(?:export\s+)?(?:default\s+)?(?:async\s+)?'
            r'function\s+(\w+)[^{]*\{',
            re.MULTILINE
        )
        lines = content.splitlines()
        matches = list(pattern.finditer(content))

        if not matches:
            yield from self._chunk_window(content)
            return

        for i, match in enumerate(matches):
            start_line = content[:match.start()].count('\n')
            end_line = (
                content[:matches[i + 1].start()].count('\n')
                if i + 1 < len(matches)
                else len(lines)
            )
            chunk_lines = lines[start_line:end_line]
            if len(chunk_lines) >= 5:
                chunk_content = '\n'.join(chunk_lines)
                if len(chunk_content) > MAX_CHUNK_CHARS:
                    yield from self._chunk_window(chunk_content)
                else:
                    yield {
                        'content': chunk_content,
                        'type': 'function',
                        'name': match.group(1),
                    }

    def _chunk_window(
        self, content: str, window: int = 40, overlap: int = 8
    ) -> Generator[dict, None, None]:
        """
        40-line windows (down from 100) so dense CSS/HTML lines stay within
        MAX_CHUNK_CHARS even when minified. The embed() truncation catches
        any remaining outliers.
        """
        lines = content.splitlines()
        if not lines:
            return
        step = window - overlap
        for start in range(0, len(lines), step):
            chunk_lines = lines[start:start + window]
            if len(chunk_lines) < 5:
                break
            chunk = '\n'.join(chunk_lines)
            # Hard cap per window — truncated at embed() too, but be explicit
            if len(chunk) > MAX_CHUNK_CHARS:
                chunk = chunk[:MAX_CHUNK_CHARS]
            yield {'content': chunk, 'type': 'window', 'name': None}

    def _is_indexed(self, file_path: str, content_hash: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM claw_code_chunks
                WHERE project_id = %s AND file_path = %s
                  AND content_hash = %s
                LIMIT 1
            """, (self.project_id, file_path, content_hash))
            return cur.fetchone() is not None

    def _delete_file_chunks(self, file_path: str):
        with self.conn.cursor() as cur:
            cur.execute("""
                DELETE FROM claw_code_chunks
                WHERE project_id = %s AND file_path = %s
            """, (self.project_id, file_path))

    def _store_chunk(self, **kwargs):
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO claw_code_chunks
                    (project_id, file_path, chunk_content, chunk_type,
                     chunk_name, content_hash, embedding, last_modified,
                     subproject_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, to_timestamp(%s), %s)
            """, (
                self.project_id,
                kwargs['file_path'],
                kwargs['content'],
                kwargs['chunk_type'],
                kwargs['chunk_name'],
                kwargs['content_hash'],
                kwargs['embedding'],
                kwargs['last_modified'],
                kwargs.get('subproject_id'),
            ))

    def index_session(
        self,
        session_id: str,
        project_id: str,
        subproject_id: str | None,
        summary: str,
        messages: list[dict],
    ) -> None:
        """
        Index an archived session into pgvector.
        Creates one chunk per session using the summary as the primary
        text and key user messages as supporting context.
        """
        if not summary and not messages:
            return

        chunk_text = f"Session summary: {summary}\n\n"
        for msg in messages:
            if msg.get('role') == 'user':
                content = msg.get('content', '')[:200]
                chunk_text += f"User: {content}\n"

        if len(chunk_text) > MAX_CHUNK_CHARS:
            chunk_text = chunk_text[:MAX_CHUNK_CHARS]

        try:
            embedding = self.embed(chunk_text)
            file_path = f'session/{session_id}'

            # Remove any previous chunk for this session
            with self.conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM claw_code_chunks
                    WHERE project_id = %s AND file_path = %s
                """, (project_id, file_path))

            with self.conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO claw_code_chunks
                        (project_id, file_path, chunk_content, chunk_type,
                         chunk_name, content_hash, embedding, last_modified,
                         subproject_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s)
                """, (
                    project_id,
                    file_path,
                    chunk_text,
                    'session',
                    session_id[:200],
                    hashlib.sha256(chunk_text.encode()).hexdigest(),
                    embedding,
                    subproject_id,
                ))
            self.conn.commit()
        except Exception as exc:
            try:
                self.conn.rollback()
            except Exception:
                self._reconnect()
            print(f"[indexer] index_session failed for {session_id}: {exc}")

    def _load_clawignore(self) -> list[str]:
        clawignore_path = (
            Path('projects') / self.project_id / '.clawignore'
        )
        if clawignore_path.exists():
            return [
                line.strip()
                for line in clawignore_path.read_text().splitlines()
                if line.strip() and not line.startswith('#')
            ]
        return []
