"""
Persistent memory for agent conversations and decisions.
SQLite per project — sovereign, no external dependency.

Stores:
    - Conversation history (context continuity across sessions)
    - Decision records (architectural choices + reasoning)
    - File edit history (what was changed and why)
"""
import sqlite3
import json
from pathlib import Path


class MemoryStore:

    def __init__(self, project_id: str, data_dir: str = './data'):
        self.project_id = project_id
        db_path = Path(data_dir) / f'{project_id}.db'
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._ensure_schema()

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

            CREATE INDEX IF NOT EXISTS idx_conv_session
                ON conversations(session_id);
            CREATE INDEX IF NOT EXISTS idx_decisions_type
                ON decisions(decision_type);
        """)
        self.conn.commit()

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        channel: str,
        model_used: str = '',
        tokens_used: int = 0,
        cost_usd: float = 0.0,
    ):
        self.conn.execute("""
            INSERT INTO conversations
                (session_id, role, content, channel,
                 model_used, tokens_used, cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (session_id, role, content, channel,
              model_used, tokens_used, cost_usd))
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
    ):
        """
        Record an architectural or implementation decision.
        These become part of the project's institutional memory.
        """
        self.conn.execute("""
            INSERT INTO decisions
                (session_id, decision_type, description,
                 reasoning, files_affected)
            VALUES (?, ?, ?, ?, ?)
        """, (
            session_id, decision_type, description, reasoning,
            json.dumps(files_affected or []),
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

    def search_decisions(self, query: str) -> list[dict]:
        """Search past decisions by keyword — agent recall mechanism."""
        rows = self.conn.execute("""
            SELECT decision_type, description, reasoning,
                   files_affected, timestamp
            FROM decisions
            WHERE description LIKE ?
               OR reasoning LIKE ?
            ORDER BY timestamp DESC
            LIMIT 10
        """, (f'%{query}%', f'%{query}%')).fetchall()

        return [
            {
                'type': r[0], 'description': r[1],
                'reasoning': r[2],
                'files': json.loads(r[3] or '[]'),
                'timestamp': r[4],
            }
            for r in rows
        ]

    def close(self):
        """Close the SQLite connection. Call when done (especially on Windows)."""
        self.conn.close()

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
