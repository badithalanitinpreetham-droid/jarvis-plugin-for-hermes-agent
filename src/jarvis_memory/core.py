"""Core Jarvis memory and safe HTML editing operations."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from .tencent_memory import CircuitBreakerOpen, MemoryGatewayError, TencentMemoryClient

logger = logging.getLogger(__name__)

_SECRET_PATTERNS = [
    (re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret|password|passwd|auth[_-]?token)\s*[:=]\s*['\"]?[^\s'\"]+"), r"\1=[REDACTED]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
]


def redact_secrets(text: str) -> str:
    value = str(text)
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


class JarvisEngine:
    def __init__(self, memory_client: Optional[TencentMemoryClient] = None):
        self.memory = memory_client or TencentMemoryClient()
        try:
            if not self.memory.health():
                logger.warning("MemoryCore Gateway is unavailable at %s", self.memory.base_url)
        except Exception:
            logger.debug("Gateway health check failed during startup", exc_info=True)

    def add_memory(self, user_id: str, text: str, metadata: Optional[Dict] = None) -> Dict:
        safe = redact_secrets(text)[:8000]
        try:
            return {"status": "success", "raw": self.memory.capture(str(user_id), safe, metadata or {})}
        except (MemoryGatewayError, CircuitBreakerOpen) as exc:
            logger.error("Memory capture failed: %s", exc)
            return {"status": "error", "message": str(exc)}

    def search_memory(self, user_id: str, query: str, limit: int = 5) -> list:
        try:
            results = self.memory.recall(str(user_id), query[:2000], limit=limit)
            # Add an explicit provenance marker. Callers must treat memory as data, not instructions.
            if isinstance(results, list):
                wrapped = []
                for item in results:
                    if isinstance(item, dict):
                        item = dict(item)
                        item["_jarvis_trust"] = "untrusted_memory"
                    wrapped.append(item)
                return wrapped
            return results
        except (MemoryGatewayError, CircuitBreakerOpen) as exc:
            logger.error("Memory recall failed: %s", exc)
            return []

    def analyze_and_learn(self, user_id: str, transcript: str) -> Dict:
        transcript = str(transcript)
        turns = self._parse_transcript(transcript)
        if not turns:
            turns = [{"role": "user", "content": transcript[-4000:]}]
        for turn in turns:
            turn["content"] = redact_secrets(turn["content"])
        try:
            result = self.memory.capture(str(user_id), turns, {"type": "session_transcript"})
            return {"status": "success", "captured": result}
        except (MemoryGatewayError, CircuitBreakerOpen) as exc:
            logger.error("Transcript capture failed: %s", exc)
            return {"status": "error", "message": str(exc)}

    def auto_capture_turn(self, user_id: str, role: str, content: str, metadata: Optional[Dict] = None) -> Dict:
        if role not in {"user", "assistant", "system"}:
            return {"status": "error", "message": "role must be user, assistant or system"}
        try:
            meta = {"type": "turn_capture", "role": role}
            if metadata:
                meta.update(metadata)
            result = self.memory.capture(str(user_id), redact_secrets(content)[-2000:], meta)
            return {"status": "success", "captured": result}
        except (MemoryGatewayError, CircuitBreakerOpen) as exc:
            logger.error("Turn capture failed: %s", exc)
            return {"status": "error", "message": str(exc)}

    def _parse_transcript(self, transcript: str) -> List[Dict[str, str]]:
        turns: List[Dict[str, str]] = []
        pattern = re.compile(r"^(User|Human|Assistant|AI|System|Bot|Agent)\s*:\s*", re.I | re.M)
        matches = list(pattern.finditer(transcript))
        if matches:
            if matches[0].start() > 0:
                prefix = transcript[:matches[0].start()].strip()
                if prefix:
                    turns.append({"role": "system", "content": prefix})
            for i, match in enumerate(matches):
                raw = match.group(1).lower()
                role = "user" if raw in {"user", "human"} else "system" if raw == "system" else "assistant"
                end = matches[i + 1].start() if i + 1 < len(matches) else len(transcript)
                content = transcript[match.end():end].strip()
                if content:
                    turns.append({"role": role, "content": content[-2000:]})
            return turns[-20:]
        xml = re.compile(r"<(user|assistant|system|human|ai)>(.*?)</\1>", re.I | re.S)
        found = xml.findall(transcript)
        if found:
            for tag, content in found:
                if content.strip():
                    role = "user" if tag.lower() in {"user", "human"} else "system" if tag.lower() == "system" else "assistant"
                    turns.append({"role": role, "content": content.strip()[-2000:]})
            return turns[-20:]
        return [{"role": "user", "content": transcript.strip()[-4000:]}] if transcript.strip() else []

    def edit_html(self, html: str, selector: str, action: str, content: str = "") -> str:
        try:
            from bs4 import BeautifulSoup
            if action not in {"replace", "append", "remove"}:
                return json.dumps({"error": f"Unknown action '{action}'", "html": html})
            soup = BeautifulSoup(html, "lxml")
            elements = soup.select(selector)
            if not elements:
                return json.dumps({"error": f"Selector '{selector}' not found", "html": html})
            for element in elements:
                if action == "remove":
                    element.decompose()
                else:
                    fragment = BeautifulSoup(content, "lxml")
                    nodes = list(fragment.body.contents) if fragment.body else list(fragment.contents)
                    if action == "replace":
                        element.clear()
                    for node in nodes:
                        element.append(node)
            return json.dumps({"success": True, "modified_count": len(elements), "html": str(soup)})
        except Exception as exc:
            return json.dumps({"error": str(exc), "html": html})
