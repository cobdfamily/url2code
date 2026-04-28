from __future__ import annotations

from url2code.config import EndpointConfig, UploadConfig
from url2code.request_parser import parse_request
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def parsing_endpoint() -> EndpointConfig:
    return EndpointConfig.model_validate(
        {
            "name": "dog-walk",
            "route": "/walk",
            "defaults": {"action": "walk"},
            "command": {
                "executable": "dog",
                "args": ["{action}"],
            },
            "request": {
                "validations": {
                    "action": {
                        "type": "enum",
                        "choices": ["walk", "jump", "run"],
                    }
                },
                "flags": [
                    {
                        "name": "speed",
                        "flag": "--speed",
                        "type": "text",
                    }
                ],
            },
        }
    )


def _build_app(endpoint: EndpointConfig) -> FastAPI:
    app = FastAPI()

    @app.post("/parse")
    async def parse(request: Request) -> dict:
        tool_request, uploads = await parse_request(request, endpoint)
        return {
            "overrides": tool_request.overrides,
            "flag_values": tool_request.flag_values,
            "extra_args": tool_request.extra_args,
            "stdin": tool_request.stdin,
            "uploads": sorted(uploads),
        }

    return app


def test_parse_request_prefers_body_values_over_query_params(parsing_endpoint: EndpointConfig) -> None:
    client = TestClient(_build_app(parsing_endpoint))

    response = client.post("/parse?action=walk&speed=slow", json={"action": "jump", "speed": "fast"})

    assert response.status_code == 200
    assert response.json()["flag_values"] == {"action": "jump", "speed": "fast"}


def test_parse_request_keeps_explicit_overrides_separate(parsing_endpoint: EndpointConfig) -> None:
    client = TestClient(_build_app(parsing_endpoint))

    response = client.post("/parse?action=walk", json={"overrides": {"action": "run"}, "speed": "fast"})

    assert response.status_code == 200
    assert response.json()["overrides"] == {"action": "run"}
    assert response.json()["flag_values"] == {"action": "walk", "speed": "fast"}


def test_parse_request_accepts_multipart_fields_and_uploads(tmp_path, parsing_endpoint: EndpointConfig) -> None:
    endpoint = parsing_endpoint.model_copy(
        update={
            "uploads": [
                UploadConfig(
                    field_name="input_file",
                    placeholder="input_file",
                    temp_dir=str(tmp_path / "uploads"),
                )
            ]
        }
    )
    client = TestClient(_build_app(endpoint))

    response = client.post(
        "/parse?action=walk",
        data={"speed": "fast", "stdin": "hello"},
        files={"input_file": ("sample.txt", b"abc", "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["flag_values"] == {"action": "walk", "speed": "fast"}
    assert response.json()["stdin"] == "hello"
    assert response.json()["uploads"] == ["input_file"]
