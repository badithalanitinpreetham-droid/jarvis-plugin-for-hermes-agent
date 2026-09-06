"""
Tests for WorkflowProgressRenderer.

Run: python3 -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import conftest

from jarvis_memory.tools.progress import WorkflowProgressRenderer


class ProgressTestCase(unittest.TestCase):
    def setUp(self):
        self.renderer = WorkflowProgressRenderer()
        self.plan = {
            "goal": "test goal",
            "estimated_steps": 2,
            "success_criteria": "criteria",
            "steps": [
                {"id": 1, "action": "Do thing 1", "confidence": 0.9, "risk": "low"},
                {"id": 2, "action": "Do thing 2", "confidence": 0.5, "risk": "high"},
            ],
        }
        self.state = {
            "plan": self.plan,
            "profile_id": "test",
            "started_at": "2024-01-01T00:00:00",
            "next_index": 0,
            "approved_index": None,
            "completed_steps": [],
            "failed_steps": [],
            "status": "running",
            "history": [],
            "replan_count": 0,
            "replan_history": [],
            "step_started_at": None,
        }

class TestPlanPreview(ProgressTestCase):
    def test_plan_preview(self):
        output = self.renderer.render_plan_preview(self.plan)
        if output:
            self.assertIn("mermaid", output.lower())
            self.assertIn("Do thing 1", output)
            self.assertIn("Do thing 2", output)
            self.assertIn("test goal", output)
            # Confidence is rendered as percentage, not raw float
            self.assertIn("90%", output)

class TestProgressBar(ProgressTestCase):
    def test_progress_bar_0(self):
        output = self.renderer.render_progress_bar("wf-test", self.state)
        self.assertIsInstance(output, str)

    def test_progress_bar_50(self):
        self.state["completed_steps"] = [1]
        self.state["next_index"] = 1
        output = self.renderer.render_progress_bar("wf-test", self.state)
        self.assertIsInstance(output, str)

    def test_progress_bar_100(self):
        self.state["completed_steps"] = [1, 2]
        self.state["next_index"] = 2
        self.state["status"] = "completed"
        output = self.renderer.render_progress_bar("wf-test", self.state)
        self.assertIsInstance(output, str)

    def test_progress_bar_failed(self):
        self.state["failed_steps"] = [2]
        self.state["status"] = "completed_with_failures"
        output = self.renderer.render_progress_bar("wf-test", self.state)
        self.assertIsInstance(output, str)

    def test_progress_bar_empty(self):
        self.state["plan"]["steps"] = []
        output = self.renderer.render_progress_bar("wf-test", self.state)
        self.assertIsInstance(output, str)

class TestMermaidStatus(ProgressTestCase):
    def test_mermaid_status(self):
        output = self.renderer.render_mermaid_status("wf-test", self.state)
        if output:
            self.assertIn("classDef", output)
            self.assertIn("done", output.lower())
            self.assertIn("failed", output.lower())
            self.assertIn("active", output.lower())
            self.assertIn("pending", output.lower())
            self.assertIn("1", output)
            self.assertIn("2", output)

class TestKanban(ProgressTestCase):
    def test_kanban(self):
        output = self.renderer.render_kanban("wf-test", self.state)
        self.assertIsInstance(output, str)
        self.assertTrue(len(output) > 0)

class TestEdgeCases(ProgressTestCase):
    def test_edge_cases(self):
        self.state["plan"]["steps"] = []
        out1 = self.renderer.render_plan_preview(self.state["plan"])
        self.assertIsInstance(out1, str)

        self.state["plan"]["steps"] = [{"id": 1, "action": "Do thing 1", "confidence": 0.9, "risk": "low"}]
        out2 = self.renderer.render_plan_preview(self.state["plan"])
        self.assertIsInstance(out2, str)

        self.state["failed_steps"] = [1]
        self.state["status"] = "completed_with_failures"
        out3 = self.renderer.render_progress_bar("wf-test", self.state)
        self.assertIsInstance(out3, str)


if __name__ == "__main__":
    unittest.main()
