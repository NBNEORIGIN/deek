import os
import json
from pathlib import Path
from typing import Callable


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
    SIMILARITY_THRESHOLD = 0.75
    MAX_TIER2_CHUNKS = 12

    def __init__(self, project_id: str, db_url: str):
        self.project_id = project_id
        self.db_url = db_url
        self.project_dir = Path('projects') / project_id
        self.core_md_path = self.project_dir / 'core.md'
        self._conn = None

    def _get_connection(self):
        if not self._conn or self._conn.closed:
            import psycopg2
            from pgvector.psycopg2 import register_vector
            self._conn = psycopg2.connect(self.db_url)
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

    def retrieve_tier2(self, task: str, embedding_fn: Callable) -> list[dict]:
        """
        Retrieve relevant code chunks via pgvector similarity search.
        Returns list of {'file': str, 'content': str, 'score': float}
        Falls back to empty list if pgvector unavailable.
        """
        try:
            task_embedding = embedding_fn(task)
            conn = self._get_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        file_path,
                        chunk_content,
                        chunk_type,
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
                    self.MAX_TIER2_CHUNKS,
                ))
                rows = cur.fetchall()

            return [
                {
                    'file': row[0],
                    'content': row[1],
                    'chunk_type': row[2],
                    'score': float(row[3]),
                }
                for row in rows
            ]
        except Exception as e:
            # Tier 2 is best-effort — if pgvector not ready, skip it
            print(f"Tier 2 retrieval unavailable: {e}")
            return []

    def load_tier3(self, file_path: str) -> str:
        """
        Load a complete file on demand.
        Enforces project root boundary — cannot escape to parent dirs.
        """
        config = self._load_config()
        project_root = Path(config.get('codebase_path', '.')).resolve()
        target = (project_root / file_path).resolve()

        if not str(target).startswith(str(project_root)):
            raise PermissionError(
                f"File '{file_path}' is outside the project root. "
                f"Agents cannot access files outside their project."
            )

        if not target.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        return target.read_text(encoding='utf-8', errors='replace')

    def build_context_prompt(self, task: str, embedding_fn: Callable) -> str:
        """Assemble full context string from all available tiers."""
        parts = []

        parts.append("# PROJECT CONTEXT\n")
        parts.append(self.load_tier1())
        parts.append("\n\n")

        chunks = self.retrieve_tier2(task, embedding_fn)
        if chunks:
            parts.append("# RELEVANT CODE\n")
            parts.append(
                f"# (Retrieved {len(chunks)} relevant sections "
                f"from codebase index)\n\n"
            )
            for chunk in chunks:
                parts.append(
                    f"## {chunk['file']} "
                    f"[similarity: {chunk['score']:.2f}]\n"
                )
                parts.append(f"```\n{chunk['content']}\n```\n\n")

        return ''.join(parts)

    def _load_config(self) -> dict:
        config_path = self.project_dir / 'config.json'
        if config_path.exists():
            return json.loads(config_path.read_text())
        return {}
