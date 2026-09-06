"""
Owns the MemoryCore Gateway process instead of just hoping something else
started it — this is the piece the official v1 Hermes plugin describes for
itself (auto-start Gateway subprocess, 10s health watchdog, auto-recovery,
circuit-breaker protection, background sync thread) and that our earlier
v2-style TencentMemoryClient deliberately didn't do, since it assumed an
externally-managed Gateway. Standalone-from-Hermes means jarvis-memory has
to be the thing keeping its own dependency alive.

Four responsibilities, each handled by background threads:
1. Auto-start: if GATEWAY_START_CMD is set and the Gateway isn't reachable
   at boot, spawn it as a subprocess.
2. Health watchdog: poll /health every WATCHDOG_INTERVAL seconds; after
   FAILURE_THRESHOLD consecutive misses, kill and respawn the subprocess
   (only if we started it — never kill a Gateway we don't own).
3. Background sync: periodically call session_end() for every profile_id
   this process has touched, so long-running conversations still get
   drained into L1 even if nobody explicitly closes the session.
4. Profile hygiene: TTL-based eviction so _known_profiles doesn't grow
   forever — profiles inactive for PROFILE_TTL seconds are dropped from
   the sync loop.
"""

import logging
import subprocess
import threading
import time
from datetime import datetime
from typing import Optional, Set, Dict

from .config import CONFIG
from .tencent_memory import TencentMemoryClient

logger = logging.getLogger(__name__)


class GatewaySupervisor:
    WATCHDOG_INTERVAL = CONFIG.watchdog_interval
    SYNC_INTERVAL = CONFIG.sync_interval
    FAILURE_THRESHOLD = CONFIG.watchdog_failure_threshold
    PROFILE_TTL = CONFIG.profile_ttl

    def __init__(
        self,
        memory_client: TencentMemoryClient,
        start_cmd: Optional[list] = None,
        auto_start: Optional[bool] = None,
        autonomous_executor=None,
    ):
        """
        start_cmd: argv list to launch the Gateway (e.g. ["node", "path/to/gateway/index.js"]).
                   Defaults to CONFIG.gateway_start_cmd (env GATEWAY_START_CMD) split on
                   whitespace. Leave both unset if you're running the Gateway yourself
                   (Docker, systemd, etc.) — the watchdog will then only monitor, never
                   spawn or kill it.
        autonomous_executor: optional reference to AutonomousExecutor for stall checking.
        """
        self.memory = memory_client
        if start_cmd is None and CONFIG.gateway_start_cmd:
            start_cmd = CONFIG.gateway_start_cmd.split()
        self.start_cmd = start_cmd
        self.auto_start = (auto_start if auto_start is not None else bool(start_cmd)) and start_cmd is not None
        self.autonomous_executor = autonomous_executor

        self._proc: Optional[subprocess.Popen] = None
        self._we_own_process = False
        self._stop_event = threading.Event()
        self._consecutive_misses = 0

        # Profile tracking with TTL-based eviction
        self._profile_last_seen: Dict[str, float] = {}  # profile_id -> time.monotonic()
        self._profiles_lock = threading.Lock()

        self._watchdog_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None

    # --- lifecycle -----------------------------------------------------

    def start(self):
        if self.auto_start and not self.memory.health():
            self._spawn()

        self._stop_event.clear()
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._watchdog_thread.start()
        self._sync_thread.start()
        logger.info("GatewaySupervisor started (auto_start=%s)", self.auto_start)

    def stop(self):
        self._stop_event.set()
        if self._watchdog_thread:
            self._watchdog_thread.join(timeout=5)
        if self._sync_thread:
            self._sync_thread.join(timeout=5)
        if self._we_own_process and self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    # --- process management ---------------------------------------------

    def _spawn(self):
        if not self.start_cmd:
            return
        logger.info("Spawning MemoryCore Gateway: %s", " ".join(self.start_cmd))
        
        kwargs = {}
        import sys
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
            
        import os
        cwd = os.environ.get("GATEWAY_CWD")
        
        self._proc = subprocess.Popen(
            self.start_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=cwd,
            **kwargs
        )
        self._we_own_process = True
        self._consecutive_misses = 0
        # Give it a moment to bind its port before the watchdog starts judging it.
        time.sleep(2)

    def _respawn(self):
        if not self._we_own_process:
            logger.warning("Gateway unhealthy but jarvis-memory doesn't own the process — not restarting it.")
            return
        logger.error("Gateway missed %d consecutive health checks — respawning.", self._consecutive_misses)
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._spawn()

    # --- background loops ------------------------------------------------

    def _watchdog_loop(self):
        while not self._stop_event.wait(self.WATCHDOG_INTERVAL):
            if self.memory.health():
                if self._consecutive_misses > 0:
                    logger.info("Gateway healthy again after %d miss(es).", self._consecutive_misses)
                self._consecutive_misses = 0
            else:
                self._consecutive_misses += 1
                if self._consecutive_misses >= self.FAILURE_THRESHOLD:
                    self._respawn()

            # Stall detection — runs every watchdog tick (cheap check)
            if self.autonomous_executor:
                try:
                    stalled = self.autonomous_executor.check_stalled_workflows()
                    if stalled:
                        for s in stalled:
                            logger.warning(
                                "Stalled workflow detected: %s (step %s, %ds elapsed)",
                                s["workflow_id"],
                                s.get("stalled_step", {}).get("id") if s.get("stalled_step") else "?",
                                s.get("elapsed_seconds", 0),
                            )
                except Exception as e:
                    logger.debug("Stall check failed: %s", e)

            # Proactivity Triggers — auto-spawn workflows on schedule
            if self.autonomous_executor and hasattr(self.autonomous_executor, "store"):
                try:
                    now_dt = datetime.now()
                    triggers = self.autonomous_executor.store.get_triggers()
                    for t in triggers:
                        if not t["last_run"]:
                            should_run = True
                        else:
                            last_run_dt = datetime.fromisoformat(t["last_run"])
                            should_run = (now_dt - last_run_dt).total_seconds() >= t["interval_seconds"]
                        
                        if should_run:
                            logger.info(f"Trigger {t['id']} firing for goal: {t['goal']}")
                            # Mark run immediately to prevent double fires
                            self.autonomous_executor.store.update_trigger_last_run(t['id'])
                            
                            # Automatically plan and queue the workflow!
                            import uuid
                            from .tools.planner import WorkflowPlanner
                            try:
                                # Instantiate a local planner specifically for cron jobs
                                local_planner = WorkflowPlanner(memory_engine=self.memory, store=self.autonomous_executor.store)
                                plan = local_planner.create_plan(t["goal"], t["profile_id"])
                                wf_id = f"cron-{uuid.uuid4().hex[:8]}"
                                self.autonomous_executor.start_workflow(workflow_id=wf_id, plan=plan, profile_id=t["profile_id"])
                                logger.info(f"Proactive workflow {wf_id} successfully queued.")
                                
                                # Voice Alert
                                try:
                                    from .tools.os_assistant import OSAssistant
                                    OSAssistant.speak(f"Sir, I have automatically queued a new background workflow for the goal: {t['goal']}")
                                except:
                                    pass
                            except Exception as plan_e:
                                logger.error(f"Failed to plan proactive trigger {t['id']}: {plan_e}")
                except Exception as e:
                    logger.debug("Trigger evaluation failed: %s", e)

            # System Telemetry Auto-Recovery
            try:
                from .tools.os_assistant import OSAssistant
                telemetry = OSAssistant.get_telemetry()
                
                # Check for critical thresholds (95% CPU or RAM)
                critical_issues = []
                if telemetry.get("cpu_percent", 0) > 95:
                    critical_issues.append(f"CPU usage critically high at {telemetry['cpu_percent']}%")
                if telemetry.get("ram_percent", 0) > 95:
                    critical_issues.append(f"RAM usage critically high at {telemetry['ram_percent']}%")
                
                if critical_issues and self.autonomous_executor:
                    # Debounce so we don't trigger every 10 seconds
                    if not hasattr(self, "_last_telemetry_trigger"):
                        self._last_telemetry_trigger = 0
                    
                    if time.monotonic() - self._last_telemetry_trigger > 600: # 10 minute cooldown
                        self._last_telemetry_trigger = time.monotonic()
                        logger.critical(f"SYSTEM OVERLOAD DETECTED: {', '.join(critical_issues)}. Triggering auto-recovery workflow.")
                        
                        goal = f"EMERGENCY: {', '.join(critical_issues)}. Find the rogue processes causing this and kill them to restore system health."
                        
                        # Bypass AI planning (which requires heavy compute) to prevent Death Spiral (Defect 6)
                        plan = {
                            "goal": goal,
                            "steps": [
                                {
                                    "id": 1,
                                    "action": "Diagnose top CPU/RAM consuming processes.",
                                    "tool": "jarvis_os_control",
                                    "parameters": {"action": "diagnose_load"},
                                    "confidence": 0.9,
                                    "risk": "medium",
                                    "requires_approval": True
                                }
                            ],
                            "estimated_steps": 1,
                            "success_criteria": "System load returned to normal",
                            "fallback_mode": True
                        }
                        
                        import uuid
                        wf_id = f"auto-recovery-{uuid.uuid4().hex[:8]}"
                        self.autonomous_executor.start_workflow(workflow_id=wf_id, plan=plan, profile_id="system_admin_bot")
                        
                        # Voice Alert
                        try:
                            OSAssistant.speak("Alert. System overload detected. I have queued an emergency recovery workflow.")
                        except:
                            pass
            except Exception as e:
                logger.debug("System telemetry check failed: %s", e)

    def _sync_loop(self):
        while not self._stop_event.wait(self.SYNC_INTERVAL):
            now = time.monotonic()

            with self._profiles_lock:
                # Evict and sync profiles that have been idle for SYNC_INTERVAL.
                # If they type again, note_profile_active will re-add them.
                # This prevents shattering active conversations every 5 minutes.
                to_sync = []
                for pid, last_seen in list(self._profile_last_seen.items()):
                    if (now - last_seen) > self.SYNC_INTERVAL:
                        to_sync.append(pid)
                        del self._profile_last_seen[pid]
                        logger.debug("Profile %s idle, triggering background session_end and evicting", pid)

            for profile_id in to_sync:
                try:
                    self.memory.session_end(profile_id)
                except Exception as e:
                    logger.debug("Background sync drain failed for %s: %s", profile_id, e)

    # --- bookkeeping hook -------------------------------------------------

    def note_profile_active(self, profile_id: str):
        """Call this whenever a profile_id is touched so the sync loop knows to drain it."""
        with self._profiles_lock:
            self._profile_last_seen[profile_id] = time.monotonic()

    def status(self) -> dict:
        with self._profiles_lock:
            profile_count = len(self._profile_last_seen)
        return {
            "auto_start": self.auto_start,
            "owns_process": self._we_own_process,
            "process_alive": bool(self._proc and self._proc.poll() is None) if self._we_own_process else None,
            "consecutive_misses": self._consecutive_misses,
            "known_profiles": profile_count,
        }
