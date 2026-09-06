"""
Persistence for workflow state — the gap flagged repeatedly: `active_workflows`
as a bare in-memory dict loses everything if the jarvis-memory process is
killed and restarted mid-workflow (which "continuous, cron-driven" makes
routine, not exceptional). SQLite via stdlib, no new dependency.

Also owns the dedupe-key table used for idempotency: if a step carries a
`dedupe_key` (e.g. a content slug that's about to be published), once it's
been reported successful once, get_next_step() will auto-skip it forever
after — protects against a crash-and-restart replaying a publish step that
already went through.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional


class WorkflowStore:
    def __init__(self, db_path: str):
        resolved = os.path.expanduser(db_path)
        directory = os.path.dirname(resolved)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.db_path = resolved
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(resolved, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY,
                    profile_id  TEXT NOT NULL,
                    status      TEXT NOT NULL,
                    state_json  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS dedupe_keys (
                    dedupe_key   TEXT PRIMARY KEY,
                    workflow_id  TEXT NOT NULL,
                    step_id      INTEGER NOT NULL,
                    completed_at TEXT NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS triggers (
                    goal TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL,
                    last_run TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS broken_tools (
                    tool_name TEXT PRIMARY KEY,
                    reason TEXT,
                    broken_at TEXT NOT NULL
                )
            """)
            self._conn.commit()

    # --- workflow state ---------------------------------------------------

    def save(self, workflow_id: str, state: Dict[str, Any]):
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO workflows (workflow_id, profile_id, status, state_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workflow_id) DO UPDATE SET
                    status=excluded.status,
                    state_json=excluded.state_json,
                    updated_at=excluded.updated_at
                """,
                (workflow_id, state["profile_id"], state["status"], json.dumps(state), datetime.now().isoformat()),
            )
            self._conn.commit()

    def load(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT state_json FROM workflows WHERE workflow_id = ?", (workflow_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def load_all(self) -> Dict[str, Dict[str, Any]]:
        """Used on startup to warm the in-memory cache so an in-flight
        workflow survives a process restart."""
        with self._lock:
            rows = self._conn.execute("SELECT workflow_id, state_json FROM workflows").fetchall()
        return {wid: json.loads(state_json) for wid, state_json in rows}

    def list_by_status(self, status: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT state_json FROM workflows WHERE status = ?", (status,)
            ).fetchall()
        return [json.loads(s) for (s,) in rows]

    def delete(self, workflow_id: str):
        with self._lock:
            self._conn.execute("DELETE FROM workflows WHERE workflow_id = ?", (workflow_id,))
            self._conn.commit()

    # --- proactivity triggers ----------------------------------------------

    def add_trigger(self, goal: str, profile_id: str, interval_seconds: int):
        with self._lock:
            self._conn.execute(
                "INSERT INTO triggers (goal, profile_id, interval_seconds, created_at) VALUES (?, ?, ?, ?)",
                (goal, profile_id, interval_seconds, datetime.now().isoformat()),
            )
            self._conn.commit()

    def get_triggers(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT rowid, goal, profile_id, interval_seconds, last_run, created_at FROM triggers"
            ).fetchall()
        return [
            {
                "id": r[0],
                "goal": r[1],
                "profile_id": r[2],
                "interval_seconds": r[3],
                "last_run": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]

    def update_trigger_last_run(self, trigger_id: int):
        with self._lock:
            self._conn.execute(
                "UPDATE triggers SET last_run = ? WHERE rowid = ?",
                (datetime.now().isoformat(), trigger_id)
            )
            self._conn.commit()

    def delete_trigger(self, trigger_id: int):
        with self._lock:
            self._conn.execute("DELETE FROM triggers WHERE rowid = ?", (trigger_id,))
            self._conn.commit()

    # --- tool repair (broken tools tracking) ------------------------------

    def mark_tool_broken(self, tool_name: str, reason: str):
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO broken_tools (tool_name, reason, broken_at)
                VALUES (?, ?, ?)
                ON CONFLICT(tool_name) DO UPDATE SET
                    reason=excluded.reason,
                    broken_at=excluded.broken_at
                """,
                (tool_name, reason, datetime.now().isoformat()),
            )
            self._conn.commit()

    def mark_tool_fixed(self, tool_name: str):
        with self._lock:
            self._conn.execute("DELETE FROM broken_tools WHERE tool_name = ?", (tool_name,))
            self._conn.commit()

    def get_broken_tools(self) -> List[str]:
        with self._lock:
            rows = self._conn.execute("SELECT tool_name FROM broken_tools").fetchall()
        return [r[0] for r in rows]

    # --- dedupe / idempotency ----------------------------------------------

    def is_dedupe_done(self, dedupe_key: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM dedupe_keys WHERE dedupe_key = ?", (dedupe_key,)
            ).fetchone()
        return row is not None

    def mark_dedupe_done(self, dedupe_key: str, workflow_id: str, step_id: int):
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO dedupe_keys (dedupe_key, workflow_id, step_id, completed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(dedupe_key) DO NOTHING
                """,
                (dedupe_key, workflow_id, step_id, datetime.now().isoformat()),
            )
            self._conn.commit()

    def close(self):
        self._conn.close()
