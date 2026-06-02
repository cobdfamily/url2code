"""Tests for the optional OpenTelemetry tracing (1.6.0).

We exercise the enable/disable decision and the no-op default —
not the live OTLP export path (that talks to a collector and
mutates global tracer state, so it's excluded from coverage). The
key behavioural guarantee: with tracing off (the default), spans
are no-ops and requests are unaffected."""

from __future__ import annotations

from fastapi.testclient import TestClient

from url2code import otel
from url2code.config import AppConfig
from url2code.main import create_app


def test_tracing_disabled_by_default(monkeypatch) -> None:
    for var in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_SDK_DISABLED",
    ):
        monkeypatch.delenv(var, raising=False)
    assert otel.tracing_enabled() is False
    # configure_tracing is a no-op (returns False) when disabled.
    assert otel.configure_tracing("svc") is False


def test_otlp_endpoint_enables(monkeypatch) -> None:
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    assert otel.tracing_enabled() is True


def test_traces_specific_endpoint_enables(monkeypatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://c:4318/v1/traces")
    assert otel.tracing_enabled() is True


def test_sdk_disabled_overrides_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert otel.tracing_enabled() is False


def test_get_tracer_supports_spans() -> None:
    tracer = otel.get_tracer()
    assert hasattr(tracer, "start_as_current_span")
    # No-op span still works as a context manager.
    with tracer.start_as_current_span("probe") as span:
        span.set_attribute("k", "v")


def test_requests_unaffected_with_tracing_off() -> None:
    # Spans are no-ops by default; the traced handler must still
    # serve normally.
    app = create_app(AppConfig.model_validate({
        "api": {"title": "t", "default_root": "/v1"},
        "endpoints": [
            {
                "name": "echo",
                "route": "/echo",
                "method": "GET",
                "command": {"executable": "/bin/echo", "args": ["hi"]},
            },
        ],
    }))
    response = TestClient(app).get("/v1/echo")
    assert response.status_code == 200
    assert response.json()["stdout"].strip() == "hi"
