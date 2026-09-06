import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis_memory.tencent_runtime import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_OLLAMA_MODEL,
    TencentRuntime,
)


class TestTencentRuntime(unittest.TestCase):
    def test_model_provisioning_pulls_missing_models_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = TencentRuntime()
            commands = []

            def fake_run(cmd, **kwargs):
                commands.append(cmd)

            with patch.object(runtime, "_ollama_model_available", side_effect=[False, True]), \
                 patch.object(runtime, "_run", side_effect=fake_run):
                runtime._ensure_ollama_models(
                    Path(tmp),
                    "ollama",
                    [DEFAULT_OLLAMA_MODEL, DEFAULT_EMBEDDING_MODEL],
                )

            self.assertEqual(commands, [["ollama", "pull", DEFAULT_OLLAMA_MODEL]])

    def test_repeated_start_is_idempotent_for_healthy_owned_stack(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".hermes"
            tencent = home / ".jarvis" / "tencentdb" / "source"
            (tencent / "deploy" / "global-images").mkdir(parents=True)

            first = TencentRuntime()
            with patch.object(first, "_ensure_tencent_source", return_value=tencent), \
                 patch.object(first, "_start_ollama"), \
                 patch.object(first, "_env_file"), \
                 patch.object(first, "_port_open", side_effect=lambda host, port, timeout=0.5: False if port in {8420, 8125, 8096} else True), \
                 patch.object(first, "_run") as first_run:
                first.start(str(home), force=True)
            self.assertEqual(first_run.call_count, 3)

            second = TencentRuntime()
            with patch.object(second, "_start_ollama"), \
                 patch.object(second, "_env_file"), \
                 patch.object(second, "_port_open", return_value=True), \
                 patch.object(second, "_run") as second_run:
                second.start(str(home), force=True)

            self.assertEqual(second_run.call_count, 0)
            self.assertTrue(second.status(str(home))["tencent_owned_by_jarvis"])

    def test_ollama_ownership_survives_new_cli_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".hermes"
            state_dir = home / ".jarvis"
            state_dir.mkdir(parents=True)
            (state_dir / "runtime-state.json").write_text(
                json.dumps({
                    "version": 2,
                    "enabled": True,
                    "hermes_home": str(home),
                    "tencent_root": "",
                    "tencent_owned": False,
                    "ollama_owned": True,
                    "ollama_pid": 4242,
                    "ollama_pgid": 4242,
                }),
                encoding="utf-8",
            )
            runtime = TencentRuntime()
            with patch.object(runtime, "_process_command", return_value="ollama serve"), \
                 patch.object(runtime, "_port_open", return_value=True), \
                 patch.object(runtime, "_ollama_model_available", return_value=True), \
                 patch.object(runtime, "_ensure_ollama_source", create=True), \
                 patch.object(runtime, "_ensure_tencent_source", return_value=home / ".jarvis" / "tencentdb" / "source"):
                # _start_ollama only needs to demonstrate ownership preservation here.
                root = home / ".jarvis" / "tencentdb" / "source"
                (root / "deploy" / "global-images").mkdir(parents=True)
                runtime._start_ollama(home, DEFAULT_OLLAMA_MODEL, DEFAULT_EMBEDDING_MODEL, runtime._load_state(home))
            self.assertTrue(runtime._ollama_owned)

    def test_start_records_selected_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".hermes"
            tencent = home / ".jarvis" / "tencentdb" / "source"
            (tencent / "deploy" / "global-images").mkdir(parents=True)
            runtime = TencentRuntime()
            with patch.object(runtime, "_ensure_tencent_source", return_value=tencent), \
                 patch.object(runtime, "_start_ollama"), \
                 patch.object(runtime, "_env_file"), \
                 patch.object(runtime, "_port_open", return_value=True):
                runtime.start(str(home), force=True)
            state = json.loads((home / ".jarvis" / "runtime-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["ollama_model"], DEFAULT_OLLAMA_MODEL)
            self.assertEqual(state["embedding_model"], DEFAULT_EMBEDDING_MODEL)


if __name__ == "__main__":
    unittest.main()
