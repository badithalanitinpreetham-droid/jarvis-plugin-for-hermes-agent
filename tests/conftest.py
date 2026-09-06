import sys
import types

try:
    import httpx  # noqa: F401  — real dependency present, nothing to stub
except ImportError:
    fake_httpx = types.ModuleType("httpx")

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            raise RuntimeError(
                "httpx is stubbed out in this offline test sandbox — "
                "these tests never exercise real HTTP calls (TencentMemoryClient "
                "network behavior isn't covered by test_autonomous.py), so this "
                "should never actually be called. If you see this, a test is "
                "reaching further than it should."
            )

        def close(self):
            pass

    class _FakeHTTPError(Exception):
        pass

    fake_httpx.Client = _FakeClient
    fake_httpx.HTTPError = _FakeHTTPError
    sys.modules["httpx"] = fake_httpx
