"""Regression tests for roster discovery and workflow-state hardening."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from jarvis_memory.orchestration.registry import HermesRegistry
from jarvis_memory.tools.autonomous import AutonomousExecutor
from jarvis_memory.workflow_store import WorkflowStore


def plan(*steps):
    return {"goal": "hardening test", "steps": list(steps)}


def step(step_id, *, confidence=0.95, risk="low", mode="sequential", **extra):
    value = {
        "id": step_id,
        "action": f"execute {step_id}",
        "confidence": confidence,
        "risk": risk,
        "execution_mode": mode,
    }
    value.update(extra)
    return value


class TestHermesRegistry(unittest.TestCase):
    def test_discovers_profile_metadata_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".hermes"
            profile = root / "profiles" / "researcher"
            profile.mkdir(parents=True)
            (root / "config.yaml").write_text(
                "model:\n  default: qwen3\n  provider: ollama\napi_key: SHOULD_NOT_APPEAR\n",
                encoding="utf-8",
            )
            (profile / "config.yaml").write_text(
                "description: Research specialist\nmodel:\n  default: qwen3:4b\n  provider: ollama\ntools:\n  web: true\nsecret_token: hidden\ncapabilities:\n  - web_research\n",
                encoding="utf-8",
            )
            (profile / "profile.yaml").write_text("display_name: Research Bot\n", encoding="utf-8")
            (profile / "SOUL.md").write_text("Research carefully. token=hidden-secret\n", encoding="utf-8")
            (profile / "skills" / "research").mkdir(parents=True)
            (profile / "skills" / "research" / "SKILL.md").write_text("# research\n", encoding="utf-8")

            previous = os.environ.get("HERMES_PROFILE")
            try:
                os.environ["HERMES_PROFILE"] = "researcher"
                snapshot = HermesRegistry(root).discover(force_refresh=True)
            finally:
                if previous is None:
                    os.environ.pop("HERMES_PROFILE", None)
                else:
                    os.environ["HERMES_PROFILE"] = previous

            bot = next(item for item in snapshot["bots"] if item["id"] == "researcher")
            profile_data = next(item for item in snapshot["profiles"] if item["id"] == "researcher")
            self.assertEqual(bot["name"], "Research Bot")
            self.assertIn("web_research", bot["capabilities"])
            self.assertTrue(bot["is_active"])
            self.assertNotIn("secret_token", profile_data["config_metadata"])
            self.assertNotIn("SHOULD_NOT_APPEAR", str(profile_data))
            self.assertNotIn("hidden-secret", profile_data["soul_excerpt"])

    def test_registry_cache_reuses_snapshot_until_ttl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".hermes"
            root.mkdir()
            (root / "config.yaml").write_text("description: Default\n", encoding="utf-8")
            registry = HermesRegistry(root, discovery_ttl=60)
            first = registry.discover()
            (root / "config.yaml").write_text("description: Changed\n", encoding="utf-8")
            second = registry.discover()
            self.assertEqual(first["profiles"][0]["description"], second["profiles"][0]["description"])
            fresh = registry.discover(force_refresh=True)
            self.assertEqual(fresh["profiles"][0]["description"], "Changed")


class TestWorkflowHardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = WorkflowStore(str(Path(self.tmp.name) / "workflows.db"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_approval_cannot_be_bypassed_by_get_next_step(self):
        executor = AutonomousExecutor(config={"auto_approve_confidence": 0.85}, store=self.store)
        risky = step(1, confidence=0.4, risk="high")
        first = executor.start_workflow("wf-approval", plan(risky), "profile")
        self.assertEqual(first["status"], "awaiting_approval")
        repeated = executor.get_next_step("wf-approval")
        self.assertEqual(repeated["status"], "awaiting_approval")
        approved = executor.approve_step("wf-approval", 1)
        self.assertEqual(approved["status"], "ready_to_execute")

    def test_failed_step_is_terminal_after_retry_budget_exhausted(self):
        executor = AutonomousExecutor(
            config={"auto_approve_confidence": 0.85, "replan_max_retries": 0},
            store=self.store,
        )
        executor.start_workflow("wf-failed", plan(step(1)), "profile")
        result = executor.report_step_result("wf-failed", 1, "failed", error="boom")
        self.assertEqual(result["status"], "completed_with_failures")
        self.assertEqual(executor.get_workflow_status("wf-failed")["failed_steps"], [1])

    def test_all_failed_race_members_do_not_loop_forever(self):
        executor = AutonomousExecutor(
            config={"auto_approve_confidence": 0.85, "replan_max_retries": 0},
            store=self.store,
        )
        first = step(1, mode="race", race_group_id="r1")
        second = step(2, mode="race", race_group_id="r1")
        result = executor.start_workflow("wf-race", plan(first, second), "profile")
        self.assertEqual(result["status"], "ready_to_execute")
        self.assertEqual({item["id"] for item in result["parallel_steps"]}, {1, 2})
        executor.report_step_result("wf-race", 1, "failed", error="first failed")
        result = executor.report_step_result("wf-race", 2, "failed", error="second failed")
        self.assertEqual(result["status"], "completed_with_failures")


if __name__ == "__main__":
    unittest.main()
