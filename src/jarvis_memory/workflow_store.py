"""SQLite persistence for workflows, schedules and tool health."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional


TERMINAL = {"completed", "completed_with_failures", "failed", "cancelled"}


class WorkflowStore:
    def __init__(self, db_path: str):
        resolved = os.path.expanduser(db_path)
        directory = os.path.dirname(resolved)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.db_path = resolved
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(resolved, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("""CREATE TABLE IF NOT EXISTS workflows(
                workflow_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,
                status TEXT NOT NULL, state_json TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            self._conn.execute("""CREATE TABLE IF NOT EXISTS dedupe_keys(
                dedupe_key TEXT PRIMARY KEY, workflow_id TEXT NOT NULL,
                step_id TEXT NOT NULL, completed_at TEXT NOT NULL)""")
            self._conn.execute("""CREATE TABLE IF NOT EXISTS triggers(
                goal TEXT NOT NULL, profile_id TEXT NOT NULL, interval_seconds INTEGER NOT NULL,
                last_run TEXT, created_at TEXT NOT NULL)""")
            self._conn.execute("DELETE FROM triggers WHERE interval_seconds <= 0")
            self._conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_triggers_unique
                ON triggers(goal, profile_id, interval_seconds)""")
            self._conn.execute("""CREATE TABLE IF NOT EXISTS broken_tools(
                tool_name TEXT PRIMARY KEY, reason TEXT, broken_at TEXT NOT NULL)""")
            self._conn.commit()

    def save(self, workflow_id: str, state: Dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute("""INSERT INTO workflows(workflow_id,profile_id,status,state_json,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(workflow_id) DO UPDATE SET
                profile_id=excluded.profile_id,status=excluded.status,state_json=excluded.state_json,
                updated_at=excluded.updated_at""",
                (workflow_id, state["profile_id"], state["status"], json.dumps(state), self._now()))
            self._conn.commit()

    def load(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT state_json FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def load_all(self, include_terminal: bool = True) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT workflow_id,state_json FROM workflows").fetchall()
        result: Dict[str, Dict[str, Any]] = {}
        for wid, raw in rows:
            try:
                state = json.loads(raw)
                if include_terminal or state.get("status") not in TERMINAL:
                    result[wid] = state
            except json.JSONDecodeError:
                continue
        return result

    def list_by_status(self, status: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT state_json FROM workflows WHERE status=?", (status,)).fetchall()
        return [json.loads(raw) for (raw,) in rows]

    def cleanup_terminal(self, retention_days: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days)))
        with self._lock:
            rows = self._conn.execute("SELECT workflow_id,updated_at,status FROM workflows").fetchall()
            doomed = []
            for wid, updated_at, status in rows:
                if status not in TERMINAL:
                    continue
                try:
                    ts = datetime.fromisoformat(updated_at)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts < cutoff:
                        doomed.append((wid,))
                except ValueError:
                    continue
            if doomed:
                self._conn.executemany("DELETE FROM workflows WHERE workflow_id=?", doomed)
                self._conn.commit()
            return len(doomed)

    def add_trigger(self, goal: str, profile_id: str, interval_seconds: int) -> int:
        goal, profile_id, interval_seconds = goal.strip(), profile_id.strip(), int(interval_seconds)
        if not goal or not profile_id or interval_seconds <= 0:
            raise ValueError("goal/profile_id are required and interval_seconds must be > 0")
        with self._lock:
            self._conn.execute("""INSERT INTO triggers(goal,profile_id,interval_seconds,created_at)
                VALUES(?,?,?,?) ON CONFLICT(goal,profile_id,interval_seconds) DO NOTHING""",
                (goal, profile_id, interval_seconds, self._now()))
            row = self._conn.execute("SELECT rowid FROM triggers WHERE goal=? AND profile_id=? AND interval_seconds=?",
                                     (goal, profile_id, interval_seconds)).fetchone()
            self._conn.commit()
            return int(row[0])

    def get_triggers(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT rowid,goal,profile_id,interval_seconds,last_run,created_at FROM triggers ORDER BY rowid").fetchall()
        return [{"id":r[0],"goal":r[1],"profile_id":r[2],"interval_seconds":r[3],"last_run":r[4],"created_at":r[5]} for r in rows]

    def update_trigger_last_run(self, trigger_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE triggers SET last_run=? WHERE rowid=?", (self._now(), int(trigger_id)))
            self._conn.commit()

    def delete_trigger(self, trigger_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM triggers WHERE rowid=?", (int(trigger_id),))
            self._conn.commit()

    def mark_tool_broken(self, tool_name: str, reason: str) -> None:
        with self._lock:
            self._conn.execute("""INSERT INTO broken_tools(tool_name,reason,broken_at) VALUES(?,?,?)
                ON CONFLICT(tool_name) DO UPDATE SET reason=excluded.reason,broken_at=excluded.broken_at""",
                (tool_name, reason, self._now()))
            self._conn.commit()

    def mark_tool_fixed(self, tool_name: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM broken_tools WHERE tool_name=?", (tool_name,))
            self._conn.commit()

    def get_broken_tools(self) -> List[str]:
        with self._lock:
            return [r[0] for r in self._conn.execute("SELECT tool_name FROM broken_tools ORDER BY tool_name")]

    def is_dedupe_done(self, dedupe_key: str) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM dedupe_keys WHERE dedupe_key=?", (dedupe_key,)).fetchone() is not None

    def mark_dedupe_done(self, dedupe_key: str, workflow_id: str, step_id: Any) -> None:
        with self._lock:
            self._conn.execute("""INSERT INTO dedupe_keys(dedupe_key,workflow_id,step_id,completed_at)
                VALUES(?,?,?,?) ON CONFLICT(dedupe_key) DO NOTHING""",
                (dedupe_key, workflow_id, str(step_id), self._now()))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
