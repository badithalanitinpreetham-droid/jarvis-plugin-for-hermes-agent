"""Client for TencentDB Agent Memory / MemoryCore Gateway."""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Union

import httpx

logger = logging.getLogger(__name__)


class MemoryGatewayError(RuntimeError):
    pass


class CircuitBreakerOpen(MemoryGatewayError):
    pass


class TencentMemoryClient:
    FAILURE_THRESHOLD = 3
    COOLDOWN_SECONDS = 30.0

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None, api_version: Optional[str] = None):
        self.base_url = (base_url or os.environ.get("TDAI_GATEWAY_URL") or "http://127.0.0.1:8000").rstrip("/")
        self.api_key = api_key if api_key is not None else (os.environ.get("TDAI_GATEWAY_API_KEY") or os.environ.get("TDAI_API_KEY", ""))
        self.api_version = str(api_version or os.environ.get("TDAI_API_VERSION", "v3")).strip() or "v3"
        self.service_id = os.environ.get("TDAI_SERVICE_ID", "jarvis-memory")
        self.team_id = os.environ.get("TDAI_TEAM_ID", "jarvis")
        self.agent_id = os.environ.get("TDAI_AGENT_ID", "jarvis")
        self._client = httpx.Client(timeout=30.0)
        self._circuit_lock = threading.Lock()
        self._consecutive_failures = 0
        self._circuit_opened_at: Optional[float] = None

    def _headers(self, include_api_key: bool = True) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.service_id:
            headers["x-tdai-service-id"] = self.service_id
        if include_api_key and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _check_circuit(self) -> None:
        with self._circuit_lock:
            opened = self._circuit_opened_at
            if opened is None:
                return
            elapsed = time.monotonic() - opened
            if elapsed < self.COOLDOWN_SECONDS:
                raise CircuitBreakerOpen(
                    f"Circuit open — {self.COOLDOWN_SECONDS - elapsed:.0f}s remaining"
                )
            self._circuit_opened_at = None

    def _record_success(self) -> None:
        with self._circuit_lock:
            self._consecutive_failures = 0
            self._circuit_opened_at = None

    def _record_failure(self) -> None:
        with self._circuit_lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.FAILURE_THRESHOLD:
                self._circuit_opened_at = time.monotonic()
                logger.error(
                    "MemoryCore circuit opened after %d failures",
                    self._consecutive_failures,
                )

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        self._check_circuit()
        try:
            response = self._client.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                json=body,
                timeout=timeout,
            )
            if response.status_code >= 400:
                self._record_failure()
                raise MemoryGatewayError(
                    f"{method} {path} -> HTTP {response.status_code}: {response.text[:500]}"
                )
            try:
                result = response.json() if response.content else {}
            except ValueError:
                result = {"raw_text": response.text}
            if isinstance(result, dict) and result.get("code") not in (None, 0, "0"):
                self._record_failure()
                raise MemoryGatewayError(
                    f"{method} {path} -> code={result.get('code')}: {result.get('message', 'unknown')}"
                )
            self._record_success()
            return result
        except CircuitBreakerOpen:
            raise
        except httpx.HTTPError as exc:
            self._record_failure()
            raise MemoryGatewayError(f"{method} {path} failed: {exc}") from exc

    def _identity(self, user_id: str) -> Dict[str, str]:
        return {"team_id": self.team_id, "agent_id": self.agent_id, "user_id": str(user_id)}

    def health(self) -> bool:
        """Run health through the same circuit/error semantics as all other requests."""
        try:
            self._request("GET", "/health", timeout=2.0)
            return True
        except (CircuitBreakerOpen, MemoryGatewayError):
            return False

    def status(self) -> Dict[str, Any]:
        with self._circuit_lock:
            opened, failures = self._circuit_opened_at, self._consecutive_failures
        remaining = 0.0 if opened is None else max(
            0.0, self.COOLDOWN_SECONDS - (time.monotonic() - opened)
        )
        return {
            "base_url": self.base_url,
            "api_version": self.api_version,
            "service_id": self.service_id,
            "team_id": self.team_id,
            "agent_id": self.agent_id,
            "healthy": self.health() if opened is None else False,
            "circuit_open": opened is not None,
            "cooldown_remaining_seconds": round(remaining, 1),
            "consecutive_failures": failures,
        }

    def capture(self, user_id: str, data: Union[str, List[Dict[str, Any]]], metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        turns = [{"role": "user", "content": data}] if isinstance(data, str) else list(data)
        metadata = metadata or {}
        session_id = str(metadata.get("session_id") or metadata.get("workflow_id") or f"profile:{user_id}")
        if self.api_version == "v3":
            result = self._request(
                "POST",
                "/v3/conversation/add",
                {**self._identity(user_id), "session_id": session_id, "messages": turns},
            )
        else:
            result = self._request(
                "POST",
                "/capture",
                {"user_id": user_id, "turns": turns, "metadata": metadata},
            )
        return result if isinstance(result, dict) else {"data": result}

    def search(self, user_id: str, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        if self.api_version == "v3":
            result = self._request(
                "POST",
                "/v3/memory/search",
                {**self._identity(user_id), "query": query, "limit": max(1, int(limit))},
            )
        else:
            result = self._request(
                "POST",
                "/search",
                {"user_id": user_id, "query": query, "limit": max(1, int(limit))},
            )
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if isinstance(result, dict):
            memories = result.get("memories", result.get("data", []))
            return [item for item in memories if isinstance(item, dict)] if isinstance(memories, list) else []
        return []

    def analyze_and_learn(self, user_id: str, transcript: str) -> Dict[str, Any]:
        return self.capture(user_id, transcript, {"type": "session_transcript"})

    def auto_capture_turn(self, user_id: str, role: str, content: str) -> Dict[str, Any]:
        return self.capture(user_id, [{"role": role, "content": content}], {"type": "turn"})

    def session_end(self, user_id: str) -> Dict[str, Any]:
        return self.capture(user_id, [], {"type": "session_end"})

    def add_memory(self, user_id: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.capture(user_id, content, metadata or {"type": "memory"})


__all__ = ["TencentMemoryClient", "MemoryGatewayError", "CircuitBreakerOpen"]
