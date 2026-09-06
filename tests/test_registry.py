"""Tests for safe Hermes profile/Bot discovery."""
from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from jarvis_memory.orchestration.registry import HermesRegistry


class TestHermesRegistry(unittest.TestCase):
    def test_discovers_default_and_named_profiles_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".hermes"
            default = root
            coder = root / "profiles" / "coder"
            (default / "skills" / "general").mkdir(parents=True)
            coder.mkdir(parents=True)
            (coder / "skills" / "python").mkdir(parents=True)
            (default / "config.yaml").write_text(
                "model:\n  default: local-model\nterminal:\n  cwd: /work\napi_key: DO_NOT_CAPTURE\n",
                encoding="utf-8",
            )
            (coder / "config.yaml").write_text(
                "model:\n  default: coder-model\nterminal:\n  cwd: /code\n",
                encoding="utf-8",
            )
            (coder / "profile.yaml").write_text(
                "display_name: Coder\ndescription: Writes and fixes Python code.\n",
                encoding="utf-8",
            )
            (coder / "SOUL.md").write_text(
                "You are a coding specialist. token: super-secret-value\n",
                encoding="utf-8",
            )
            (coder / "skills" / "python" / "SKILL.md").write_text("# Python\n", encoding="utf-8")

            snapshot = HermesRegistry(root).discover()
            ids = {item["id"] for item in snapshot["profiles"]}
            self.assertEqual(ids, {"default", "coder"})
            coder_record = next(item for item in snapshot["profiles"] if item["id"] == "coder")
            self.assertEqual(coder_record["model"], "coder-model")
            self.assertEqual(coder_record["skills"], ["python"])
            self.assertIn("[REDACTED]", coder_record["soul_excerpt"])
            self.assertNotIn("super-secret-value", str(snapshot))
            self.assertNotIn("DO_NOT_CAPTURE", str(snapshot))

    def test_named_profile_home_environment_is_mapped_to_hermes_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".hermes"
            profile = root / "profiles" / "researcher"
            profile.mkdir(parents=True)
            (root / "config.yaml").write_text("model:\n  default: root\n", encoding="utf-8")
            (profile / "config.yaml").write_text("model:\n  default: research\n", encoding="utf-8")
            registry = HermesRegistry(profile)
            snapshot = registry.discover()
            self.assertEqual(snapshot["profile_count"], 2)
            self.assertEqual(snapshot["bot_count"], 2)


if __name__ == "__main__":
    unittest.main()
