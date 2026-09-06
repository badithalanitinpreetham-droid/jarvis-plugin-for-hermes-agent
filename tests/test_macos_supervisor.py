import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis_memory.macos_supervisor import LABEL, _plist_content, status


class TestMacOSSupervisor(unittest.TestCase):
    def test_plist_contains_user_agent_and_python_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = _plist_content(Path(tmp) / ".hermes")
            self.assertIn(LABEL, text)
            self.assertIn("jarvis_memory.macos_supervisor", text)
            self.assertIn(sys.executable, text)
            self.assertIn("KeepAlive", text)
            self.assertIn("RunAtLoad", text)

    def test_status_is_safe_on_non_macos(self):
        with patch("jarvis_memory.macos_supervisor.sys.platform", "linux"):
            self.assertEqual(status(), {"supported": False, "loaded": False})


if __name__ == "__main__":
    unittest.main()
