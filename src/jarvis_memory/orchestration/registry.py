"""Read-only discovery of Hermes profiles/Bots for Jarvis planning.

Jarvis treats Hermes as the source of truth. Discovery reads only non-secret
metadata and never reads credentials, auth databases, memories or sessions.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml

_SECRET_KEYS = {
    "api_key", "apikey", "key", "token", "secret", "password", "passwd",
    "credential", "credentials", "auth", "authorization", "access_token",
    "refresh_token", "client_secret", "bot_token",
}


def _safe_scalar(value: Any, limit: int = 500) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:limit]


def _safe_mapping(value: Any, depth: int = 0) -> Dict[str, Any]:
    if depth > 2 or not isinstance(value, Mapping):
        return {}
    result: Dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key or any(secret in key.lower() for secret in _SECRET_KEYS):
            continue
        if isinstance(raw_value, Mapping):
            result[key] = _safe_mapping(raw_value, depth + 1)
        elif isinstance(raw_value, list):
            result[key] = [_safe_scalar(item) for item in raw_value[:50]]
        else:
            result[key] = _safe_scalar(raw_value)
    return result


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.is_file():
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
    text = re.sub(r"(?i)(api[_ -]?key|token|password|secret)\s*[:=]\s*\S+", r"\1=[REDACTED]", text)
    return text.strip()[:1200]


def _profile_record(profile_id: str, home: Path, *, is_default: bool) -> Dict[str, Any]:
    config = _read_yaml(home / "config.yaml")
    display = _read_yaml(home / "profile.yaml")
    model = config.get("model") if isinstance(config.get("model"), Mapping) else {}
    terminal = config.get("terminal") if isinstance(config.get("terminal"), Mapping) else {}
    tools = config.get("tools") if isinstance(config.get("tools"), Mapping) else {}
    skills: List[str] = []
    skills_dir = home / "skills"
    if skills_dir.is_dir():
        try:
            for item in sorted(skills_dir.rglob("SKILL.md")):
                skills.append(str(item.parent.relative_to(skills_dir)))
                if len(skills) >= 200:
                    break
        except OSError:
            pass
    role_text = str(display.get("description") or config.get("description") or "").strip()
    return {
        "id": profile_id,
        "name": str(display.get("display_name") or profile_id)[:200],
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
    """Discover safe Hermes profile/Bot metadata without mutating Hermes."""

    def __init__(self, root: Optional[str | Path] = None):
        self.requested_root = Path(root).expanduser() if root else None
        self._snapshot: Dict[str, Any] = {"profiles": [], "bots": [], "source": "filesystem"}

    def _root(self) -> Path:
        raw = str(self.requested_root or os.environ.get("HERMES_HOME", "") or (Path.home() / ".hermes"))
        path = Path(raw).expanduser()
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
                    if home.is_dir() and not home.name.startswith(".") and (home / "config.yaml").is_file():
                        candidates.append((home.name, home, False))
            except OSError:
                pass
        profiles = [_profile_record(pid, home, is_default=is_default) for pid, home, is_default in candidates]
        bots = [
            {"id": p["id"], "name": p["name"], "role": p["role"], "description": p["description"],
             "capabilities": list(p["skills"]), "model": p["model"], "provider": p["provider"],
             "available": p["available"]}
            for p in profiles
        ]
        self._snapshot = {"source": "filesystem", "root": str(root), "profiles": profiles, "bots": bots,
                          "profile_count": len(profiles), "bot_count": len(bots)}
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
        return next((dict(item) for item in self._snapshot.get("profiles", []) if item.get("id") == wanted), None)

    def bots(self) -> List[Dict[str, Any]]:
        return [dict(item) for item in self._snapshot.get("bots", [])]


_DEFAULT_REGISTRY: HermesRegistry | None = None


def get_default_registry() -> HermesRegistry:
    """Reuse one registry instance so org/context discovery shares its cache."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = HermesRegistry()
    return _DEFAULT_REGISTRY
