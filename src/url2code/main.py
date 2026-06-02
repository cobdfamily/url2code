"""url2code FastAPI app.

Boot flow:
  1. Read YAML config from URL2CODE_CONFIG (or fall
     back to config/tools.yaml).
  2. For each EndpointConfig declared there, register
     the HTTP route + any download routes for its
     output_files placeholders.
  3. Each request lands in parse_request() (body /
     query / file uploads), then execute_endpoint()
     (subprocess + parser), then ToolResponse.

URL2CODE_CONFIG path is the operator's hook -- one
url2code image, many YAML configs, one container per
tool surface.
"""

from __future__ import annotations

import math
import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .config import (
    AppConfig,
    EndpointConfig,
    LimitsConfig,
    build_full_path,
    effective_limits,
    load_config,
    missing_executables,
    summarize_config,
)
from .executor import execute_endpoint, request_template_values
from .logging_config import configure_logging
from .metrics import Metrics
from .models import ToolResponse
from .otel import configure_tracing, get_tracer
from .ratelimit import RateLimiter, client_ip
from .request_parser import parse_request
from .templating import TemplateRenderError, render_template

CONFIG_ENV_VAR = "URL2CODE_CONFIG"
DEFAULT_CONFIG_PATH = "config/tools.yaml"
logger = logging.getLogger("cli_api")

# Module-level tracer. get_tracer() returns the OTel API proxy
# tracer, which resolves to the real provider once
# configure_tracing() installs one (or stays no-op if tracing is
# off), so caching it here is safe.
_tracer = get_tracer()


def build_output_download_path(endpoint_path: str, output_placeholder: str, filename: str = "{filename}") -> str:
    return f"{endpoint_path.rstrip('/')}/downloads/{output_placeholder}/{filename}"


def register_download_routes(app: FastAPI, endpoint: EndpointConfig, endpoint_path: str) -> dict[str, str]:
    download_templates: dict[str, str] = {}
    output_file_lookup = {output_file.placeholder: output_file for output_file in endpoint.output_files}

    if not output_file_lookup:
        return download_templates

    async def download_output(output_placeholder: str, filename: str) -> FileResponse:
        output_file = output_file_lookup.get(output_placeholder)
        if output_file is None:
            raise HTTPException(status_code=404, detail="unknown output file")

        if Path(filename).name != filename:
            raise HTTPException(status_code=404, detail="invalid filename")

        output_path = Path(output_file.output_dir) / filename
        if not output_path.is_file():
            raise HTTPException(status_code=404, detail="output file not found")

        return FileResponse(path=output_path, filename=filename)

    route_path = build_output_download_path(endpoint_path, "{output_placeholder}", "{filename}")
    app.add_api_route(
        path=route_path,
        endpoint=download_output,
        methods=["GET"],
        name=f"{endpoint.name}-download",
        description=f"Download generated output files for {endpoint.name}.",
    )

    for placeholder in output_file_lookup:
        download_templates[placeholder] = build_output_download_path(endpoint_path, placeholder)

    return download_templates

def _build_template_context(
    endpoint: EndpointConfig,
    tool_request,
    response: ToolResponse,
) -> dict:
    """Assemble the run-context dict the response template
    resolves paths against. The two big subtrees are
    ``parsed_output`` (whatever the YAML's regex / native_json
    parser produced) and ``request`` (the same defaults +
    overrides + flag values that drove the CLI args). The
    flat top-level keys mirror ToolResponse fields so a
    template can lift `duration_ms` or `exit_code` straight
    into its response shape if it wants to.

    `static` is whatever the YAML's `output.template_static`
    declares -- a place to park OData / Redfish boilerplate
    that's identical per request and shouldn't bloat
    `parsed_output`.
    """
    return {
        "parsed_output": response.parsed_output,
        "stdout":        response.stdout,
        "stderr":        response.stderr,
        "duration_ms":   response.duration_ms,
        "exit_code":     response.exit_code,
        "endpoint":      response.endpoint,
        "command":       response.command,
        "request":       request_template_values(
                              endpoint, tool_request),
        "static":        endpoint.output.template_static,
    }


def register_endpoint(
    app: FastAPI,
    endpoint: EndpointConfig,
    default_root: str,
    app_limits: LimitsConfig | None = None,
    limiter: RateLimiter | None = None,
) -> None:
    path = build_full_path(default_root, endpoint)
    download_templates = register_download_routes(app, endpoint, path)
    has_template = endpoint.output.template is not None
    # Resolve the effective limits once at registration — config
    # is static, so there's no need to recompute per request.
    endpoint_limits = (
        effective_limits(app_limits, endpoint) if app_limits is not None else LimitsConfig()
    )
    rate_limit = endpoint_limits.rate_limit

    async def handler(request: Request):
        # Metrics (1.5.0) + tracing (1.6.0). Counters/timing live on
        # app.state.metrics; a request span (with a child cli.execute
        # span) is opened on the module tracer — a no-op unless an
        # OTLP endpoint is configured.
        metrics: Metrics = request.app.state.metrics
        metrics.inc_inflight(endpoint.name)
        status_code = 200
        duration_seconds: float | None = None
        with _tracer.start_as_current_span(f"{endpoint.method} {path}") as span:
            span.set_attribute("url2code.endpoint", endpoint.name)
            span.set_attribute("http.request.method", endpoint.method)
            try:
                # Abuse-resistance gates (1.3.0). Evaluated before the
                # request body is read, so an oversize upload is
                # rejected with 413 before it ever touches disk.
                if endpoint_limits.max_request_bytes is not None:
                    content_length = request.headers.get("content-length")
                    if content_length is not None:
                        try:
                            declared = int(content_length)
                        except ValueError:
                            declared = None
                        if declared is not None and declared > endpoint_limits.max_request_bytes:
                            raise HTTPException(
                                status_code=413,
                                detail=(
                                    f"request body {declared} bytes exceeds the "
                                    f"{endpoint_limits.max_request_bytes}-byte limit "
                                    f"for endpoint '{endpoint.name}'"
                                ),
                            )
                if rate_limit is not None and limiter is not None:
                    allowed, retry_after = limiter.check(
                        f"{endpoint.name}:{client_ip(request)}",
                        rate_limit.requests,
                        rate_limit.window_seconds,
                    )
                    if not allowed:
                        # Retry-After is whole seconds, min 1 — a 0
                        # would invite an immediate retry that bounces.
                        raise HTTPException(
                            status_code=429,
                            detail=f"rate limit exceeded for endpoint '{endpoint.name}'",
                            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
                        )
                tool_request, uploads = await parse_request(request, endpoint)
                # Route path params (e.g. /Systems/{id}) become command
                # placeholders + response-template values. Starlette
                # captures them on the route match even though this
                # handler doesn't declare them in its signature.
                if request.path_params:
                    tool_request = tool_request.model_copy(
                        update={"path_params": {k: str(v) for k, v in request.path_params.items()}}
                    )
                with _tracer.start_as_current_span("cli.execute") as cli_span:
                    # Executable only — never the full argv, which can
                    # carry request-supplied values.
                    cli_span.set_attribute("cli.executable", endpoint.command.executable)
                    response = execute_endpoint(
                        endpoint, tool_request, uploads, download_templates,
                    )
                    cli_span.set_attribute("cli.exit_code", response.exit_code)
                duration_seconds = response.duration_ms / 1000.0
                if not has_template:
                    # Classic shape: FastAPI serializes the
                    # ToolResponse via the registered response_model.
                    return response
                # Templated shape: render against the run context and
                # ship the result with the configured Content-Type.
                # A path miss is a 500 with both the template error
                # AND the raw envelope, so operators can see what
                # the CLI actually returned alongside the mismatch.
                context = _build_template_context(
                    endpoint, tool_request, response,
                )
                try:
                    body = render_template(endpoint.output.template, context)
                except TemplateRenderError as exc:
                    logger.error(
                        "Response template rendering failed",
                        extra={
                            "endpoint": endpoint.name,
                            "error": str(exc),
                            "status_code": 500,
                        },
                    )
                    raise HTTPException(
                        status_code=500,
                        detail={
                            "message": "response template rendering failed",
                            "error": str(exc),
                            "envelope": response.model_dump(),
                        },
                    ) from exc
                return JSONResponse(
                    content=body,
                    media_type=endpoint.output.template_content_type,
                )
            except HTTPException as exc:
                status_code = exc.status_code
                raise
            except Exception:
                # Any unhandled error surfaces as a 500 to the client;
                # record it as such before re-raising.
                status_code = 500
                raise
            finally:
                span.set_attribute("http.response.status_code", status_code)
                metrics.dec_inflight(endpoint.name)
                metrics.inc_request(endpoint.name, status_code)
                if duration_seconds is not None:
                    metrics.observe_duration(endpoint.name, duration_seconds)

    # Endpoints with a template have a free-form response
    # shape, so the ToolResponse response_model would either
    # double-validate (and fail) or strip fields. Skip the
    # response_model on those; FastAPI's OpenAPI for those
    # routes lands as `application/json` (or the custom
    # Content-Type) with no schema, which is honest -- the
    # shape is whatever the YAML template says it is.
    add_route_kwargs: dict = {
        "path":        path,
        "endpoint":    handler,
        "methods":     [endpoint.method],
        "name":        endpoint.name,
        "description": endpoint.description,
    }
    if not has_template:
        add_route_kwargs["response_model"] = ToolResponse
    app.add_api_route(**add_route_kwargs)
    logger.info(
        "Registered endpoint",
        extra={
            "endpoint":    endpoint.name,
            "route":       path,
            "templated":   has_template,
            "status_code": 200,
        },
    )


ENGINE_VERSION = "1.7.0"
"""Hard-coded url2code engine version.

Surfaced on / liveness when no api.version is set in the
YAML. Downstream images (cobdfamily/needle, etc.) override
this by setting their own api.version so the liveness
response carries the consumer's identity.
"""


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Startup: config is already loaded at import; nothing to warm.
    yield
    # Shutdown: uvicorn has stopped accepting new connections and is
    # draining in-flight requests (up to timeout_graceful_shutdown,
    # set from URL2CODE_DRAIN_SECONDS in run()). Leave a breadcrumb
    # so a rolling deploy is visible in the logs.
    logger.info("url2code draining and shutting down", extra={"status_code": 200})


def create_app(config: AppConfig) -> FastAPI:
    # api.version is Optional[str]; None means "use the engine
    # version". Downstream images bump api.version per release
    # so / liveness reports the consumer's own tag, not url2code's.
    reported_version = config.api.version or ENGINE_VERSION
    app = FastAPI(
        title=config.api.title,
        version=reported_version,
        redoc_url="/redocs",
        lifespan=_lifespan,
    )

    # One token-bucket limiter shared across the app's endpoints;
    # buckets are keyed per (endpoint, client IP) inside it.
    limiter = RateLimiter()
    app.state.rate_limiter = limiter
    # One metrics registry, shared across endpoints and read by
    # the /metrics route below.
    app.state.metrics = Metrics()

    @app.get("/", tags=["Health"])
    async def root() -> dict[str, str]:
        # ``service`` echoes the YAML's api.title so a downstream
        # image (eg. cobdfamily/needle) reports its own identity in
        # the liveness response, not "url2code". The title is
        # required by FastAPI's OpenAPI assembly (asserts non-empty
        # at app construction), so this is always a real string.
        return {
            "service": config.api.title,
            "status": "ok",
            "version": app.version,
        }

    @app.get("/readyz", tags=["Health"])
    async def readyz() -> dict[str, object]:
        # Readiness is not liveness. `/` being 200 only says the
        # process is up; it says nothing about whether the wrapped
        # CLIs were actually installed in this image. /readyz probes
        # each endpoint's executable and returns 503 (with the
        # missing names) if any is absent, so an orchestrator keeps
        # the instance out of rotation rather than routing traffic
        # that would 500 on first use.
        missing = missing_executables(config)
        if missing:
            raise HTTPException(
                status_code=503,
                detail={"status": "not ready", "missing": missing},
            )
        return {"status": "ready", "checked": len(config.endpoints)}

    @app.get("/metrics", tags=["Health"])
    async def metrics_endpoint() -> Response:
        # Prometheus text exposition (format version 0.0.4). Plain
        # text, so it's fully screen-reader / CLI friendly — no
        # dashboard required. Gate it at the reverse proxy like the
        # rest of the surface.
        return Response(
            content=app.state.metrics.render(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    for endpoint in config.endpoints:
        register_endpoint(app, endpoint, config.api.default_root, config.limits, limiter)

    return app


def build_application() -> FastAPI:
    config_path = os.getenv(CONFIG_ENV_VAR, DEFAULT_CONFIG_PATH)
    config = load_config(config_path)
    configure_logging(config.logging.level)
    # Record which engine build is actually running. engine_version
    # is the hard-coded ENGINE_VERSION; reported_version is what /
    # liveness will report (the consumer's api.version when set, else
    # the engine version). Lets an operator confirm a downstream
    # image's base from the logs, not just the liveness probe.
    logger.info(
        "url2code engine starting",
        extra={
            "status_code": 200,
            "engine_version": ENGINE_VERSION,
            "reported_version": config.api.version or ENGINE_VERSION,
            "service": config.api.title,
        },
    )
    if configure_tracing(config.api.title):
        logger.info("OpenTelemetry tracing enabled", extra={"status_code": 200})
    logger.info(
        "Loaded configuration",
        extra={"status_code": 200, "config_summary": summarize_config(config)},
    )
    return create_app(config)


app = build_application()


def run() -> None:
    """Console-script entrypoint (`uv run url2code`)."""
    import uvicorn
    host = os.getenv("URL2CODE_HOST", "0.0.0.0")
    port = int(os.getenv("URL2CODE_PORT", "8000"))
    # Graceful drain window for rolling deploys: uvicorn stops
    # accepting new requests on SIGTERM, then waits up to this many
    # seconds for in-flight CLI runs to finish before exiting.
    drain = int(os.getenv("URL2CODE_DRAIN_SECONDS", "30"))
    uvicorn.run(
        "url2code.main:app",
        host=host,
        port=port,
        reload=False,
        timeout_graceful_shutdown=drain,
    )
