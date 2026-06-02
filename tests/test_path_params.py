"""Route path-parameter support (1.7.0). A route like
/item/{id} exposes {id} as a command placeholder and as
request.<name> in response templates, optionally validated."""

from __future__ import annotations

from fastapi.testclient import TestClient

from url2code.config import AppConfig
from url2code.main import create_app


def _client(endpoints: list[dict]) -> TestClient:
    return TestClient(create_app(AppConfig.model_validate(
        {"api": {"title": "t", "default_root": "/v1"}, "endpoints": endpoints}
    )))


def test_path_param_fills_command_placeholder() -> None:
    client = _client([{
        "name": "echo-id",
        "route": "/item/{id}",
        "method": "GET",
        "command": {"executable": "/bin/echo", "args": ["{id}"]},
    }])
    r = client.get("/v1/item/abc123")
    assert r.status_code == 200, r.text
    assert r.json()["stdout"].strip() == "abc123"


def test_path_param_available_in_response_template() -> None:
    client = _client([{
        "name": "tmpl",
        "route": "/sys/{id}",
        "method": "GET",
        "command": {"executable": "/bin/echo", "args": ["x"]},
        "output": {"mode": "text", "template": {"Id": "{request.id}"}},
    }])
    r = client.get("/v1/sys/42")
    assert r.status_code == 200, r.text
    assert r.json() == {"Id": "42"}


def test_path_param_validated_when_validation_declared() -> None:
    # id declared as an enum -> only the listed values are accepted.
    client = _client([{
        "name": "v",
        "route": "/m/{id}",
        "method": "GET",
        "command": {"executable": "/bin/echo", "args": ["{id}"]},
        "request": {"validations": {"id": {"type": "enum", "choices": ["1", "2"]}}},
    }])
    assert client.get("/v1/m/1").status_code == 200
    bad = client.get("/v1/m/9")
    assert bad.status_code == 400, bad.text


def test_routes_without_path_params_are_unchanged() -> None:
    client = _client([{
        "name": "plain",
        "route": "/plain",
        "method": "GET",
        "command": {"executable": "/bin/echo", "args": ["hi"]},
    }])
    r = client.get("/v1/plain")
    assert r.status_code == 200
    assert r.json()["stdout"].strip() == "hi"
