from __future__ import annotations

import os
import logging

from fastapi import FastAPI, Request

from .config import AppConfig, EndpointConfig, load_config
from .executor import execute_endpoint
from .logging_config import configure_logging
from .models import ToolResponse
from .request_parser import parse_request

CONFIG_ENV_VAR = "URL2CODE_CONFIG"
DEFAULT_CONFIG_PATH = "config/tools.yaml"
logger = logging.getLogger("cli_api")


def _normalize_root(root: str) -> str:
    if not root or root == "/":
        return ""
    return "/" + root.strip("/")


def _normalize_route(route: str) -> str:
    return "/" + route.strip("/")


def _full_path(default_root: str, endpoint: EndpointConfig) -> str:
    root = endpoint.root if endpoint.root is not None else default_root
    normalized_root = _normalize_root(root)
    normalized_route = _normalize_route(endpoint.route)
    return normalized_route if not normalized_root else f"{normalized_root}{normalized_route}"


def register_endpoint(app: FastAPI, endpoint: EndpointConfig, default_root: str) -> None:
    path = _full_path(default_root, endpoint)

    async def handler(request: Request) -> ToolResponse:
        tool_request, uploads = await parse_request(request, endpoint)
        return execute_endpoint(endpoint, tool_request, uploads)

    app.add_api_route(
        path=path,
        endpoint=handler,
        methods=[endpoint.method],
        name=endpoint.name,
        description=endpoint.description,
        response_model=ToolResponse,
    )
    logger.info(
        "Registered endpoint",
        extra={"endpoint": endpoint.name, "route": path, "status_code": 200},
    )


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title=config.api.title, version=config.api.version)

    @app.get("/healthz", tags=["system"])
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    for endpoint in config.endpoints:
        register_endpoint(app, endpoint, config.api.default_root)

    return app


def build_application() -> FastAPI:
    config_path = os.getenv(CONFIG_ENV_VAR, DEFAULT_CONFIG_PATH)
    config = load_config(config_path)
    configure_logging(config.logging.level)
    logger.info("Loaded configuration", extra={"status_code": 200})
    return create_app(config)


app = build_application()
