"""Route-level integration tests for the 1.3.0 limits:
413 on oversize Content-Length, 429 + Retry-After on rate-limit
breach, and the backwards-compat path where no limits are
configured. Runs through TestClient against /bin/echo so the
suite stays self-contained."""

from __future__ import annotations

from fastapi.testclient import TestClient

from url2code.config import AppConfig
from url2code.main import create_app


def _client(app_extra: dict | None = None, endpoint_limits: dict | None = None,
            method: str = "POST") -> TestClient:
    endpoint = {
        "name": "echo",
        "route": "/echo",
        "method": method,
        "command": {"executable": "/bin/echo", "args": ["hi"]},
    }
    if endpoint_limits is not None:
        endpoint["limits"] = endpoint_limits
    data: dict = {"api": {"title": "t", "default_root": "/v1"}, "endpoints": [endpoint]}
    if app_extra:
        data.update(app_extra)
    return TestClient(create_app(AppConfig.model_validate(data)))


def test_no_limits_is_backwards_compatible() -> None:
    client = _client()
    for _ in range(5):
        assert client.post("/v1/echo", json={}).status_code == 200


def test_oversize_body_rejected_with_413() -> None:
    client = _client(endpoint_limits={"max_request_bytes": 10})
    response = client.post("/v1/echo", json={"data": "x" * 100})
    assert response.status_code == 413
    assert "exceeds" in response.json()["detail"]


def test_body_within_limit_passes() -> None:
    client = _client(endpoint_limits={"max_request_bytes": 10})
    assert client.post("/v1/echo", json={}).status_code == 200  # "{}" = 2 bytes


def test_missing_content_length_skips_size_check() -> None:
    # A GET with no body has no Content-Length; the size cap is
    # a no-op rather than a false 413.
    client = _client(endpoint_limits={"max_request_bytes": 10}, method="GET")
    assert client.get("/v1/echo").status_code == 200


def test_rate_limit_returns_429_with_retry_after() -> None:
    client = _client(endpoint_limits={"rate_limit": {"requests": 1, "window_seconds": 60}})
    assert client.post("/v1/echo", json={}).status_code == 200
    blocked = client.post("/v1/echo", json={})
    assert blocked.status_code == 429
    retry_after = {k.lower(): v for k, v in blocked.headers.items()}.get("retry-after")
    assert retry_after is not None
    assert int(retry_after) >= 1


def test_app_level_rate_limit_inherited_by_endpoint() -> None:
    client = _client(app_extra={"limits": {"rate_limit": {"requests": 1, "window_seconds": 60}}})
    assert client.post("/v1/echo", json={}).status_code == 200
    assert client.post("/v1/echo", json={}).status_code == 429
