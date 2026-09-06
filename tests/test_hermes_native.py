import os
import tempfile
import unittest
from pathlib import Path

from jarvis_memory.hermes_memory_provider import JarvisMemoryProvider
from jarvis_memory.hermes_plugin import JarvisPluginRuntime, _on_pre_llm_call, register


class FakeContext:
    def __init__(self):
        self.hooks = []
        self.tools = []

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)


class TestHermesNativeIntegration(unittest.TestCase):
    def setUp(self):
        self._old_autostart = os.environ.get("JARVIS_TENCENT_AUTOSTART")
        os.environ["JARVIS_TENCENT_AUTOSTART"] = "0"

    def tearDown(self):
        if self._old_autostart is None:
            os.environ.pop("JARVIS_TENCENT_AUTOSTART", None)
        else:
            os.environ["JARVIS_TENCENT_AUTOSTART"] = self._old_autostart

    def test_plugin_registers_current_hermes_surfaces(self):
        ctx = FakeContext()
        register(ctx)
        hook_names = {name for name, _ in ctx.hooks}
        self.assertIn("pre_llm_call", hook_names)
        self.assertIn("post_tool_call", hook_names)
        self.assertIn("subagent_stop", hook_names)
        self.assertEqual({tool["toolset"] for tool in ctx.tools}, {"jarvis"})

    def test_pre_llm_hook_returns_bounded_context_for_complex_goal(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = JarvisPluginRuntime()
            result = runtime.pre_llm_context(
                messages=[{"role": "user", "content": "Build and publish a weekly AI research report"}],
                profile_id="default", hermes_home=tmp,
            )
            self.assertTrue(result)
            self.assertLessEqual(len(result), 4500)
            runtime.close()

    def test_memory_provider_uses_hermes_home_scoped_experience_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = JarvisMemoryProvider()
            provider.initialize("session-1", hermes_home=tmp, agent_identity="research-bot", user_id="u1")
            self.assertEqual(provider.name, "jarvis")
            self.assertIn("supplementary organisational memory", provider.system_prompt_block())
            provider.sync_turn("research transformers", "done", session_id="session-1")
            self.assertTrue((Path(tmp) / ".jarvis" / "experience.db").exists())
            provider.shutdown()

    def test_trivial_pre_llm_hook_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _on_pre_llm_call(messages=[{"role": "user", "content": "thanks"}], hermes_home=tmp)
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
