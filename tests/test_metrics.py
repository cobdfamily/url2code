"""Tests for the hand-rolled Prometheus metrics (1.5.0).

Unit-level: the Metrics registry's counters / gauge / histogram
and its text rendering. Route-level: /metrics exposition and the
handler instrumentation (a request is counted by status, timed,
and leaves in-flight back at zero)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from url2code.config import AppConfig
from url2code.main import create_app
from url2code.metrics import Metrics


def test_inc_request_counts_by_endpoint_and_status() -> None:
    m = Metrics()
    m.inc_request("e", 200)
    m.inc_request("e", 200)
    m.inc_request("e", 502)
    out = m.render()
    assert 'url2code_requests_total{endpoint="e",status="200"} 2' in out
    assert 'url2code_requests_total{endpoint="e",status="502"} 1' in out


def test_inflight_gauge_increments_and_decrements() -> None:
    m = Metrics()
    m.inc_inflight("e")
    m.inc_inflight("e")
    m.dec_inflight("e")
    assert 'url2code_in_flight_requests{endpoint="e"} 1' in m.render()


def test_duration_histogram_buckets_sum_and_count() -> None:
    m = Metrics()
    m.observe_duration("e", 0.1)
    m.observe_duration("e", 1.0)
    out = m.render()
    # 0.1 lands in le>=0.1; 1.0 lands in le>=1. le="1" is the
    # trimmed form of 1.0 (see _format_float).
    assert 'url2code_request_duration_seconds_bucket{endpoint="e",le="0.05"} 0' in out
    assert 'url2code_request_duration_seconds_bucket{endpoint="e",le="0.1"} 1' in out
    assert 'url2code_request_duration_seconds_bucket{endpoint="e",le="1"} 2' in out
    assert 'url2code_request_duration_seconds_bucket{endpoint="e",le="+Inf"} 2' in out
    assert 'url2code_request_duration_seconds_count{endpoint="e"} 2' in out
    assert 'url2code_request_duration_seconds_sum{endpoint="e"} 1.1' in out


def test_render_emits_help_and_type_lines() -> None:
    m = Metrics()
    m.inc_request("e", 200)
    out = m.render()
    assert "# TYPE url2code_requests_total counter" in out
    assert "# TYPE url2code_in_flight_requests gauge" in out
    assert "# TYPE url2code_request_duration_seconds histogram" in out


def _client(endpoint_extra: dict | None = None) -> TestClient:
    endpoint = {
        "name": "echo",
        "route": "/echo",
        "method": "GET",
        "command": {"executable": "/bin/echo", "args": ["hi"]},
    }
    if endpoint_extra:
        endpoint.update(endpoint_extra)
    return TestClient(create_app(AppConfig.model_validate({
        "api": {"title": "t", "default_root": "/v1"},
        "endpoints": [endpoint],
    })))


def test_metrics_endpoint_serves_prometheus_text() -> None:
    response = _client().get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE url2code_requests_total counter" in response.text


def test_request_is_counted_timed_and_inflight_returns_to_zero() -> None:
    client = _client()
    assert client.get("/v1/echo").status_code == 200
    body = client.get("/metrics").text
    assert 'url2code_requests_total{endpoint="echo",status="200"} 1' in body
    assert 'url2code_request_duration_seconds_count{endpoint="echo"} 1' in body
    assert 'url2code_in_flight_requests{endpoint="echo"} 0' in body


def test_failed_request_counted_with_its_status() -> None:
    client = _client({"limits": {"rate_limit": {"requests": 1, "window_seconds": 60}}})
    assert client.get("/v1/echo").status_code == 200
    assert client.get("/v1/echo").status_code == 429
    body = client.get("/metrics").text
    assert 'url2code_requests_total{endpoint="echo",status="200"} 1' in body
    assert 'url2code_requests_total{endpoint="echo",status="429"} 1' in body
