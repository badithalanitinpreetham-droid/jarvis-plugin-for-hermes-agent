"""Thread-safe HTTP client for Tencent MemoryCore v3."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Union

import httpx

from .config import CONFIG

logger = logging.getLogger(__name__)


class MemoryGatewayError(Exception):
    pass


class CircuitBreakerOpen(Exception):
    pass


class TencentMemoryClient:
    FAILURE_THRESHOLD = CONFIG.circuit_failure_threshold
    COOLDOWN_SECONDS = CONFIG.circuit_cooldown_seconds

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None, timeout: float = 30.0):
        self.base_url = (base_url or CONFIG.gateway_url).rstrip("/")
        self.api_key = (api_key if api_key is not None else CONFIG.gateway_api_key).strip() or None
        self.service_id = CONFIG.gateway_service_id
        self.team_id = CONFIG.gateway_team_id
        self.agent_id = CONFIG.gateway_agent_id
        self.api_version = CONFIG.gateway_api_version.lower()
        self._client = httpx.Client(timeout=timeout)
        self._consecutive_failures = 0
        self._circuit_lock = threading.Lock()
        self._circuit_opened_at: Optional[float] = None

    def _headers(self, content_type: bool = True) -> Dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key or 'local'}"}
        if content_type:
            headers["Content-Type"] = "application/json"
        if self.service_id:
            headers["x-tdai-service-id"] = self.service_id
        return headers

    def _check_circuit(self) -> None:
        with self._circuit_lock:
            if self._circuit_opened_at is None:
                return
            elapsed = time.monotonic() - self._circuit_opened_at
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
        """Use the same guarded request path as normal MemoryCore traffic."""
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

    def capture(
        self,
        user_id: str,
        data: Union[str, List[Dict[str, Any]]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        turns = [{"role": "user", "content": data}] if isinstance(data, str) else list(data)
        metadata = metadata or {}
        session_id = str(
            metadata.get("session_id")
            or metadata.get("workflow_id")
            or f"profile:{user_id}"
        )
        if self.api_version == "v3":
            result = self._request(
                "POST",
                "/v3/conversation/add",
                {
                    **self._identity(user_id),
                    "session_id": session_id,
                    "messages": turns,
                },
            )
        else:
            result = self._request(
                "POST",
                "/capture",
                {"user_id": user_id, "turns": turns, "metadata": metadata},
            )
        return result if isinstance(result, dict) else {"data": result}

    def recall(self, user_id: str, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        if self.api_version == "v3":
            result = self._request(
                "POST",
                "/v3/atomic/search",
                {**self._identity(user_id), "query": query, "limit": limit},
            )
        else:
            result = self._request(
                "POST",
                "/recall",
                {"user_id": user_id, "query": query, "limit": limit},
            )
        data = result.get("data", result) if isinstance(result, dict) else result
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("results", "memories", "items", "records"):
                if isinstance(data.get(key), list):
                    return [item for item in data[key] if isinstance(item, dict)]
            return [data] if data else []
        return [{"memory": data}] if isinstance(data, str) else []

    def search(self, user_id: str, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Compatibility alias for callers that use a search-shaped API."""
        return self.recall(user_id, query, limit=limit)

    def session_end(self, user_id: str) -> Dict[str, Any]:
        # MemoryCore session lifecycle is optional. Keep this operation local
        # because the gateway's v3 contract does not require a session-end call.
        return {"status": "ok", "user_id": str(user_id), "api_version": self.api_version}

    def close(self) -> None:
        self._client.close()


__all__ = ["TencentMemoryClient", "MemoryGatewayError", "CircuitBreakerOpen"]
