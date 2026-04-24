"""
Alf-E Memory — SQLite persistent storage.

Stores conversations per-user with session grouping,
token/cost tracking, and context summaries for the cross-domain mesh.
"""

import os
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional


class Memory:
    """Persistent memory store for Alf-E conversations and context."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            # Use /data when running as HA add-on (SUPERVISOR_TOKEN is set),
            # otherwise fall back to local data/ directory.
            db_path = "/data/alfe_memory.db" if os.getenv("SUPERVISOR_TOKEN") else "data/alfe_memory.db"
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                user_id TEXT NOT NULL DEFAULT 'default',
                conversation_id TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                model_used TEXT,
                provider TEXT,
                tokens_input INTEGER DEFAULT 0,
                tokens_output INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS context (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                domain TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                source TEXT,
                expires_at TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                user_id TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT,
                result TEXT,
                details TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_messages_user
                ON messages(user_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_messages_conversation
                ON messages(conversation_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_context_domain_key
                ON context(domain, key);
        """)
        conn.commit()
        conn.close()

    # ── Messages ─────────────────────────────────────────────────────────

    def save_message(
        self,
        role: str,
        content: str,
        user_id: str = "default",
        conversation_id: str = None,
        model_used: str = None,
        provider: str = None,
        tokens_input: int = 0,
        tokens_output: int = 0,
        cost_usd: float = 0.0,
    ):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO messages
               (timestamp, user_id, conversation_id, role, content,
                model_used, provider, tokens_input, tokens_output, cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                user_id,
                conversation_id,
                role,
                content,
                model_used,
                provider,
                tokens_input,
                tokens_output,
                cost_usd,
            ),
        )
        conn.commit()
        conn.close()

    def load_messages(
        self,
        user_id: str = "default",
        conversation_id: str = None,
        limit: int = 100,
    ) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        if conversation_id:
            rows = conn.execute(
                """SELECT role, content FROM messages
                   WHERE user_id = ? AND conversation_id = ?
                   ORDER BY id DESC LIMIT ?""",
                (user_id, conversation_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT role, content FROM messages
                   WHERE user_id = ?
                   ORDER BY id DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        conn.close()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    def get_message_count(self, user_id: str = None) -> int:
        conn = sqlite3.connect(self.db_path)
        if user_id:
            count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
        else:
            count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.close()
        return count

    # ── Context Mesh ─────────────────────────────────────────────────────

    def set_context(
        self,
        domain: str,
        key: str,
        value: str,
        source: str = None,
        expires_at: str = None,
    ):
        """Store a cross-domain context entry (e.g. domain='energy', key='solar_trend', value='down_30pct')."""
        conn = sqlite3.connect(self.db_path)
        # Upsert: replace if domain+key exists
        conn.execute(
            """INSERT INTO context (timestamp, domain, key, value, source, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(domain, key) DO UPDATE SET
                   timestamp = excluded.timestamp,
                   value = excluded.value,
                   source = excluded.source""",
            (datetime.now().isoformat(), domain, key, value, source, expires_at),
        )
        conn.commit()
        conn.close()

    def get_context(self, domain: str = None) -> list[dict]:
        """Get context entries, optionally filtered by domain."""
        conn = sqlite3.connect(self.db_path)
        if domain:
            rows = conn.execute(
                "SELECT domain, key, value, source, timestamp FROM context WHERE domain = ? ORDER BY timestamp DESC",
                (domain,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT domain, key, value, source, timestamp FROM context ORDER BY timestamp DESC"
            ).fetchall()
        conn.close()
        return [
            {"domain": r[0], "key": r[1], "value": r[2], "source": r[3], "timestamp": r[4]}
            for r in rows
        ]

    # ── Audit Log ────────────────────────────────────────────────────────

    def log_action(
        self,
        user_id: str,
        action: str,
        target: str = None,
        result: str = None,
        details: str = None,
    ):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO audit_log (timestamp, user_id, action, target, result, details)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (datetime.now().isoformat(), user_id, action, target, result, details),
        )
        conn.commit()
        conn.close()

    def get_audit_log(self, limit: int = 100, user_id: str = None) -> list[dict]:
        """Return recent audit entries, most recent first."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        if user_id:
            rows = conn.execute(
                """SELECT timestamp, user_id, action, target, result, details
                   FROM audit_log WHERE user_id = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT timestamp, user_id, action, target, result, details
                   FROM audit_log ORDER BY timestamp DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── Memory Export (for Claude Code bridge) ───────────────────────────

    def export_for_claude_code(self) -> dict:
        """Export everything useful for a Claude Code session to inherit.

        Returns a structured dict covering:
          - context: all stored facts (domain/key/value)
          - recent_topics: summary of what was discussed in last 7 days
          - users: active users and their message counts
          - cost_30d: spend summary

        This is what /api/memory/export returns. Claude Code reads it at
        session start so it inherits Alf-E's knowledge of the household.
        """
        conn = sqlite3.connect(self.db_path)

        # All stored context facts
        context_rows = conn.execute(
            "SELECT domain, key, value, source, timestamp FROM context ORDER BY domain, key"
        ).fetchall()
        context = [
            {"domain": r[0], "key": r[1], "value": r[2], "source": r[3], "updated": r[4]}
            for r in context_rows
        ]

        # Recent conversation topics — last 50 user messages (7-day window)
        recent_rows = conn.execute(
            """SELECT user_id, content, timestamp FROM messages
               WHERE role = 'user'
               AND timestamp > datetime('now', '-7 days')
               ORDER BY timestamp DESC LIMIT 50"""
        ).fetchall()
        recent_topics = [
            {"user": r[0], "message": r[1][:200], "at": r[2][:16]}
            for r in recent_rows
        ]

        # Active users
        user_rows = conn.execute(
            """SELECT user_id, COUNT(*) as msg_count, MAX(timestamp) as last_seen
               FROM messages GROUP BY user_id ORDER BY last_seen DESC"""
        ).fetchall()
        users = [
            {"user_id": r[0], "messages": r[1], "last_seen": r[2][:16]}
            for r in user_rows
        ]

        # Cost summary
        cost_row = conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(cost_usd),0)
               FROM messages WHERE timestamp > datetime('now', '-30 days')"""
        ).fetchone()

        conn.close()

        return {
            "exported_at":    datetime.now().isoformat(),
            "context_facts":  context,
            "recent_topics":  recent_topics,
            "users":          users,
            "cost_30d_usd":   round(cost_row[1], 4),
            "messages_30d":   cost_row[0],
        }

    # ── Cost Tracking ────────────────────────────────────────────────────

    def get_cost_summary(self, days: int = 30) -> dict:
        """Get token/cost summary for the last N days."""
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            """SELECT
                   COUNT(*) as messages,
                   COALESCE(SUM(tokens_input), 0) as total_input,
                   COALESCE(SUM(tokens_output), 0) as total_output,
                   COALESCE(SUM(cost_usd), 0) as total_cost
               FROM messages
               WHERE timestamp > datetime('now', ?)""",
            (f"-{days} days",),
        ).fetchone()
        conn.close()
        return {
            "messages": row[0],
            "tokens_input": row[1],
            "tokens_output": row[2],
            "cost_usd": round(row[3], 4),
        }
