"""Integration tests for the response-template feature
at the route level.

Two big shapes:

  1. Endpoint WITHOUT `output.template` -> the route
     returns the classic ToolResponse envelope, byte for
     byte the same as 1.0.8 returned. Backwards-compat
     guard.

  2. Endpoint WITH `output.template` -> the route
     returns the shaped body with the configured
     Content-Type; the ToolResponse envelope is NOT
     visible in the response.

Both run through FastAPI's TestClient against a tiny
config that shells out to `/bin/echo`, so the suite is
self-contained.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from url2code.config import AppConfig
from url2code.main import create_app


def _build_app(endpoints_yaml: list[dict]) -> AppConfig:
    return AppConfig.model_validate({
        "api": {"title": "test", "default_root": "/v1"},
        "endpoints": endpoints_yaml,
    })


def test_endpoint_without_template_returns_classic_envelope() -> None:
    """Backwards-compat: an endpoint that omits
    `output.template` returns the same ToolResponse
    shape every pre-1.1 url2code version returned."""
    config = _build_app([
        {
            "name":   "echo",
            "route":  "/echo",
            "method": "GET",
            "command": {
                "executable": "/bin/echo",
                "args":       ["hello"],
            },
        },
    ])
    app = create_app(config)
    client = TestClient(app)
    response = client.get("/v1/echo")
    assert response.status_code == 200
    body = response.json()
    # Classic envelope fields all present.
    assert body["endpoint"] == "echo"
    assert body["exit_code"] == 0
    assert body["stdout"].strip() == "hello"
    assert body["stderr"] == ""
    assert "duration_ms" in body
    assert body["parsed_output"] is None  # mode=text default


def test_endpoint_with_template_returns_shaped_body() -> None:
    """When `output.template` is set, the response IS the
    template -- no ToolResponse fields visible at the
    root."""
    config = _build_app([
        {
            "name":   "shaped",
            "route":  "/shaped",
            "method": "GET",
            "command": {
                "executable": "/bin/echo",
                "args":       ["hello"],
            },
            "output": {
                "mode": "text",
                "template_static": {
                    "id":   "1",
                    "name": "System One",
                },
                "template": {
                    "@odata.id":   "/redfish/v1/Systems/{static.id}",
                    "@odata.type": "#ComputerSystem.v1_0_0.ComputerSystem",
                    "Id":          "{static.id}",
                    "Name":        "{static.name}",
                    # Whole-leaf form preserves the native
                    # int. Embedded form (`"exit={x}"`)
                    # would stringify; pick the shape your
                    # schema wants.
                    "Exit":        "{exit_code}",
                },
            },
        },
    ])
    app = create_app(config)
    client = TestClient(app)
    response = client.get("/v1/shaped")
    assert response.status_code == 200
    body = response.json()
    # Custom shape at the root, no ToolResponse fields.
    assert body == {
        "@odata.id":   "/redfish/v1/Systems/1",
        "@odata.type": "#ComputerSystem.v1_0_0.ComputerSystem",
        "Id":          "1",
        "Name":        "System One",
        "Exit":        0,
    }
    assert "parsed_output" not in body
    assert "stdout" not in body


def test_endpoint_with_template_custom_content_type() -> None:
    """`template_content_type` lands on the Content-Type
    header so downstream surfaces with their own media
    type (Redfish, HAL, JSON-LD, etc.) can declare it."""
    config = _build_app([
        {
            "name":   "redfish",
            "route":  "/redfish",
            "method": "GET",
            "command": {
                "executable": "/bin/echo",
                "args":       ["x"],
            },
            "output": {
                "mode": "text",
                "template_content_type":
                    "application/redfish+json",
                "template": {"ok": True},
            },
        },
    ])
    app = create_app(config)
    client = TestClient(app)
    response = client.get("/v1/redfish")
    assert response.status_code == 200
    assert "application/redfish+json" in (
        response.headers.get("content-type", "")
    )


def test_endpoint_template_path_miss_returns_500_with_envelope() -> None:
    """A typo'd template path is loud, not silent. 500
    response carries the template error AND the
    underlying ToolResponse envelope so operators can
    see what the CLI actually returned."""
    config = _build_app([
        {
            "name":   "broken",
            "route":  "/broken",
            "method": "GET",
            "command": {
                "executable": "/bin/echo",
                "args":       ["hi"],
            },
            "output": {
                "mode": "text",
                "template": {
                    "x": "{parsed_output.no_such_field}",
                },
            },
        },
    ])
    app = create_app(config)
    client = TestClient(app)
    response = client.get("/v1/broken")
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "template rendering failed" in detail["message"]
    # mode=text leaves parsed_output as None, so the
    # template renderer hits the "walks past None" branch
    # rather than a missing-key. Either error message is
    # acceptable; we just want SOMETHING actionable to
    # show the operator which path mismatched.
    assert "parsed_output.no_such_field" in detail["error"]
    assert detail["envelope"]["endpoint"] == "broken"
    assert detail["envelope"]["stdout"].strip() == "hi"


def test_native_json_output_flows_into_template() -> None:
    """A CLI that emits JSON -> parsed_output is a dict ->
    template can pluck specific fields from it. End-to-end
    smoke for the salmon-shaped use case (ipmitool wrapper
    emits JSON, template wraps in Redfish boilerplate)."""
    # `printf` here emits a small JSON blob to stdout.
    config = _build_app([
        {
            "name":   "power",
            "route":  "/power",
            "method": "GET",
            "command": {
                "executable": "/bin/sh",
                "args": [
                    "-c",
                    # Doubled braces so executor's
                    # str.format pass-through leaves the
                    # literal `{` and `}` in the shell
                    # command. Same escape rule the
                    # existing /bin/echo args don't have
                    # to think about because they have
                    # no braces.
                    "printf '%s' '{{\"state\":\"on\"}}'",
                ],
            },
            "output": {
                "mode": "native_json",
                "template_static": {"id": "1"},
                "template": {
                    "@odata.id":  "/redfish/v1/Systems/{static.id}",
                    "Id":         "{static.id}",
                    "PowerState": "{parsed_output.state}",
                },
            },
        },
    ])
    app = create_app(config)
    client = TestClient(app)
    response = client.get("/v1/power")
    assert response.status_code == 200
    assert response.json() == {
        "@odata.id":  "/redfish/v1/Systems/1",
        "Id":         "1",
        "PowerState": "on",
    }
