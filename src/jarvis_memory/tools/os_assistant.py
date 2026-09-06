"""macOS voice, controls and telemetry helpers."""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, Dict

import psutil


class OSAssistant:
    @staticmethod
    def speak(text: str) -> str:
        if sys.platform != "darwin":
            return "Speech synthesis is only supported on macOS."
        if not isinstance(text, str) or not text.strip():
            return "Speech text must not be empty."
        try:
            subprocess.Popen(["say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"Speech queued: {text}"
        except Exception as exc:
            return f"Failed to speak: {exc}"

    @staticmethod
    def get_telemetry() -> Dict[str, Any]:
        try:
            data: Dict[str, Any] = {
                "cpu_percent": psutil.cpu_percent(interval=0.1),
                "ram_percent": psutil.virtual_memory().percent,
                "ram_available_gb": round(psutil.virtual_memory().available / 1024**3, 2),
                "disk_percent": psutil.disk_usage(os.path.abspath(os.sep)).percent,
                "disk_free_gb": round(psutil.disk_usage(os.path.abspath(os.sep)).free / 1024**3, 2),
            }
            if sys.platform == "darwin":
                try:
                    out = subprocess.check_output(["pmset", "-g", "batt"], text=True, timeout=3)
                    import re
                    match = re.search(r"(\d+)%", out)
                    data["battery"] = f"{match.group(1)}%" if match else "Unknown"
                except Exception:
                    data["battery"] = "Unknown"
            else:
                data["battery"] = "Unknown"
            return data
        except Exception as exc:
            return {"error": f"Telemetry failed: {exc}"}

    @staticmethod
    def control_os(action: str, value: str = "") -> str:
        if sys.platform != "darwin":
            return "OS control actions are only supported on macOS."
        action = str(action).lower().strip()
        try:
            if action == "set_volume":
                vol = int(value)
                if not 0 <= vol <= 100:
                    return "Volume must be between 0 and 100."
                subprocess.run(["osascript", "-e", f"set volume output volume {vol}"], check=True,
                               capture_output=True, text=True, timeout=5)
                return f"Volume set to {vol}%"
            if action == "mute":
                subprocess.run(["osascript", "-e", "set volume with output muted"], check=True,
                               capture_output=True, text=True, timeout=5)
                return "System muted"
            if action == "unmute":
                subprocess.run(["osascript", "-e", "set volume without output muted"], check=True,
                               capture_output=True, text=True, timeout=5)
                return "System unmuted"
            if action == "lock_screen":
                # This asks macOS to lock immediately; unlike display sleep it is a security action.
                subprocess.run(["/usr/bin/osascript", "-e", 'tell application "System Events" to keystroke "q" using {control down, command down}'],
                               check=True, capture_output=True, text=True, timeout=5)
                return "Lock command sent"
            if action == "play_pause_media":
                script = 'tell application "Spotify" to playpause'
                try:
                    subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True, timeout=5)
                except subprocess.CalledProcessError:
                    subprocess.run(["osascript", "-e", 'tell application "Music" to playpause'], check=True,
                                   capture_output=True, text=True, timeout=5)
                return "Toggled media playback"
            if action == "diagnose_load":
                result = subprocess.run(["ps", "-A", "-o", "pid,%cpu,%mem,comm", "-r"], capture_output=True, text=True, timeout=5, check=True)
                return "Top Processes:\n" + "\n".join(result.stdout.splitlines()[:20])
            return f"Unknown OS action: {action}"
        except subprocess.CalledProcessError as exc:
            return f"OS control failed for {action}: {(exc.stderr or str(exc)).strip()}"
        except (ValueError, TypeError) as exc:
            return f"Invalid value for {action}: {exc}"
        except Exception as exc:
            return f"OS control failed for {action}: {exc}"
