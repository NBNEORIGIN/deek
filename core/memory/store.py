"""
Persistent memory for agent conversations and decisions.
SQLite per project — sovereign, no external dependency.

Stores:
    - Conversation history (context continuity across sessions)
    - Decision records (architectural choices + reasoning)
    - File edit history (what was changed and why)
    - Sessions (metadata, subproject association, token tracking)
    - Subprojects (per-project client/tenant isolation)
    - Archived sessions (moved out of active conversations when over token limit)
"""
import sqlite3
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


class MemoryStore:

    def __init__(self, project_id: str, data_dir: str = './data'):
        self.project_id = project_id
        db_path = Path(data_dir) / f'{project_id}.db'
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._ensure_schema()
        self._migrate()

    def _ensure_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                channel TEXT NOT NULL,
                model_used TEXT,
                tokens_used INTEGER,
                cost_usd REAL,
                timestamp TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                decision_type TEXT NOT NULL,
                description TEXT NOT NULL,
                reasoning TEXT,
                files_affected TEXT,
                timestamp TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS file_edits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                file_path TEXT NOT NULL,
                edit_type TEXT NOT NULL,
                diff_preview TEXT,
                reason TEXT,
                approved_by TEXT,
                timestamp TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS subprojects (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                name TEXT NOT NULL,
                display_name TEXT NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(project_id, name)
            );

            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                subproject_id TEXT,
                started_at TEXT NOT NULL,
                last_message_at TEXT NOT NULL,
                estimated_tokens INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS archived_sessions (
                session_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                subproject_id TEXT,
                started_at TEXT NOT NULL,
                last_message_at TEXT NOT NULL,
                message_count INTEGER NOT NULL,
                summary TEXT,
                raw_messages TEXT NOT NULL,
                archived_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_conv_session
                ON conversations(session_id);
            CREATE INDEX IF NOT EXISTS idx_decisions_type
                ON decisions(decision_type);
            CREATE INDEX IF NOT EXISTS idx_sessions_project
                ON sessions(project_id);
            CREATE INDEX IF NOT EXISTS idx_archived_project
                ON archived_sessions(project_id);
            CREATE INDEX IF NOT EXISTS idx_archived_subproject
                ON archived_sessions(subproject_id);
        """)
        self.conn.commit()

    def _migrate(self):
        """Defensive column additions — safe to run on existing databases."""
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(sessions)")]
        if 'subproject_id' not in cols:
            self.conn.execute("ALTER TABLE sessions ADD COLUMN subproject_id TEXT")
        if 'estimated_tokens' not in cols:
            self.conn.execute(
                "ALTER TABLE sessions ADD COLUMN estimated_tokens INTEGER DEFAULT 0"
            )
        if 'active_skills_json' not in cols:
            self.conn.execute(
                "ALTER TABLE sessions ADD COLUMN active_skills_json TEXT DEFAULT '[]'"
            )

        # Cairn Protocol spec fields for decisions table
        dcols = [r[1] for r in self.conn.execute("PRAGMA table_info(decisions)")]
        if 'project' not in dcols:
            self.conn.execute("ALTER TABLE decisions ADD COLUMN project TEXT DEFAULT ''")
        if 'query' not in dcols:
            self.conn.execute("ALTER TABLE decisions ADD COLUMN query TEXT DEFAULT ''")
        if 'rejected' not in dcols:
            self.conn.execute("ALTER TABLE decisions ADD COLUMN rejected TEXT DEFAULT ''")
        if 'model_used' not in dcols:
            self.conn.execute("ALTER TABLE decisions ADD COLUMN model_used TEXT DEFAULT ''")

        self.conn.commit()

    # ─── Core message storage ──────────────────────────────────────────────────

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        channel: str,
        model_used: str = '',
        tokens_used: int = 0,
        cost_usd: float = 0.0,
        subproject_id: str | None = None,
    ):
        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        self.conn.execute("""
            INSERT INTO conversations
                (session_id, role, content, channel,
                 model_used, tokens_used, cost_usd, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (session_id, role, content, channel,
              model_used, tokens_used, cost_usd, now))
        # Keep sessions table in sync
        self.conn.execute("""
            INSERT INTO sessions
                (session_id, project_id, subproject_id, started_at, last_message_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                last_message_at = excluded.last_message_at,
                subproject_id = COALESCE(excluded.subproject_id, sessions.subproject_id)
        """, (session_id, self.project_id, subproject_id, now, now))
        self.conn.commit()

    def get_recent_history(
        self, session_id: str, limit: int = 20
    ) -> list[dict]:
        rows = self.conn.execute("""
            SELECT role, content, timestamp
            FROM conversations
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (session_id, limit)).fetchall()

        return [
            {'role': r[0], 'content': r[1], 'timestamp': r[2]}
            for r in reversed(rows)
        ]

    def record_decision(
        self,
        session_id: str,
        decision_type: str,
        description: str,
        reasoning: str = '',
        files_affected: list | None = None,
        project: str = '',
        query: str = '',
        rejected: str = '',
        model_used: str = '',
    ):
        """
        Record an architectural or implementation decision.
        These become part of the project's institutional memory.
        """
        self.conn.execute("""
            INSERT INTO decisions
                (session_id, decision_type, description,
                 reasoning, files_affected,
                 project, query, rejected, model_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id, decision_type, description, reasoning,
            json.dumps(files_affected or []),
            project, query, rejected, model_used,
        ))
        self.conn.commit()

    def record_file_edit(
        self,
        session_id: str,
        file_path: str,
        edit_type: str,
        diff_preview: str = '',
        reason: str = '',
        approved_by: str = 'user',
    ):
        self.conn.execute("""
            INSERT INTO file_edits
                (session_id, file_path, edit_type,
                 diff_preview, reason, approved_by)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_id, file_path, edit_type,
              diff_preview, reason, approved_by))
        self.conn.commit()

    def get_decision(self, decision_id: int) -> dict | None:
        """Get a single decision by ID."""
        row = self.conn.execute("""
            SELECT id, decision_type, description, reasoning,
                   files_affected, timestamp,
                   project, query, rejected, model_used
            FROM decisions WHERE id = ?
        """, (decision_id,)).fetchone()
        if not row:
            return None
        return {
            'id': row[0], 'type': row[1], 'description': row[2],
            'reasoning': row[3], 'files': json.loads(row[4] or '[]'),
            'timestamp': row[5], 'project': row[6] if len(row) > 6 else '',
            'query': row[7] if len(row) > 7 else '',
            'rejected': row[8] if len(row) > 8 else '',
            'model_used': row[9] if len(row) > 9 else '',
        }

    def update_decision(
        self,
        decision_id: int,
        description: str | None = None,
        reasoning: str | None = None,
        query: str | None = None,
        rejected: str | None = None,
        decision_type: str | None = None,
        files_affected: list | None = None,
    ) -> bool:
        """Update an existing decision. Only provided fields are changed."""
        updates = []
        params = []
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if reasoning is not None:
            updates.append("reasoning = ?")
            params.append(reasoning)
        if query is not None:
            updates.append("query = ?")
            params.append(query)
        if rejected is not None:
            updates.append("rejected = ?")
            params.append(rejected)
        if decision_type is not None:
            updates.append("decision_type = ?")
            params.append(decision_type)
        if files_affected is not None:
            updates.append("files_affected = ?")
            params.append(json.dumps(files_affected))
        if not updates:
            return False
        params.append(decision_id)
        self.conn.execute(
            f"UPDATE decisions SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.conn.commit()
        return self.conn.total_changes > 0

    def delete_decision(self, decision_id: int) -> bool:
        """Delete a decision by ID."""
        self.conn.execute("DELETE FROM decisions WHERE id = ?", (decision_id,))
        self.conn.commit()
        return self.conn.total_changes > 0

    def list_decisions(
        self,
        limit: int = 50,
        offset: int = 0,
        query_filter: str = '',
    ) -> tuple[list[dict], int]:
        """List decisions with pagination. Returns (entries, total_count)."""
        if query_filter:
            where = "WHERE description LIKE ? OR query LIKE ? OR reasoning LIKE ?"
            params = [f'%{query_filter}%'] * 3
            count = self.conn.execute(
                f"SELECT COUNT(*) FROM decisions {where}", params
            ).fetchone()[0]
            rows = self.conn.execute(f"""
                SELECT id, decision_type, description, reasoning,
                       files_affected, timestamp,
                       project, query, rejected, model_used
                FROM decisions {where}
                ORDER BY timestamp DESC LIMIT ? OFFSET ?
            """, params + [limit, offset]).fetchall()
        else:
            count = self.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            rows = self.conn.execute("""
                SELECT id, decision_type, description, reasoning,
                       files_affected, timestamp,
                       project, query, rejected, model_used
                FROM decisions ORDER BY timestamp DESC LIMIT ? OFFSET ?
            """, (limit, offset)).fetchall()

        entries = [
            {
                'id': r[0], 'type': r[1], 'description': r[2],
                'reasoning': r[3], 'files': json.loads(r[4] or '[]'),
                'timestamp': r[5], 'project': r[6] if len(r) > 6 else '',
                'query': r[7] if len(r) > 7 else '',
                'rejected': r[8] if len(r) > 8 else '',
                'model_used': r[9] if len(r) > 9 else '',
            }
            for r in rows
        ]
        return entries, count

    def search_decisions(self, query: str) -> list[dict]:
        """Search past decisions by keyword — agent recall mechanism."""
        rows = self.conn.execute("""
            SELECT decision_type, description, reasoning,
                   files_affected, timestamp,
                   project, query, rejected, model_used
            FROM decisions
            WHERE description LIKE ?
               OR reasoning LIKE ?
               OR query LIKE ?
               OR rejected LIKE ?
            ORDER BY timestamp DESC
            LIMIT 10
        """, (f'%{query}%', f'%{query}%',
              f'%{query}%', f'%{query}%')).fetchall()

        return [
            {
                'type': r[0], 'description': r[1],
                'reasoning': r[2],
                'files': json.loads(r[3] or '[]'),
                'timestamp': r[4],
                'project': r[5] if len(r) > 5 else '',
                'query': r[6] if len(r) > 6 else '',
                'rejected': r[7] if len(r) > 7 else '',
                'model_used': r[8] if len(r) > 8 else '',
            }
            for r in rows
        ]

    def close(self):
        """Close the SQLite connection. Call when done (especially on Windows)."""
        self.conn.close()

    def _compact_session_text(
        self,
        text: str | None,
        fallback: str = 'New chat',
        max_len: int = 72,
    ) -> str:
        compact = ' '.join((text or '').split())
        if not compact:
            return fallback
        if len(compact) <= max_len:
            return compact
        return compact[: max_len - 1].rstrip() + '…'

    def get_cost_summary(self) -> list[dict]:
        rows = self.conn.execute("""
            SELECT
                model_used,
                COUNT(*) as calls,
                SUM(tokens_used) as total_tokens,
                SUM(cost_usd) as total_cost
            FROM conversations
            WHERE role = 'assistant' AND model_used != ''
            GROUP BY model_used
            ORDER BY total_cost DESC
        """).fetchall()
        return [
            {
                'model': r[0], 'calls': r[1],
                'tokens': r[2], 'cost_usd': round(r[3] or 0, 4),
            }
            for r in rows
        ]

    def get_spend_since(self, since_iso: str) -> list[dict]:
        """
        Cost breakdown since a given UTC timestamp (ISO 8601).
        Groups by model_used so the caller can map to provider.
        Used by GET /cost/today for cross-project aggregation.
        """
        rows = self.conn.execute("""
            SELECT
                model_used,
                COUNT(*)         AS calls,
                SUM(tokens_used) AS total_tokens,
                SUM(cost_usd)    AS total_cost
            FROM conversations
            WHERE role = 'assistant'
              AND model_used != ''
              AND timestamp >= ?
            GROUP BY model_used
            ORDER BY total_cost DESC
        """, (since_iso,)).fetchall()
        return [
            {
                'model': r[0],
                'calls': r[1],
                'tokens': r[2],
                'cost_usd': round(r[3] or 0, 6),
            }
            for r in rows
        ]

    # ─── Subproject management ─────────────────────────────────────────────────

    def create_subproject(
        self,
        project_id: str,
        name: str,
        display_name: str,
        description: str = '',
    ) -> dict:
        """Create a subproject. Idempotent — silently succeeds if already exists."""
        sp_id = f'{project_id}:{name}'
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute("""
            INSERT OR IGNORE INTO subprojects
                (id, project_id, name, display_name, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (sp_id, project_id, name, display_name, description, now))
        self.conn.commit()
        return self.get_subproject_by_name(project_id, name)  # type: ignore[return-value]

    def get_subprojects(self, project_id: str) -> list[dict]:
        """Return all subprojects for a project ordered by name."""
        rows = self.conn.execute("""
            SELECT id, project_id, name, display_name, description, created_at
            FROM subprojects
            WHERE project_id = ?
            ORDER BY name
        """, (project_id,)).fetchall()
        return [
            {
                'id': r[0], 'project_id': r[1], 'name': r[2],
                'display_name': r[3], 'description': r[4], 'created_at': r[5],
            }
            for r in rows
        ]

    def get_subproject_by_name(self, project_id: str, name: str) -> dict | None:
        """Return a single subproject by name."""
        row = self.conn.execute("""
            SELECT id, project_id, name, display_name, description, created_at
            FROM subprojects
            WHERE project_id = ? AND name = ?
        """, (project_id, name)).fetchone()
        if not row:
            return None
        return {
            'id': row[0], 'project_id': row[1], 'name': row[2],
            'display_name': row[3], 'description': row[4], 'created_at': row[5],
        }

    def set_session_subproject(
        self, session_id: str, subproject_id: str | None
    ) -> None:
        """Associate a session with a subproject."""
        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        self.conn.execute("""
            INSERT INTO sessions
                (session_id, project_id, subproject_id, started_at, last_message_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                subproject_id = excluded.subproject_id,
                last_message_at = excluded.last_message_at
        """, (session_id, self.project_id, subproject_id, now, now))
        self.conn.commit()

    def set_session_skills(
        self,
        session_id: str,
        skill_ids: list[str],
    ) -> None:
        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        self.conn.execute("""
            INSERT INTO sessions
                (session_id, project_id, started_at, last_message_at, active_skills_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                active_skills_json = excluded.active_skills_json,
                last_message_at = excluded.last_message_at
        """, (
            session_id,
            self.project_id,
            now,
            now,
            json.dumps(skill_ids or []),
        ))
        self.conn.commit()

    def get_session_skills(self, session_id: str) -> list[str]:
        row = self.conn.execute("""
            SELECT active_skills_json
            FROM sessions
            WHERE session_id = ?
        """, (session_id,)).fetchone()
        if not row or not row[0]:
            return []
        try:
            return [str(item) for item in json.loads(row[0])]
        except Exception:
            return []

    # ─── Session listing and retrieval ────────────────────────────────────────

    def get_session_list(
        self,
        project_id: str,
        subproject_id: str | None = None,
    ) -> list[dict]:
        """
        Return sessions for a project ordered by last_message_at desc.
        If subproject_id provided, scope to that subproject only.
        Includes archived sessions marked with archived=True.
        """
        if subproject_id:
            active_rows = self.conn.execute("""
                SELECT c.session_id,
                       MIN(c.timestamp) AS started_at,
                       MAX(c.timestamp) AS last_message_at,
                       COUNT(*) AS message_count,
                       s.subproject_id,
                       0 AS archived,
                       (
                           SELECT content
                           FROM conversations c2
                           WHERE c2.session_id = c.session_id
                             AND c2.role = 'user'
                           ORDER BY c2.id ASC
                           LIMIT 1
                       ) AS first_user_message,
                       (
                           SELECT content
                           FROM conversations c3
                           WHERE c3.session_id = c.session_id
                           ORDER BY c3.id DESC
                           LIMIT 1
                       ) AS last_message_preview
                FROM conversations c
                INNER JOIN sessions s ON c.session_id = s.session_id
                WHERE s.project_id = ? AND s.subproject_id = ?
                GROUP BY c.session_id
            """, (project_id, subproject_id)).fetchall()

            archived_rows = self.conn.execute("""
                SELECT session_id, started_at, last_message_at,
                       message_count, subproject_id, 1, summary, raw_messages
                FROM archived_sessions
                WHERE project_id = ? AND subproject_id = ?
            """, (project_id, subproject_id)).fetchall()
        else:
            active_rows = self.conn.execute("""
                SELECT c.session_id,
                       MIN(c.timestamp) AS started_at,
                       MAX(c.timestamp) AS last_message_at,
                       COUNT(*) AS message_count,
                       s.subproject_id,
                       0 AS archived,
                       (
                           SELECT content
                           FROM conversations c2
                           WHERE c2.session_id = c.session_id
                             AND c2.role = 'user'
                           ORDER BY c2.id ASC
                           LIMIT 1
                       ) AS first_user_message,
                       (
                           SELECT content
                           FROM conversations c3
                           WHERE c3.session_id = c.session_id
                           ORDER BY c3.id DESC
                           LIMIT 1
                       ) AS last_message_preview
                FROM conversations c
                INNER JOIN sessions s ON c.session_id = s.session_id
                WHERE s.project_id = ?
                GROUP BY c.session_id
            """, (project_id,)).fetchall()

            archived_rows = self.conn.execute("""
                SELECT session_id, started_at, last_message_at,
                       message_count, subproject_id, 1, summary, raw_messages
                FROM archived_sessions
                WHERE project_id = ?
            """, (project_id,)).fetchall()

        def _row_to_dict(r) -> dict:
            if bool(r[5]):
                summary = r[6] or ''
                raw_messages = json.loads(r[7] or '[]')
                first_user = next(
                    (m.get('content', '') for m in raw_messages if m.get('role') == 'user'),
                    '',
                )
                last_preview = raw_messages[-1].get('content', '') if raw_messages else summary
            else:
                first_user = r[6] or ''
                last_preview = r[7] or ''

            return {
                'session_id': r[0],
                'started_at': r[1],
                'last_message_at': r[2],
                'message_count': r[3],
                'subproject_id': r[4],
                'archived': bool(r[5]),
                'title': self._compact_session_text(first_user),
                'preview': self._compact_session_text(
                    last_preview,
                    fallback=self._compact_session_text(first_user),
                    max_len=96,
                ),
            }

        all_sessions = [_row_to_dict(r) for r in active_rows + archived_rows]
        all_sessions.sort(key=lambda s: s['last_message_at'] or '', reverse=True)
        return all_sessions

    def get_session(
        self,
        session_id: str,
        project_id: str | None = None,
        subproject_id: str | None = None,
    ) -> dict | None:
        """Return full session including messages. Checks active and archived tables."""
        active_query = """
            SELECT c.role, c.content, c.model_used, c.tokens_used, c.cost_usd,
                   c.timestamp, s.subproject_id, s.estimated_tokens
                   , s.active_skills_json
            FROM conversations c
            INNER JOIN sessions s ON c.session_id = s.session_id
            WHERE c.session_id = ?
        """
        params: list = [session_id]
        if project_id:
            active_query += " AND s.project_id = ?"
            params.append(project_id)
        if subproject_id is not None:
            active_query += " AND s.subproject_id = ?"
            params.append(subproject_id)
        active_query += " ORDER BY c.id"
        rows = self.conn.execute(active_query, params).fetchall()

        if rows:
            return {
                'session_id': session_id,
                'subproject_id': rows[0][6],
                'estimated_tokens': rows[0][7],
                'skill_ids': json.loads(rows[0][8] or '[]'),
                'archived': False,
                'messages': [
                    {
                        'role': r[0], 'content': r[1], 'model_used': r[2],
                        'tokens_used': r[3], 'cost_usd': r[4], 'timestamp': r[5],
                    }
                    for r in rows
                ],
            }

        archived_query = """
            SELECT session_id, project_id, subproject_id, started_at,
                   last_message_at, message_count, summary, raw_messages
            FROM archived_sessions
            WHERE session_id = ?
        """
        archived_params: list = [session_id]
        if project_id:
            archived_query += " AND project_id = ?"
            archived_params.append(project_id)
        if subproject_id is not None:
            archived_query += " AND subproject_id = ?"
            archived_params.append(subproject_id)
        row = self.conn.execute(archived_query, archived_params).fetchone()

        if row:
            return {
                'session_id': row[0],
                'project_id': row[1],
                'subproject_id': row[2],
                'started_at': row[3],
                'last_message_at': row[4],
                'message_count': row[5],
                'summary': row[6],
                'archived': True,
                'messages': json.loads(row[7] or '[]'),
            }

        return None

    # ─── Token tracking and archiving ─────────────────────────────────────────

    def estimate_tokens(self, session_id: str) -> int:
        """Estimate token count using word count × 1.3. No tokenizer needed."""
        rows = self.conn.execute("""
            SELECT content FROM conversations WHERE session_id = ?
        """, (session_id,)).fetchall()
        total_words = sum(len(r[0].split()) for r in rows)
        return int(total_words * 1.3)

    def should_trim(self, session_id: str) -> bool:
        """Return True if estimated tokens > 40,000."""
        return self.estimate_tokens(session_id) > 40_000

    def should_archive(self, session_id: str) -> bool:
        """Return True if estimated tokens > 50,000."""
        return self.estimate_tokens(session_id) > 50_000

    def trim_session(self, session_id: str) -> int:
        """
        Remove oldest non-system messages until under 40,000 tokens.
        Returns number of messages removed.
        """
        removed = 0
        while self.estimate_tokens(session_id) > 40_000:
            row = self.conn.execute("""
                SELECT id FROM conversations
                WHERE session_id = ? AND role != 'system'
                ORDER BY id ASC
                LIMIT 1
            """, (session_id,)).fetchone()
            if not row:
                break
            self.conn.execute("DELETE FROM conversations WHERE id = ?", (row[0],))
            self.conn.commit()
            removed += 1
        return removed

    def archive_session(self, session_id: str, summary: str) -> None:
        """Move session to archived_sessions table. Remove from active tables."""
        rows = self.conn.execute("""
            SELECT role, content, model_used, tokens_used, cost_usd, timestamp
            FROM conversations
            WHERE session_id = ?
            ORDER BY id
        """, (session_id,)).fetchall()

        if not rows:
            return

        messages = [
            {
                'role': r[0], 'content': r[1], 'model_used': r[2],
                'tokens_used': r[3], 'cost_usd': r[4], 'timestamp': r[5],
            }
            for r in rows
        ]

        meta = self.conn.execute("""
            SELECT subproject_id FROM sessions WHERE session_id = ?
        """, (session_id,)).fetchone()
        subproject_id = meta[0] if meta else None

        started_at = messages[0]['timestamp'] or ''
        last_at = messages[-1]['timestamp'] or ''
        now = datetime.now(timezone.utc).isoformat()

        self.conn.execute("""
            INSERT OR REPLACE INTO archived_sessions
                (session_id, project_id, subproject_id, started_at,
                 last_message_at, message_count, summary, raw_messages, archived_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id, self.project_id, subproject_id,
            started_at, last_at, len(messages),
            summary, json.dumps(messages), now,
        ))

        self.conn.execute(
            "DELETE FROM conversations WHERE session_id = ?", (session_id,)
        )
        self.conn.execute(
            "DELETE FROM sessions WHERE session_id = ?", (session_id,)
        )
        self.conn.commit()
