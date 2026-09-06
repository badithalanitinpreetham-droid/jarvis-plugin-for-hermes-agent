"""Local durable store for Jarvis organisational experience and evolution state.

This store is deliberately separate from Hermes profile memory. It keeps the small,
structured evidence Jarvis needs to learn which workers, strategies and retrieval
patterns perform well. TencentDB remains the optional durable knowledge backend.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


class ExperienceStore:
    """SQLite-backed outcome, lesson and policy store with bounded JSON payloads."""

    def __init__(self, path: str = "~/.jarvis-memory/experience.db") -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA busy_timeout=5000;
                CREATE TABLE IF NOT EXISTS outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    profile_id TEXT,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    strategy TEXT,
                    bots TEXT,
                    deliverable TEXT,
                    quality REAL,
                    duration_seconds REAL,
                    evidence TEXT,
                    lessons TEXT,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS policies (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0,
                    successes INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_outcomes_goal ON outcomes(goal);
                CREATE INDEX IF NOT EXISTS idx_outcomes_status ON outcomes(status);
                """
            )
            self._conn.commit()

    @staticmethod
    def _clip(value: Any, limit: int) -> str:
        text = str(value or "")
        return text[:limit]

    def record_outcome(
        self,
        *,
        session_id: str = "",
        profile_id: str = "",
        goal: str,
        status: str,
        strategy: str = "",
        bots: Optional[Iterable[str]] = None,
        deliverable: str = "",
        quality: Optional[float] = None,
        duration_seconds: Optional[float] = None,
        evidence: Optional[Dict[str, Any]] = None,
        lessons: Optional[Iterable[str]] = None,
    ) -> int:
        payload = json.dumps(dict(evidence or {}), ensure_ascii=False)[:12000]
        bot_json = json.dumps([self._clip(x, 120) for x in (bots or [])])[:4000]
        lesson_json = json.dumps([self._clip(x, 800) for x in (lessons or [])])[:6000]
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO outcomes
                (session_id, profile_id, goal, status, strategy, bots, deliverable,
                 quality, duration_seconds, evidence, lessons, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self._clip(session_id, 200),
                    self._clip(profile_id, 200),
                    self._clip(goal, 4000),
                    self._clip(status, 80),
                    self._clip(strategy, 3000),
                    bot_json,
                    self._clip(deliverable, 3000),
                    quality,
                    duration_seconds,
                    payload,
                    lesson_json,
                    time.time(),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def recent(self, query: str = "", limit: int = 8) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 50))
        query = self._clip(query, 1000).strip()
        with self._lock:
            if query:
                rows = self._conn.execute(
                    """SELECT * FROM outcomes
                       WHERE goal LIKE ? OR strategy LIKE ? OR lessons LIKE ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (f"%{query}%", f"%{query}%", f"%{query}%", limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM outcomes ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [dict(row) for row in rows]

    def policy(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM policies WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None

    def record_policy_result(self, key: str, *, success: bool, value: str, confidence: float) -> Dict[str, Any]:
        current = self.policy(key) or {
            "successes": 0,
            "failures": 0,
            "version": 0,
        }
        successes = int(current.get("successes", 0)) + (1 if success else 0)
        failures = int(current.get("failures", 0)) + (0 if success else 1)
        confidence = max(0.0, min(1.0, float(confidence)))
        version = int(current.get("version", 0)) + 1
        with self._lock:
            self._conn.execute(
                """INSERT INTO policies(key,value,confidence,successes,failures,version,updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET
                  value=excluded.value, confidence=excluded.confidence,
                  successes=excluded.successes, failures=excluded.failures,
                  version=excluded.version, updated_at=excluded.updated_at""",
                (key, self._clip(value, 8000), confidence, successes, failures, version, time.time()),
            )
            self._conn.commit()
        return dict(self.policy(key) or {})

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = ["ExperienceStore"]
