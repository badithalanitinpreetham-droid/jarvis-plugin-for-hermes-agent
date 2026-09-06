import tempfile
import unittest
from pathlib import Path

from jarvis_memory.experience_store import ExperienceStore
from jarvis_memory.intelligence import JarvisIntelligence


class TestJarvisIntelligence(unittest.TestCase):
    def test_routing_modes(self):
        self.assertEqual(JarvisIntelligence.classify("hello").mode, "simple")
        self.assertEqual(JarvisIntelligence.classify("write a report about transformers").mode, "moderate")
        self.assertEqual(JarvisIntelligence.classify("Research, analyse, write, verify and publish a weekly report").mode, "complex")

    def test_experience_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceStore(str(Path(tmp) / "experience.db"))
            engine = JarvisIntelligence(store=store)
            item_id = engine.observe_outcome(
                goal="transformer report",
                status="success",
                strategy="research -> analyst -> reviewer",
                bots=["research", "reviewer"],
                lessons=["verify claims against primary sources"],
            )
            self.assertGreater(item_id, 0)
            rows = store.recent("transformer", limit=2)
            self.assertEqual(len(rows), 1)
            store.close()

    def test_policy_is_versioned_and_evidence_accumulates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceStore(str(Path(tmp) / "experience.db"))
            first = store.record_policy_result("research.topology", success=True, value="parallel research", confidence=0.7)
            second = store.record_policy_result("research.topology", success=False, value="parallel research", confidence=0.8)
            self.assertEqual(first["version"], 1)
            self.assertEqual(second["version"], 2)
            self.assertEqual(second["successes"], 1)
            self.assertEqual(second["failures"], 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
