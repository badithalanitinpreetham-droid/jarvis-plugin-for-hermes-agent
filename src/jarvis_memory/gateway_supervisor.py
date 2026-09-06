"""Gateway lifecycle, health monitoring and recurring workflow supervision."""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from .config import CONFIG
from .tencent_memory import TencentMemoryClient

logger = logging.getLogger(__name__)


class GatewaySupervisor:
    WATCHDOG_INTERVAL = CONFIG.watchdog_interval
    SYNC_INTERVAL = CONFIG.sync_interval
    FAILURE_THRESHOLD = CONFIG.watchdog_failure_threshold
    PROFILE_TTL = CONFIG.profile_ttl

    def __init__(self, memory_client: TencentMemoryClient, start_cmd: Optional[list] = None,
                 auto_start: Optional[bool] = None, autonomous_executor=None, planner=None):
        self.memory = memory_client
        if start_cmd is None and CONFIG.gateway_start_cmd:
            start_cmd = shlex.split(CONFIG.gateway_start_cmd)
        self.start_cmd = start_cmd
        self.auto_start = bool(start_cmd) if auto_start is None else bool(auto_start and start_cmd)
        self.autonomous_executor = autonomous_executor
        self._planner = planner or getattr(autonomous_executor, "planner", None)
        self._proc: Optional[subprocess.Popen] = None
        self._we_own_process = False
        self._stop_event = threading.Event()
        self._consecutive_misses = 0
        self._profile_last_seen: Dict[str, float] = {}
        self._profiles_lock = threading.Lock()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None
        self._trigger_lock = threading.Lock()
        self._last_trigger_dispatch: Dict[int, float] = {}
        self._last_telemetry_trigger = 0.0

    def start(self) -> None:
        if self.auto_start and not self.memory.health():
            self._spawn()
        self._stop_event.clear()
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True, name="jarvis-gateway-watchdog")
        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True, name="jarvis-memory-sync")
        self._watchdog_thread.start()
        self._sync_thread.start()
        logger.info("GatewaySupervisor started (auto_start=%s)", self.auto_start)

    def stop(self) -> None:
        self._stop_event.set()
        for thread in (self._watchdog_thread, self._sync_thread):
            if thread:
                thread.join(timeout=5)
        if self._we_own_process and self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._we_own_process = False
        if self._planner and hasattr(self._planner, "close"):
            try:
                self._planner.close()
            except Exception:
                logger.debug("Planner cleanup failed", exc_info=True)

    def _spawn(self) -> None:
        if not self.start_cmd:
            return
        kwargs = {"start_new_session": True} if sys.platform != "win32" else {}
        cwd = CONFIG.gateway_cwd or os.environ.get("GATEWAY_CWD") or None
        logger.info("Spawning MemoryCore Gateway process")
        try:
            self._proc = subprocess.Popen(
                self.start_cmd,
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **kwargs,
            )
        except (OSError, ValueError) as exc:
            self._proc = None
            self._we_own_process = False
            logger.error("Unable to spawn MemoryCore Gateway: %s", exc)
            return
        self._we_own_process = True
        self._consecutive_misses = 0

    def _respawn(self) -> None:
        if not self._we_own_process:
            logger.warning("Gateway unhealthy; external process is not owned by Jarvis, so it will not be killed.")
            return
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._spawn()

    def _watchdog_loop(self) -> None:
        while not self._stop_event.wait(self.WATCHDOG_INTERVAL):
            healthy = self.memory.health()
            if healthy:
                if self._consecutive_misses:
                    logger.info("Gateway healthy again after %d miss(es).", self._consecutive_misses)
                self._consecutive_misses = 0
            else:
                self._consecutive_misses += 1
                if self._consecutive_misses >= self.FAILURE_THRESHOLD:
                    self._respawn()

            if self.autonomous_executor:
                try:
                    stalled = self.autonomous_executor.check_stalled_workflows()
                    for item in stalled:
                        logger.warning(
                            "Stalled workflow %s at step %s (%ss)",
                            item["workflow_id"],
                            (item.get("stalled_step") or {}).get("id", "?"),
                            item.get("elapsed_seconds", 0),
                        )
                except Exception:
                    logger.debug("Stall check failed", exc_info=True)

                try:
                    self._process_triggers()
                except Exception:
                    logger.exception("Scheduled-trigger evaluation failed")

                try:
                    self.autonomous_executor.store.cleanup_terminal(CONFIG.workflow_retention_days)
                except Exception:
                    logger.debug("Workflow retention cleanup failed", exc_info=True)

            self._safe_telemetry()

    def _process_triggers(self) -> None:
        store = self.autonomous_executor.store
        now = datetime.now(timezone.utc)
        for trigger in store.get_triggers():
            last = trigger.get("last_run")
            due = True
            if last:
                try:
                    last_dt = datetime.fromisoformat(last)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    due = (now - last_dt).total_seconds() >= int(trigger["interval_seconds"])
                except (TypeError, ValueError):
                    due = True
            if not due:
                continue
            with self._trigger_lock:
                last_dispatch = self._last_trigger_dispatch.get(trigger["id"], 0.0)
                if time.monotonic() - last_dispatch < min(60.0, float(trigger["interval_seconds"])):
                    continue
                self._last_trigger_dispatch[trigger["id"]] = time.monotonic()

            if self._planner is None:
                from .tools.planner import WorkflowPlanner
                self._planner = WorkflowPlanner(
                    memory_engine=self.autonomous_executor.memory_engine,
                    store=store,
                )
            plan = self._planner.create_plan(trigger["goal"], trigger["profile_id"])
            if plan.get("error"):
                logger.error("Trigger %s planning failed; last_run not advanced: %s", trigger["id"], plan["error"])
                continue
            wf_id = f"cron-{uuid.uuid4().hex}"
            result = self.autonomous_executor.start_workflow(wf_id, plan, trigger["profile_id"])
            if result.get("error"):
                logger.error("Trigger %s workflow dispatch failed; last_run not advanced: %s", trigger["id"], result)
                continue
            store.update_trigger_last_run(trigger["id"])
            logger.info("Scheduled trigger %s dispatched workflow %s", trigger["id"], wf_id)

    def _safe_telemetry(self) -> None:
        try:
            from .tools.os_assistant import OSAssistant
            telemetry = OSAssistant.get_telemetry()
            cpu, ram = telemetry.get("cpu_percent", 0), telemetry.get("ram_percent", 0)
            if cpu > 95 or ram > 95:
                logger.warning("System load high: CPU=%s%% RAM=%s%%", cpu, ram)
                if os.environ.get("JARVIS_ENABLE_AUTO_RECOVERY") != "1":
                    return
                if time.monotonic() - self._last_telemetry_trigger < 600:
                    return
                self._last_telemetry_trigger = time.monotonic()
                if self.autonomous_executor:
                    plan = {
                        "goal": "Diagnose system overload safely",
                        "steps": [{
                            "id": "diagnose-overload",
                            "action": "Read current CPU, RAM, disk and battery telemetry and identify the likely overload source.",
                            "tool": "jarvis_monitor_operative",
                            "parameters": {},
                            "confidence": 0.95,
                            "risk": "high",
                            "requires_approval": True,
                        }],
                        "estimated_steps": 1,
                        "success_criteria": "System load telemetry is captured and reviewed safely.",
                    }
                    self.autonomous_executor.start_workflow(
                        f"auto-recovery-{uuid.uuid4().hex}", plan, "system_admin_bot"
                    )
        except Exception:
            logger.debug("Telemetry check failed", exc_info=True)

    def _sync_loop(self) -> None:
        while not self._stop_event.wait(self.SYNC_INTERVAL):
            now = time.monotonic()
            with self._profiles_lock:
                to_sync = [pid for pid, seen in self._profile_last_seen.items() if now - seen > self.PROFILE_TTL]
                for pid in to_sync:
                    del self._profile_last_seen[pid]
            for profile_id in to_sync:
                try:
                    self.memory.session_end(profile_id)
                except Exception:
                    logger.debug("Background memory sync failed for %s", profile_id, exc_info=True)

    def note_profile_active(self, profile_id: str) -> None:
        with self._profiles_lock:
            self._profile_last_seen[str(profile_id)] = time.monotonic()

    def status(self) -> dict:
        with self._profiles_lock:
            count = len(self._profile_last_seen)
        return {
            "auto_start": self.auto_start,
            "owns_process": self._we_own_process,
            "process_alive": bool(self._proc and self._proc.poll() is None) if self._we_own_process else None,
            "consecutive_misses": self._consecutive_misses,
            "known_profiles": count,
            "planner_ready": self._planner is not None,
        }
