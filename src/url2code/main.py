from __future__ import annotations

import os
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from .config import AppConfig, EndpointConfig, build_full_path, load_config, summarize_config
from .executor import execute_endpoint
from .logging_config import configure_logging
from .models import ToolResponse
from .request_parser import parse_request

CONFIG_ENV_VAR = "URL2CODE_CONFIG"
DEFAULT_CONFIG_PATH = "config/tools.yaml"
logger = logging.getLogger("cli_api")


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

def register_endpoint(app: FastAPI, endpoint: EndpointConfig, default_root: str) -> None:
    path = build_full_path(default_root, endpoint)
    download_templates = register_download_routes(app, endpoint, path)

    async def handler(request: Request) -> ToolResponse:
        tool_request, uploads = await parse_request(request, endpoint)
        return execute_endpoint(endpoint, tool_request, uploads, download_templates)

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
    app = FastAPI(
        title=config.api.title,
        version="1.0.6",
        redoc_url="/redocs",
    )

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

    for endpoint in config.endpoints:
        register_endpoint(app, endpoint, config.api.default_root)

    return app


def build_application() -> FastAPI:
    config_path = os.getenv(CONFIG_ENV_VAR, DEFAULT_CONFIG_PATH)
    config = load_config(config_path)
    configure_logging(config.logging.level)
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
    uvicorn.run("url2code.main:app", host=host, port=port, reload=False)
