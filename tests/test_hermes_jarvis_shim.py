from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHIM = ROOT / "scripts" / "hermes-jarvis-shim.sh"


def _make_fake_hermes(tmp_path: Path) -> tuple[Path, Path]:
    shim_bin = tmp_path / "shim-bin"
    real_bin = tmp_path / "real-bin"
    shim_bin.mkdir()
    real_bin.mkdir()

    # The wrapper discovers `hermes` through PATH. Symlink it under the command
    # name so the wrapper can skip itself and find the real executable next.
    os.symlink(SHIM, shim_bin / "hermes")

    real = real_bin / "hermes"
    real.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" > \"$HERMES_ARGS_FILE\"\n",
        encoding="utf-8",
    )
    real.chmod(real.stat().st_mode | stat.S_IXUSR)
    return shim_bin, real_bin


def _run(tmp_path: Path, *args: str) -> str:
    shim_bin, real_bin = _make_fake_hermes(tmp_path)
    args_file = tmp_path / "args.txt"
    env = os.environ.copy()
    env["PATH"] = f"{shim_bin}:{real_bin}:{env.get('PATH', '')}"
    env["HERMES_ARGS_FILE"] = str(args_file)
    subprocess.run(
        ["bash", str(shim_bin / "hermes"), *args],
        env=env,
        check=True,
    )
    return args_file.read_text(encoding="utf-8").strip()


def test_start_jarvis_translates_to_native_plugin_command(tmp_path: Path) -> None:
    assert _run(tmp_path, "start", "jarvis") == "jarvis start"


def test_stop_jarvis_translates_to_native_plugin_command(tmp_path: Path) -> None:
    assert _run(tmp_path, "stop", "jarvis") == "jarvis stop"


def test_other_hermes_commands_are_unchanged(tmp_path: Path) -> None:
    assert _run(tmp_path, "plugins", "list") == "plugins list"


def test_start_for_another_target_is_unchanged(tmp_path: Path) -> None:
    assert _run(tmp_path, "start", "something-else") == "start something-else"
