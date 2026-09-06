"""
OS Assistant & Telemetry Module

Brings traditional "Jarvis" features (Voice, OS Control, Hardware Telemetry) 
to the MCP plugin without the bloat of IoT/Smart Home networks.
Designed specifically for macOS via osascript/pmset, but degrades gracefully.
"""

import os
import sys
import subprocess
import psutil
from typing import Dict, Any

class OSAssistant:
    @staticmethod
    def speak(text: str) -> str:
        """Use the native macOS TTS engine to speak text out loud."""
        if sys.platform != "darwin":
            return "Speech synthesis is only supported on macOS."
        try:
            subprocess.run(["say", text], check=True)
            return f"Successfully spoke: {text}"
        except Exception as e:
            return f"Failed to speak: {str(e)}"

    @staticmethod
    def get_telemetry() -> Dict[str, Any]:
        """Get 'monitor_operative' system vitals."""
        try:
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage(os.path.abspath(os.sep))
            
            # Try to get battery on Mac
            battery_percent = "Unknown"
            if sys.platform == "darwin":
                try:
                    bat_out = subprocess.check_output(["pmset", "-g", "batt"], text=True)
                    if "%" in bat_out:
                        battery_percent = bat_out.split("%")[0].split()[-1] + "%"
                except:
                    pass

            return {
                "cpu_percent": cpu,
                "ram_percent": ram.percent,
                "ram_available_gb": round(ram.available / (1024**3), 2),
                "disk_percent": disk.percent,
                "disk_free_gb": round(disk.free / (1024**3), 2),
                "battery": battery_percent
            }
        except Exception as e:
            return {"error": f"Telemetry failed: {str(e)}"}

    @staticmethod
    def control_os(action: str, value: str = "") -> str:
        """Control native macOS functions via AppleScript."""
        if sys.platform != "darwin":
            return "OS control actions are only supported on macOS."
        action = action.lower()
        try:
            if action == "set_volume":
                # value should be 0 to 100
                vol = int(value)
                subprocess.run(["osascript", "-e", f"set volume output volume {vol}"], check=True)
                return f"Volume set to {vol}%"
                
            elif action == "mute":
                subprocess.run(["osascript", "-e", "set volume with output muted"], check=True)
                return "System muted"
                
            elif action == "unmute":
                subprocess.run(["osascript", "-e", "set volume without output muted"], check=True)
                return "System unmuted"
                
            elif action == "lock_screen":
                subprocess.run(["pmset", "displaysleepnow"], check=True)
                return "Screen locked/sleeping"
                
            elif action == "play_pause_media":
                script = '''
                try
                    tell application "Spotify" to playpause
                on error
                    try
                        tell application "Music" to playpause
                    on error
                        error "Neither Spotify nor Music is running"
                    end try
                end try
                '''
                subprocess.run(["osascript", "-e", script], check=True, stderr=subprocess.PIPE)
                return "Toggled play/pause (Targeting Spotify/Music)"
                
            else:
                return f"Unknown OS action: {action}"
                
        except Exception as e:
            return f"OS Control failed for {action}: {str(e)}"
