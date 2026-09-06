"""
OS Assistant & Telemetry Module

Brings traditional "Jarvis" features (Voice, OS Control, Hardware Telemetry) 
to the MCP plugin without the bloat of IoT/Smart Home networks.
Designed specifically for macOS via osascript/pmset, but degrades gracefully.
"""

import os
import subprocess
import psutil
from typing import Dict, Any

class OSAssistant:
    @staticmethod
    def speak(text: str) -> str:
        """Use the native macOS TTS engine to speak text out loud."""
        try:
            # Escape single quotes for bash
            safe_text = text.replace("'", "\\'")
            subprocess.run(["say", safe_text], check=True)
            return f"Successfully spoke: {text}"
        except Exception as e:
            return f"Failed to speak: {str(e)}"

    @staticmethod
    def get_telemetry() -> Dict[str, Any]:
        """Get 'monitor_operative' system vitals."""
        try:
            cpu = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Try to get battery on Mac
            battery_percent = "Unknown"
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
                apple_script = 'tell application "System Events" to key code 53 using command down' # Fallback for media key
                # Actually, standard media play/pause key code is 100, but requires specific targeting.
                # Let's use standard Music/Spotify toggles if available, or just generic key code.
                script = '''
                tell application "Spotify"
                    playpause
                end tell
                '''
                subprocess.run(["osascript", "-e", script])
                return "Toggled play/pause (Targeting Spotify/Music)"
                
            else:
                return f"Unknown OS action: {action}"
                
        except Exception as e:
            return f"OS Control failed for {action}: {str(e)}"
