"""Read-only discovery of Hermes profiles/Bots for Jarvis planning.

Jarvis treats Hermes as the source of truth. This module discovers only
non-secret profile metadata and never reads credentials, auth databases or
memory/session contents.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only on incomplete installs
    yaml = None

_SECRET_KEYS = {
    "api_key", "apikey", "key", "token", "secret", "password", "passwd",
    "credential", "credentials", "auth", "authorization", "access_token",
    "refresh_token", "client_secret", "bot_token",
}


def _safe_scalar(value: Any, *, limit: int = 500) -> Any:
    """Return a bounded JSON-safe scalar with obvious secrets removed."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return str(value)[:limit] if isinstance(value, str) else value
    return str(value)[:limit]


def _safe_mapping(value: Any, depth: int = 0) -> Dict[str, Any]:
    if depth > 2 or not isinstance(value, Mapping):
        return {}
    output: Dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key:
            continue
        if any(secret in key.lower() for secret in _SECRET_KEYS):
            continue
        if isinstance(raw_value, Mapping):
            output[key] = _safe_mapping(raw_value, depth + 1)
        elif isinstance(raw_value, list):
            output[key] = [_safe_scalar(item) for item in raw_value[:50]]
        else:
            output[key] = _safe_scalar(raw_value)
    return output


def _read_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None or not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError):
        return {}
    return _safe_mapping(raw)


def _read_soul(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    # Keep only a small, redacted excerpt. SOUL is useful for role inference,
    # but should not become a second memory database.
    text = re.sub(r"(?i)(api[_ -]?key|token|password|secret)\s*[:=]\s*\S+", r"\1=[REDACTED]", text)
    return text.strip()[:1200]


def _profile_record(profile_id: str, home: Path, *, is_default: bool) -> Dict[str, Any]:
    config = _read_yaml(home / "config.yaml")
    display = _read_yaml(home / "profile.yaml")
    model = config.get("model", {}) if isinstance(config.get("model"), Mapping) else {}
    terminal = config.get("terminal", {}) if isinstance(config.get("terminal"), Mapping) else {}
    tools = config.get("tools", {}) if isinstance(config.get("tools"), Mapping) else {}
    skills_dir = home / "skills"
    skills: List[str] = []
    if skills_dir.is_dir():
        try:
            for item in sorted(skills_dir.rglob("SKILL.md")):
                if item.is_file() and len(skills) < 200:
                    skills.append(str(item.parent.relative_to(skills_dir)))
        except OSError:
            pass

    role_text = str(display.get("description") or config.get("description") or "").strip()
    return {
        "id": profile_id,
        "name": str(display.get("display_name") or profile_id),
        "role": role_text[:300],
        "description": role_text[:500],
        "model": _safe_scalar(model.get("default") or model.get("name") or ""),
        "provider": _safe_scalar(model.get("provider") or ""),
        "terminal_cwd": _safe_scalar(terminal.get("cwd") or ""),
        "toolsets": _safe_mapping(tools),
        "skills": skills,
        "soul_excerpt": _read_soul(home / "SOUL.md"),
        "home": str(home),
        "is_default": is_default,
        "available": True,
    }


class HermesRegistry:
    """Discover and cache the safe metadata Jarvis needs from Hermes."""

    def __init__(self, root: Optional[str | Path] = None):
        self.requested_root = Path(root).expanduser() if root else None
        self._snapshot: Dict[str, Any] = {"profiles": [], "bots": [], "source": "filesystem"}

    def _root(self) -> Path:
        env_home = os.environ.get("HERMES_HOME", "").strip()
        if self.requested_root:
            path = self.requested_root
        elif env_home:
            path = Path(env_home).expanduser()
        else:
            path = Path.home() / ".hermes"
        # A named profile's HERMES_HOME is <root>/profiles/<name>.
        if path.parent.name == "profiles":
            return path.parent.parent
        return path

    def discover(self) -> Dict[str, Any]:
        root = self._root()
        candidates: List[tuple[str, Path, bool]] = []
        if (root / "config.yaml").is_file():
            candidates.append(("default", root, True))

        profiles_dir = root / "profiles"
        if profiles_dir.is_dir():
            try:
                for home in sorted(profiles_dir.iterdir(), key=lambda p: p.name.lower()):
                    if not home.is_dir() or home.name.startswith("."):
                        continue
                    if (home / "config.yaml").is_file():
                        candidates.append((home.name, home, False))
            except OSError:
                pass

        profiles = [_profile_record(profile_id, home, is_default=is_default)
                    for profile_id, home, is_default in candidates]
        # In Hermes, Bot Mode is a view over profiles, so keep an explicit Bot
        # projection while retaining the full profile metadata separately.
        bots = [
            {
                "id": item["id"],
                "name": item["name"],
                "role": item["role"],
                "description": item["description"],
                "capabilities": list(item["skills"]),
                "model": item["model"],
                "available": item["available"],
            }
            for item in profiles
        ]
        self._snapshot = {
            "source": "filesystem",
            "root": str(root),
            "profiles": profiles,
            "bots": bots,
            "profile_count": len(profiles),
            "bot_count": len(bots),
        }
        return self.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "source": self._snapshot.get("source", "filesystem"),
            "root": self._snapshot.get("root", ""),
            "profiles": [dict(item) for item in self._snapshot.get("profiles", [])],
            "bots": [dict(item) for item in self._snapshot.get("bots", [])],
            "profile_count": int(self._snapshot.get("profile_count", 0)),
            "bot_count": int(self._snapshot.get("bot_count", 0)),
        }

    def profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        wanted = str(profile_id).strip()
        for item in self._snapshot.get("profiles", []):
            if item.get("id") == wanted:
                return dict(item)
        return None

    def bots(self) -> List[Dict[str, Any]]:
        return [dict(item) for item in self._snapshot.get("bots", [])]
