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


# ---------------------------------------------------------------------------
# JSON-body error paths
# ---------------------------------------------------------------------------


def test_parse_request_invalid_json_body_returns_400(parsing_endpoint):
    client = TestClient(_build_app(parsing_endpoint))
    response = client.post(
        "/parse",
        content="this is not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert "valid JSON" in response.json()["detail"]


def test_parse_request_non_dict_body_returns_400(parsing_endpoint):
    client = TestClient(_build_app(parsing_endpoint))
    response = client.post(
        "/parse",
        content='["not", "an", "object"]',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert "JSON object" in response.json()["detail"]


def test_parse_request_empty_body_uses_query_params(parsing_endpoint):
    client = TestClient(_build_app(parsing_endpoint))
    response = client.post("/parse?action=walk&speed=slow")
    assert response.status_code == 200
    body = response.json()
    assert body["flag_values"] == {"action": "walk", "speed": "slow"}


# ---------------------------------------------------------------------------
# Multipart / overrides / extra_args validation paths
# ---------------------------------------------------------------------------


def test_multipart_invalid_json_in_overrides_returns_400(tmp_path, parsing_endpoint):
    """``overrides`` form field is parsed as JSON; a malformed
    string returns 400 instead of 500."""
    endpoint = parsing_endpoint.model_copy(
        update={
            "uploads": [
                UploadConfig(
                    field_name="input_file",
                    placeholder="input_file",
                    temp_dir=str(tmp_path / "u"),
                )
            ]
        }
    )
    client = TestClient(_build_app(endpoint))
    response = client.post(
        "/parse?action=walk",
        data={"overrides": "{not json"},
        files={"input_file": ("a.txt", b"x", "text/plain")},
    )
    assert response.status_code == 400
    assert "invalid JSON" in response.json()["detail"]
    assert "'overrides'" in response.json()["detail"]


def test_multipart_overrides_must_be_object(tmp_path, parsing_endpoint):
    endpoint = parsing_endpoint.model_copy(
        update={
            "uploads": [
                UploadConfig(
                    field_name="input_file",
                    placeholder="input_file",
                    temp_dir=str(tmp_path / "u"),
                )
            ]
        }
    )
    client = TestClient(_build_app(endpoint))
    response = client.post(
        "/parse?action=walk",
        data={"overrides": '["a", "b"]'},
        files={"input_file": ("a.txt", b"x", "text/plain")},
    )
    assert response.status_code == 400
    assert "JSON object" in response.json()["detail"]


def test_multipart_invalid_json_in_extra_args_returns_400(tmp_path, parsing_endpoint):
    endpoint = parsing_endpoint.model_copy(
        update={
            "uploads": [
                UploadConfig(
                    field_name="input_file",
                    placeholder="input_file",
                    temp_dir=str(tmp_path / "u"),
                )
            ]
        }
    )
    client = TestClient(_build_app(endpoint))
    response = client.post(
        "/parse?action=walk",
        data={"extra_args": "{not a list"},
        files={"input_file": ("a.txt", b"x", "text/plain")},
    )
    assert response.status_code == 400
    assert "invalid JSON" in response.json()["detail"]


def test_multipart_extra_args_must_be_string_array(tmp_path, parsing_endpoint):
    """extra_args is a JSON array of STRINGS — a list of mixed
    types is rejected so the eventual subprocess call doesn't
    blow up trying to splice ints into argv."""
    endpoint = parsing_endpoint.model_copy(
        update={
            "uploads": [
                UploadConfig(
                    field_name="input_file",
                    placeholder="input_file",
                    temp_dir=str(tmp_path / "u"),
                )
            ]
        }
    )
    client = TestClient(_build_app(endpoint))
    response = client.post(
        "/parse?action=walk",
        data={"extra_args": '["ok", 42]'},
        files={"input_file": ("a.txt", b"x", "text/plain")},
    )
    assert response.status_code == 400
    assert "array of strings" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Upload field shape validation
# ---------------------------------------------------------------------------


def test_multipart_missing_required_upload_field(tmp_path, parsing_endpoint):
    endpoint = parsing_endpoint.model_copy(
        update={
            "uploads": [
                UploadConfig(
                    field_name="input_file",
                    placeholder="input_file",
                    temp_dir=str(tmp_path / "u"),
                )
            ]
        }
    )
    client = TestClient(_build_app(endpoint))
    response = client.post(
        "/parse?action=walk",
        # Only sends a non-file field — no input_file upload.
        data={"speed": "slow"},
        files={"unrelated": ("z.txt", b"x", "text/plain")},
    )
    assert response.status_code == 400
    assert "missing upload field" in response.json()["detail"]


def test_multipart_upload_field_must_be_file(tmp_path, parsing_endpoint):
    """If the caller sends a multipart form where the configured
    upload field arrives as a plain string instead of a file, we
    reject — the executor would try to write a non-file later."""
    endpoint = parsing_endpoint.model_copy(
        update={
            "uploads": [
                UploadConfig(
                    field_name="input_file",
                    placeholder="input_file",
                    temp_dir=str(tmp_path / "u"),
                )
            ]
        }
    )
    client = TestClient(_build_app(endpoint))
    # Force multipart by including at least one file (as some
    # other harmless field), then put input_file in data so it
    # arrives as a string.
    response = client.post(
        "/parse?action=walk",
        data={"input_file": "this is a string, not a file"},
        files={"_anchor": ("a.txt", b"x", "text/plain")},
    )
    assert response.status_code == 400
    assert "must be a file upload" in response.json()["detail"]


def test_multipart_non_upload_field_as_file_rejected(tmp_path, parsing_endpoint):
    """A non-upload form field that arrives as a file (eg.
    ``speed=@thing.txt``) gets rejected — only the configured
    upload fields are allowed to be files."""
    endpoint = parsing_endpoint.model_copy(
        update={
            "uploads": [
                UploadConfig(
                    field_name="input_file",
                    placeholder="input_file",
                    temp_dir=str(tmp_path / "u"),
                )
            ]
        }
    )
    client = TestClient(_build_app(endpoint))
    response = client.post(
        "/parse?action=walk",
        files={
            "input_file": ("a.txt", b"x", "text/plain"),
            "speed": ("speed.txt", b"fast", "text/plain"),
        },
    )
    assert response.status_code == 400
    assert "must be a string" in response.json()["detail"]


def test_multipart_overrides_field_as_file_rejected(tmp_path, parsing_endpoint):
    endpoint = parsing_endpoint.model_copy(
        update={
            "uploads": [
                UploadConfig(
                    field_name="input_file",
                    placeholder="input_file",
                    temp_dir=str(tmp_path / "u"),
                )
            ]
        }
    )
    client = TestClient(_build_app(endpoint))
    response = client.post(
        "/parse?action=walk",
        files={
            "input_file": ("a.txt", b"x", "text/plain"),
            "overrides": ("o.json", b'{"x": 1}', "application/json"),
        },
    )
    assert response.status_code == 400
    assert "'overrides'" in response.json()["detail"]


def test_multipart_extra_args_field_as_file_rejected(tmp_path, parsing_endpoint):
    endpoint = parsing_endpoint.model_copy(
        update={
            "uploads": [
                UploadConfig(
                    field_name="input_file",
                    placeholder="input_file",
                    temp_dir=str(tmp_path / "u"),
                )
            ]
        }
    )
    client = TestClient(_build_app(endpoint))
    response = client.post(
        "/parse?action=walk",
        files={
            "input_file": ("a.txt", b"x", "text/plain"),
            "extra_args": ("ea.json", b"[]", "application/json"),
        },
    )
    assert response.status_code == 400
    assert "'extra_args'" in response.json()["detail"]


def test_multipart_stdin_field_as_file_rejected(tmp_path, parsing_endpoint):
    endpoint = parsing_endpoint.model_copy(
        update={
            "uploads": [
                UploadConfig(
                    field_name="input_file",
                    placeholder="input_file",
                    temp_dir=str(tmp_path / "u"),
                )
            ]
        }
    )
    client = TestClient(_build_app(endpoint))
    response = client.post(
        "/parse?action=walk",
        files={
            "input_file": ("a.txt", b"x", "text/plain"),
            "stdin": ("s.txt", b"hello", "text/plain"),
        },
    )
    assert response.status_code == 400
    assert "'stdin'" in response.json()["detail"]


def test_json_body_endpoint_with_uploads_rejected(parsing_endpoint, tmp_path):
    """An endpoint that defines uploads requires multipart input.
    A JSON body alone returns 400."""
    endpoint = parsing_endpoint.model_copy(
        update={
            "uploads": [
                UploadConfig(
                    field_name="input_file",
                    placeholder="input_file",
                    temp_dir=str(tmp_path / "u"),
                )
            ]
        }
    )
    client = TestClient(_build_app(endpoint))
    response = client.post("/parse?action=walk", json={"speed": "fast"})
    assert response.status_code == 400
    assert "multipart/form-data" in response.json()["detail"]
