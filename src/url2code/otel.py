"""Optional OpenTelemetry tracing for url2code.

No-op unless an OTLP endpoint is configured. When
`OTEL_EXPORTER_OTLP_ENDPOINT` (or the traces-specific variant) is
set, `configure_tracing()` installs a `TracerProvider` with an
OTLP/HTTP span exporter; otherwise the global tracer stays the
API's no-op implementation and spans cost almost nothing.

The engine opens a request span per call and a child span around
the CLI execution. Span attributes carry the endpoint name and
the executable — not the full argv, which can contain
request-supplied values.
"""

from __future__ import annotations

import os

from opentelemetry import trace

_TRACER_NAME = "url2code"


def tracing_enabled() -> bool:
    """True when an OTLP endpoint is configured and the SDK isn't
    explicitly disabled. Honours the standard OTEL_* env vars so
    operators configure this the usual way."""
    if os.getenv("OTEL_SDK_DISABLED", "").lower() == "true":
        return False
    return bool(
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    )


def configure_tracing(service_name: str) -> bool:
    """Install an OTLP tracer provider iff tracing is enabled.

    Returns True when a provider was installed, False when left as
    the no-op default. Safe to call once at startup.
    """
    if not tracing_enabled():
        return False
    # SDK + exporter are imported lazily so they're only touched
    # when tracing is actually turned on. This block is external
    # glue (it talks to a collector); excluded from coverage.
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # pragma: no cover
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource  # pragma: no cover
    from opentelemetry.sdk.trace import TracerProvider  # pragma: no cover
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # pragma: no cover

    provider = TracerProvider(  # pragma: no cover
        resource=Resource.create({"service.name": service_name})
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))  # pragma: no cover
    trace.set_tracer_provider(provider)  # pragma: no cover
    return True  # pragma: no cover


def get_tracer():
    """Return the url2code tracer. Before a provider is installed
    this is the API's proxy tracer, which resolves to the real one
    once configure_tracing() runs — so a module-level cache of the
    return value is safe."""
    return trace.get_tracer(_TRACER_NAME)
