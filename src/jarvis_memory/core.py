import json
import logging
import re
from typing import Dict, Any, Optional, List

from .tencent_memory import TencentMemoryClient, MemoryGatewayError, CircuitBreakerOpen

logger = logging.getLogger(__name__)


class JarvisEngine:
    """
    The core brain of Jarvis Memory.

    Memory storage/recall is delegated entirely to TencentDB Agent
    Memory's MemoryCore Gateway (see tencent_memory.py) — no local
    mem0/Qdrant/Ollama stack anymore. BeautifulSoup-based HTML editing
    is unrelated to memory and is unchanged.
    """

    def __init__(self, memory_client: Optional[TencentMemoryClient] = None):
        self.memory = memory_client or TencentMemoryClient()
        if not self.memory.health():
            logger.warning(
                "MemoryCore Gateway did not respond to /health at %s — "
                "capture/recall calls will fail until it's reachable.",
                self.memory.base_url,
            )

    def add_memory(self, user_id: str, text: str, metadata: Optional[Dict] = None) -> Dict:
        """Store a memory item (fact, correction, workflow event, etc.)."""
        try:
            result = self.memory.capture(user_id, text, metadata or {})
            return {"status": "success", "raw": result}
        except (MemoryGatewayError, CircuitBreakerOpen) as e:
            logger.error(f"capture() failed: {e}")
            return {"status": "error", "message": str(e)}

    def search_memory(self, user_id: str, query: str, limit: int = 5) -> list:
        """Retrieve relevant memories for a profile."""
        try:
            return self.memory.recall(user_id, query, limit=limit)
        except (MemoryGatewayError, CircuitBreakerOpen) as e:
            logger.error(f"recall() failed: {e}")
            return []

    def analyze_and_learn(self, user_id: str, transcript: str) -> Dict:
        """
        Forward a session transcript into MemoryCore for extraction.

        Parses multi-turn transcripts into structured turns instead of
        naively truncating to 4000 chars. MemoryCore's own L0->L1 pipeline
        handles the distillation (using whichever LLM *the Gateway* is
        configured with).
        """
        try:
            turns = self._parse_transcript(transcript)
            if not turns:
                # Fallback: send as single user turn
                turns = [{"role": "user", "content": transcript[-4000:]}]

            result = self.memory.capture(
                user_id,
                turns,
                {"type": "session_transcript"},
            )
            return {"status": "success", "captured": result}
        except (MemoryGatewayError, CircuitBreakerOpen) as e:
            logger.error(f"Self-evolution capture failed: {e}")
            return {"status": "error", "message": str(e)}

    def auto_capture_turn(self, user_id: str, role: str, content: str,
                          metadata: Optional[Dict] = None) -> Dict:
        """Lightweight per-turn incremental capture.

        Instead of waiting for the full session to end, capture each
        significant turn as it happens. This is cheaper and more granular
        than analyze_and_learn() — ideal for long sessions where you don't
        want to lose context if the session is interrupted.
        """
        try:
            meta = {"type": "turn_capture", "role": role}
            if metadata:
                meta.update(metadata)
            result = self.memory.capture(user_id, content[-2000:], meta)
            return {"status": "success", "captured": result}
        except (MemoryGatewayError, CircuitBreakerOpen) as e:
            logger.error(f"Turn capture failed: {e}")
            return {"status": "error", "message": str(e)}

    def _parse_transcript(self, transcript: str) -> List[Dict[str, str]]:
        """Parse a multi-turn transcript into structured turns.

        Supports common formats:
        - "User: message" / "Assistant: message"
        - "Human: message" / "AI: message"
        - "<user>message</user>" / "<assistant>message</assistant>"
        - Plain text (returned as single user turn)
        """
        turns: List[Dict[str, str]] = []

        # Try "Role: message" format
        role_pattern = re.compile(
            r'^(User|Human|Assistant|AI|System|Bot|Agent)\s*:\s*',
            re.IGNORECASE | re.MULTILINE
        )
        matches = list(role_pattern.finditer(transcript))

        if matches:
            for i, match in enumerate(matches):
                raw_role = match.group(1).lower()
                if raw_role in ("user", "human"):
                    role = "user"
                elif raw_role in ("system",):
                    role = "system"
                else:
                    role = "assistant"

                start = match.end()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(transcript)
                content = transcript[start:end].strip()
                if content:
                    turns.append({"role": role, "content": content[-2000:]})
            return turns[-20:]  # Last 20 turns max

        # Try XML-style tags
        xml_pattern = re.compile(
            r'<(user|assistant|system|human|ai)>(.*?)</\1>',
            re.IGNORECASE | re.DOTALL
        )
        xml_matches = xml_pattern.findall(transcript)
        if xml_matches:
            for tag, content in xml_matches:
                tag_lower = tag.lower()
                if tag_lower in ("user", "human"):
                    role = "user"
                elif tag_lower == "system":
                    role = "system"
                else:
                    role = "assistant"
                if content.strip():
                    turns.append({"role": role, "content": content.strip()[-2000:]})
            return turns[-20:]

        # Fallback: plain text as single user turn
        if transcript.strip():
            return [{"role": "user", "content": transcript.strip()[-4000:]}]
        return []

    def edit_html(self, html: str, selector: str, action: str, content: str = "") -> str:
        """
        Visual HTML Editor Logic (Lemon AI Style).
        Actions: 'replace', 'append', 'remove'.
        """
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')
            elements = soup.select(selector)

            if not elements:
                return json.dumps({"error": f"Selector '{selector}' not found", "html": html})

            count = 0
            for el in elements:
                if action == "remove":
                    el.decompose()
                elif action == "replace":
                    fragment = BeautifulSoup(content, 'lxml')
                    new_nodes = fragment.body.contents if fragment.body else fragment.contents
                    el.clear()
                    for node in list(new_nodes):
                        el.append(node)
                elif action == "append":
                    fragment = BeautifulSoup(content, 'lxml')
                    new_nodes = fragment.body.contents if fragment.body else fragment.contents
                    for node in list(new_nodes):
                        el.append(node)
                else:
                    return json.dumps({"error": f"Unknown action '{action}'", "html": html})
                count += 1

            return json.dumps({"success": True, "modified_count": count, "html": str(soup)})

        except Exception as e:
            return json.dumps({"error": str(e), "html": html})
