"""
Client for TencentDB Agent Memory's MemoryCore Gateway.

Replaces the old mem0 + Qdrant + local-Ollama stack entirely:
- MemoryCore runs as its own long-lived service (its Quick Start docs show
  it listening on 127.0.0.1:8420 by default, backed by SQLite + local
  files) and does its own conversation -> memory distillation (L0 raw
  turns -> L1 atomic facts -> L2 scenes -> L3 personas) using whatever LLM
  *it* is configured with (TDAI_LLM_API_KEY / TDAI_LLM_BASE_URL /
  TDAI_LLM_MODEL on the Gateway side, not this process).
- That means jarvis-memory no longer needs to run its own critic-loop LLM
  call to extract facts from a transcript — see analyze_and_learn() in
  core.py, which now just forwards the transcript and lets the Gateway's
  own pipeline do the extraction.

IMPORTANT — VERIFY BEFORE PRODUCTION USE:
The public docs for this project describe the endpoints referenced below
(/capture, /recall, /session/end, /health) and the auth/isolation model
(Bearer token, per-tenant isolation, circuit breaker after repeated
failures) but the full request/response JSON schema was not fully
documented in what I could pull. The shapes below (`user_id` as the
tenant/profile key, `metadata` passed through on capture) are a
reasonable mapping onto jarvis-memory's existing profile_id concept, but
confirm field names against MemoryCore's actual OpenAPI/gateway docs
before relying on this in a live pipeline — a mismatched field name will
fail loudly (see CircuitBreakerOpen / non-2xx handling below) rather than
silently, but better to catch it once at setup than mid-run.
"""

import logging
import os
import time
import threading
from typing import Any, Dict, List, Optional, Union

import httpx

from .config import CONFIG

logger = logging.getLogger(__name__)


class MemoryGatewayError(Exception):
    """Raised for any non-2xx response from the MemoryCore Gateway."""


class CircuitBreakerOpen(Exception):
    """Raised when the circuit breaker is tripped — Gateway calls are paused."""


class TencentMemoryClient:
    """
    Thin HTTP client for the MemoryCore Gateway's v2 REST API.

    Mirrors the resilience behavior the project's own v2 Hermes plugin
    documents for itself: Bearer token auth, per-tenant isolation via
    `user_id`, and a circuit breaker that opens after 5 consecutive
    failures and cools down for 60s before allowing another attempt.
    """

    FAILURE_THRESHOLD = CONFIG.circuit_failure_threshold
    COOLDOWN_SECONDS = CONFIG.circuit_cooldown_seconds

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.base_url = (base_url or CONFIG.gateway_url).rstrip("/")
        self.api_key = api_key or CONFIG.gateway_api_key or None
        self._client = httpx.Client(timeout=timeout)
        self._consecutive_failures = 0
        self._circuit_lock = threading.Lock()
        self._circuit_opened_at: Optional[float] = None

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _check_circuit(self):
        with self._circuit_lock:
            if self._circuit_opened_at is None:
                return
            elapsed = time.monotonic() - self._circuit_opened_at
            if elapsed < self.COOLDOWN_SECONDS:
                raise CircuitBreakerOpen(
                    f"Circuit open — {self.COOLDOWN_SECONDS - elapsed:.0f}s remaining in cooldown"
                )
            # Cooldown elapsed: allow a trial request through (half-open).
            # We do NOT reset _consecutive_failures yet — if this trial fails,
            # _record_failure will immediately trip the circuit open again.
            self._circuit_opened_at = None

    def _record_success(self):
        with self._circuit_lock:
            self._consecutive_failures = 0
            self._circuit_lock = threading.Lock()
            self._circuit_opened_at = None

    def _record_failure(self):
        with self._circuit_lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.FAILURE_THRESHOLD:
                self._circuit_opened_at = time.monotonic()
                logger.error("Circuit breaker OPEN after %d consecutive Gateway failures", self._consecutive_failures)

    def _request(self, method: str, path: str, json_body: Optional[Dict] = None) -> Dict[str, Any]:
        self._check_circuit()
        url = f"{self.base_url}{path}"
        try:
            resp = self._client.request(method, url, headers=self._headers(), json=json_body)
            if resp.status_code >= 400:
                self._record_failure()
                raise MemoryGatewayError(f"{method} {path} -> {resp.status_code}: {resp.text[:500]}")
            self._record_success()
            if resp.content:
                try:
                    return resp.json()
                except Exception:
                    return {"raw_text": resp.text}
            return {}
        except httpx.HTTPError as e:
            self._record_failure()
            raise MemoryGatewayError(f"{method} {path} failed: {e}") from e

    def health(self) -> bool:
        try:
            resp = self._client.get(f"{self.base_url}/health", headers=self._headers(), timeout=2.0)
            if resp.status_code == 200:
                self._record_success()
                return True
            return False
        except httpx.HTTPError:
            return False

    def status(self) -> Dict[str, Any]:
        """Aggregate state for a health/monitoring surface — not itself
        called on every request, so it's safe to poll from a jarvis_health tool."""
        circuit_open = self._circuit_opened_at is not None
        cooldown_remaining = 0.0
        if circuit_open:
            cooldown_remaining = max(0.0, self.COOLDOWN_SECONDS - (time.monotonic() - self._circuit_opened_at))
        return {
            "base_url": self.base_url,
            "healthy": self.health() if not circuit_open else False,
            "circuit_open": circuit_open,
            "cooldown_remaining_seconds": round(cooldown_remaining, 1),
            "consecutive_failures": self._consecutive_failures,
        }

    def capture(self, user_id: str, data: Union[str, List[Dict]], metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Push a conversation turn / memory item into the Gateway. MemoryCore's
        own L0->L1 pipeline decides what's worth distilling into durable
        facts (subject to its `everyNConversations` batching threshold —
        set that to 1 on the Gateway config if you need synchronous,
        deterministic capture rather than batched extraction).
        """
        if isinstance(data, str):
            turns = [{"role": "user", "content": data}]
        else:
            turns = data
            
        body = {
            "user_id": user_id,
            "turns": turns,
            "metadata": metadata or {},
        }
        return self._request("POST", "/capture", body)

    def recall(self, user_id: str, query: str, limit: int = 5) -> List[Dict]:
        body = {"user_id": user_id, "query": query, "limit": limit}
        result = self._request("POST", "/recall", body)
        # Normalize: the Gateway may return a bare list or a {"results": [...]}
        # envelope depending on version — handle both defensively, same
        # lesson as the mem0 response-shape drift this replaced.
        if isinstance(result, list):
            return result
        elif isinstance(result, dict):
            return result.get("results", []) or result.get("memories", []) or []
        elif isinstance(result, str):
            return [{"memory": result}]
        return []

    def session_end(self, user_id: str) -> Dict[str, Any]:
        """Drain any in-flight extraction for this profile/session."""
        return self._request("POST", "/session/end", {"user_id": user_id})

    def close(self):
        self._client.close()
