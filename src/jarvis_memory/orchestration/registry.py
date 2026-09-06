"""Read-only discovery of Hermes profiles/Bots for Jarvis planning.

Hermes is the source of truth. Jarvis discovers only bounded, non-secret
configuration/profile metadata and never reads credentials, auth databases,
sessions, or memory stores.
"""
from __future__ import annotations

from copy import deepcopy
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml

_SECRET_KEY_RE = re.compile(
    r"(?i)(api[_ -]?key|api[_ -]?secret|token|secret|password|passwd|credential|authorization|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|private[_ -]?key)"
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(api[_ -]?key|api[_ -]?secret|token|secret|password|passwd|credential|authorization|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|private[_ -]?key)\s*[:=]\s*[^\s,;]+"
)


def _safe_value(value: Any, *, depth: int = 0, max_items: int = 50, max_text: int = 800) -> Any:
    """Recursively copy configuration while filtering secret-looking keys."""
    if depth > 3:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:max_items]:
            key = str(raw_key).strip()
            if not key or _SECRET_KEY_RE.search(key):
                continue
            result[key] = _safe_value(raw_value, depth=depth + 1, max_items=max_items, max_text=max_text)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item, depth=depth + 1, max_items=max_items, max_text=max_text) for item in list(value)[:max_items]]
    if isinstance(value, str):
        return value[:max_text]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:max_text]


def _read_yaml(path: Path) -> tuple[Dict[str, Any], bool]:
    if not path.is_file():
        return {}, False
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError):
        return {}, False
    if raw is None:
        return {}, True
    if not isinstance(raw, Mapping):
        return {}, False
    return _safe_value(raw), True


def _read_soul(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    text = _SECRET_VALUE_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return text.strip()[:1600]


def _list_skills(home: Path) -> List[str]:
    skills_dir = home / "skills"
    if not skills_dir.is_dir():
        return []
    skills: List[str] = []
    try:
        for item in sorted(skills_dir.rglob("SKILL.md"), key=lambda p: str(p).lower()):
            try:
                rel = str(item.parent.relative_to(skills_dir))
            except ValueError:
                continue
            skills.append(rel or item.parent.name)
            if len(skills) >= 200:
                break
    except OSError:
        return skills
    return skills


def _profile_record(profile_id: str, home: Path, *, is_default: bool, active_profile_id: str) -> Dict[str, Any]:
    config, config_valid = _read_yaml(home / "config.yaml")
    profile_meta, profile_meta_valid = _read_yaml(home / "profile.yaml")
    model = config.get("model") if isinstance(config.get("model"), Mapping) else {}
    terminal = config.get("terminal") if isinstance(config.get("terminal"), Mapping) else {}
    tools = config.get("tools") if isinstance(config.get("tools"), Mapping) else {}
    declared_capabilities = config.get("capabilities", config.get("capability", []))
    if isinstance(declared_capabilities, str):
        declared_capabilities = [declared_capabilities]
    if not isinstance(declared_capabilities, list):
        declared_capabilities = []
    skills = _list_skills(home)
    capabilities = list(dict.fromkeys([str(x).strip() for x in declared_capabilities if str(x).strip()] + skills))
    role_text = str(profile_meta.get("description") or config.get("description") or config.get("role") or "").strip()
    name = str(profile_meta.get("display_name") or profile_meta.get("name") or profile_id).strip()[:200]
    active = bool(active_profile_id and active_profile_id == profile_id)
    available = bool(home.is_dir() and config_valid)
    return {
        "id": profile_id,
        "name": name,
        "role": role_text[:300],
        "description": role_text[:600],
        "model": str(model.get("default") or model.get("name") or "")[:200],
        "provider": str(model.get("provider") or "")[:200],
        "terminal_cwd": str(terminal.get("cwd") or "")[:500],
        "toolsets": _safe_value(tools),
        "capabilities": capabilities[:250],
        "skills": skills,
        "soul_excerpt": _read_soul(home / "SOUL.md"),
        "profile_metadata": profile_meta,
        "config_metadata": config,
        "home": str(home),
        "is_default": is_default,
        "is_active": active,
        "available": available,
        "config_valid": config_valid,
        "profile_metadata_valid": profile_meta_valid,
    }


class HermesRegistry:
    """Discover safe Hermes profile/Bot metadata without mutating Hermes."""

    def __init__(self, root: Optional[str | Path] = None, discovery_ttl: float = 3.0):
        self.requested_root = Path(root).expanduser() if root else None
        self.discovery_ttl = max(0.0, float(discovery_ttl))
        self._snapshot: Dict[str, Any] = {"source": "filesystem", "root": "", "profiles": [], "bots": [], "profile_count": 0, "bot_count": 0, "active_profile_id": ""}
        self._last_discovery = 0.0

    def _root(self) -> Path:
        raw = str(self.requested_root or os.environ.get("HERMES_HOME", "") or (Path.home() / ".hermes"))
        path = Path(raw).expanduser()
        if path.name in {"profiles", "bots"} and path.parent.name not in {"", "/"}:
            return path.parent
        if path.parent.name == "profiles":
            return path.parent.parent
        return path

    @staticmethod
    def _active_profile_id() -> str:
        for key in ("HERMES_PROFILE", "HERMES_PROFILE_ID", "HERMES_BOT"):
            value = str(os.environ.get(key, "")).strip()
            if value:
                return value
        return ""

    def refresh(self) -> Dict[str, Any]:
        root = self._root()
        active_profile_id = self._active_profile_id()
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

        profiles = [_profile_record(pid, home, is_default=is_default, active_profile_id=active_profile_id) for pid, home, is_default in candidates]
        bots = []
        for profile in profiles:
            bots.append({
                "id": profile["id"],
                "name": profile["name"],
                "role": profile["role"],
                "description": profile["description"],
                "capabilities": list(profile["capabilities"]),
                "skills": list(profile["skills"]),
                "model": profile["model"],
                "provider": profile["provider"],
                "toolsets": deepcopy(profile["toolsets"]),
                "is_default": profile["is_default"],
                "is_active": profile["is_active"],
                "available": profile["available"],
            })
        self._snapshot = {
            "source": "filesystem",
            "root": str(root),
            "active_profile_id": active_profile_id,
            "profiles": profiles,
            "bots": bots,
            "profile_count": len(profiles),
            "bot_count": len(bots),
            "refreshed_at": time.time(),
        }
        self._last_discovery = time.monotonic()
        return self.snapshot()

    def discover(self, force_refresh: bool = False) -> Dict[str, Any]:
        if force_refresh or (time.monotonic() - self._last_discovery) >= self.discovery_ttl:
            return self.refresh()
        return self.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        return deepcopy(self._snapshot)

    def profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        snapshot = self.discover()
        wanted = str(profile_id).strip()
        return next((deepcopy(item) for item in snapshot.get("profiles", []) if item.get("id") == wanted), None)

    def bots(self) -> List[Dict[str, Any]]:
        return deepcopy(self.discover().get("bots", []))


_DEFAULT_REGISTRY: HermesRegistry | None = None


def get_default_registry() -> HermesRegistry:
    """Reuse one registry instance so organisation/context discovery shares a cache."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = HermesRegistry()
    return _DEFAULT_REGISTRY
