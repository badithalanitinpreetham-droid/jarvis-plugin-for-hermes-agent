"""
Circuit-breaker behavior is the one piece of resilience logic in
tencent_memory.py that's easy to get subtly wrong (off-by-one on the
failure count, cooldown that never actually resets, etc.) and easy to
verify without a real Gateway — fake the HTTP layer, drive it through
failures and recovery, assert the breaker opens and closes exactly when
it should.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import conftest  # noqa: E402 — stubs httpx if it isn't really installed

from jarvis_memory.tencent_memory import (  # noqa: E402
    TencentMemoryClient,
    MemoryGatewayError,
    CircuitBreakerOpen,
)


class FakeResponse:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}
        self.content = b"x"  # truthy so _request tries to parse json
        self.text = "error body"

    def json(self):
        return self._json_body


class ScriptedTransport:
    """Returns responses/raises in the order given, one per call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def request(self, method, url, headers=None, json=None):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class TestCircuitBreaker(unittest.TestCase):
    def _client_with_transport(self, script):
        client = TencentMemoryClient(base_url="http://fake", api_key="k")
        client._client = ScriptedTransport(script)
        return client

    def test_successful_call_keeps_circuit_closed(self):
        client = self._client_with_transport([FakeResponse(200, {"ok": True})])
        result = client._request("GET", "/health")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(client._consecutive_failures, 0)

    def test_failures_below_threshold_do_not_open_circuit(self):
        script = [FakeResponse(500) for _ in range(TencentMemoryClient.FAILURE_THRESHOLD - 1)]
        client = self._client_with_transport(script)
        for _ in range(TencentMemoryClient.FAILURE_THRESHOLD - 1):
            with self.assertRaises(MemoryGatewayError):
                client._request("GET", "/health")
        self.assertIsNone(client._circuit_opened_at)

    def test_reaching_threshold_opens_circuit(self):
        script = [FakeResponse(500) for _ in range(TencentMemoryClient.FAILURE_THRESHOLD)]
        client = self._client_with_transport(script)
        for _ in range(TencentMemoryClient.FAILURE_THRESHOLD):
            with self.assertRaises(MemoryGatewayError):
                client._request("GET", "/health")
        self.assertIsNotNone(client._circuit_opened_at)

    def test_open_circuit_rejects_calls_without_hitting_transport(self):
        script = [FakeResponse(500) for _ in range(TencentMemoryClient.FAILURE_THRESHOLD)]
        client = self._client_with_transport(script)
        for _ in range(TencentMemoryClient.FAILURE_THRESHOLD):
            with self.assertRaises(MemoryGatewayError):
                client._request("GET", "/health")

        calls_before = client._client.calls
        with self.assertRaises(CircuitBreakerOpen):
            client._request("GET", "/health")
        # The whole point of the breaker: no transport call while open.
        self.assertEqual(client._client.calls, calls_before)

    def test_circuit_closes_again_after_cooldown_elapses(self):
        script = [FakeResponse(500) for _ in range(TencentMemoryClient.FAILURE_THRESHOLD)]
        script.append(FakeResponse(200, {"ok": True}))
        client = self._client_with_transport(script)
        for _ in range(TencentMemoryClient.FAILURE_THRESHOLD):
            with self.assertRaises(MemoryGatewayError):
                client._request("GET", "/health")
        self.assertIsNotNone(client._circuit_opened_at)

        # Simulate the cooldown having elapsed without a real sleep.
        with patch("jarvis_memory.tencent_memory.time.monotonic", return_value=(
            client._circuit_opened_at + TencentMemoryClient.COOLDOWN_SECONDS + 1
        )):
            result = client._request("GET", "/health")
        self.assertEqual(result, {"ok": True})
        self.assertIsNone(client._circuit_opened_at)
        self.assertEqual(client._consecutive_failures, 0)

    def test_health_returns_false_instead_of_raising(self):
        client = self._client_with_transport([FakeResponse(500)])
        self.assertFalse(client.health())


if __name__ == "__main__":
    unittest.main()
