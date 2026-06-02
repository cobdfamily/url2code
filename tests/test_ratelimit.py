"""Unit tests for the token-bucket limiter, the client-IP
helper, and the effective-limits resolver. All pure -- no
TestClient, no subprocess. Time is injected via `now` so the
refill math is deterministic."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from url2code.config import AppConfig, LimitsConfig, RateLimitConfig, effective_limits
from url2code.ratelimit import RateLimiter, client_ip


def test_first_request_starts_full_and_is_allowed() -> None:
    rl = RateLimiter()
    allowed, retry = rl.check("k", capacity=3, window_seconds=60.0, now=0.0)
    assert allowed is True
    assert retry == 0.0


def test_depletes_then_denies_with_retry_after() -> None:
    rl = RateLimiter()
    # capacity 2 over 2s -> 1 token/sec refill.
    assert rl.check("k", 2, 2.0, now=0.0)[0] is True   # tokens 2 -> 1
    assert rl.check("k", 2, 2.0, now=0.0)[0] is True   # tokens 1 -> 0
    allowed, retry = rl.check("k", 2, 2.0, now=0.0)     # empty
    assert allowed is False
    # one token refills in exactly 1s at 1 tok/s.
    assert retry == pytest.approx(1.0, rel=1e-6)


def test_refills_over_elapsed_time() -> None:
    rl = RateLimiter()
    assert rl.check("k", 1, 1.0, now=0.0)[0] is True    # spend the only token
    assert rl.check("k", 1, 1.0, now=0.0)[0] is False   # still empty
    allowed, retry = rl.check("k", 1, 1.0, now=1.0)      # 1s later: 1 token back
    assert allowed is True
    assert retry == 0.0


def test_does_not_overfill_past_capacity() -> None:
    rl = RateLimiter()
    rl.check("k", 2, 2.0, now=0.0)  # tokens 2 -> 1
    # A long idle must not bank more than `capacity` tokens.
    a1 = rl.check("k", 2, 2.0, now=100.0)[0]
    a2 = rl.check("k", 2, 2.0, now=100.0)[0]
    a3 = rl.check("k", 2, 2.0, now=100.0)[0]
    assert (a1, a2, a3) == (True, True, False)


def test_keys_are_independent() -> None:
    rl = RateLimiter()
    assert rl.check("a", 1, 60.0, now=0.0)[0] is True
    assert rl.check("a", 1, 60.0, now=0.0)[0] is False
    # A different key has its own full bucket.
    assert rl.check("b", 1, 60.0, now=0.0)[0] is True


def test_zero_capacity_denies_all() -> None:
    rl = RateLimiter()
    allowed, retry = rl.check("k", 0, 30.0, now=0.0)
    assert allowed is False
    assert retry == 30.0


class _FakeClient:
    def __init__(self, host: str | None) -> None:
        self.host = host


class _FakeRequest:
    def __init__(self, headers: dict, client: _FakeClient | None = None) -> None:
        self.headers = headers
        self.client = client


def test_client_ip_prefers_left_most_forwarded_for() -> None:
    req = _FakeRequest({"x-forwarded-for": "203.0.113.7, 10.0.0.1"}, _FakeClient("10.0.0.1"))
    assert client_ip(req) == "203.0.113.7"


def test_client_ip_falls_back_to_socket_peer() -> None:
    req = _FakeRequest({}, _FakeClient("198.51.100.4"))
    assert client_ip(req) == "198.51.100.4"


def test_client_ip_unknown_without_client() -> None:
    assert client_ip(_FakeRequest({}, None)) == "unknown"


def _endpoint(limits: dict | None = None) -> dict:
    ep = {
        "name": "e",
        "route": "/e",
        "method": "GET",
        "command": {"executable": "/bin/echo", "args": ["x"]},
    }
    if limits is not None:
        ep["limits"] = limits
    return ep


def test_effective_limits_inherits_app_default_when_endpoint_unset() -> None:
    cfg = AppConfig.model_validate({
        "limits": {"max_request_bytes": 1000, "rate_limit": {"requests": 5}},
        "endpoints": [_endpoint()],
    })
    eff = effective_limits(cfg.limits, cfg.endpoints[0])
    assert eff.max_request_bytes == 1000
    assert eff.rate_limit is not None and eff.rate_limit.requests == 5


def test_effective_limits_overrides_field_by_field() -> None:
    cfg = AppConfig.model_validate({
        "limits": {"max_request_bytes": 1000, "rate_limit": {"requests": 5}},
        "endpoints": [_endpoint({"rate_limit": {"requests": 1, "window_seconds": 10}})],
    })
    eff = effective_limits(cfg.limits, cfg.endpoints[0])
    # rate_limit overridden, max_request_bytes inherited.
    assert eff.max_request_bytes == 1000
    assert eff.rate_limit is not None
    assert eff.rate_limit.requests == 1
    assert eff.rate_limit.window_seconds == 10


def test_no_limits_anywhere_is_the_default() -> None:
    cfg = AppConfig.model_validate({"endpoints": [_endpoint()]})
    eff = effective_limits(cfg.limits, cfg.endpoints[0])
    assert eff.max_request_bytes is None
    assert eff.rate_limit is None


def test_config_rejects_nonpositive_values() -> None:
    with pytest.raises(ValidationError):
        RateLimitConfig(requests=0)
    with pytest.raises(ValidationError):
        RateLimitConfig(requests=5, window_seconds=0)
    with pytest.raises(ValidationError):
        LimitsConfig(max_request_bytes=0)
