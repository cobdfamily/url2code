from __future__ import annotations

from app.config import AppConfig, EndpointConfig, build_full_path, summarize_config
from app.executor import _random_filename_token, build_command, execute_endpoint
from app.main import build_output_download_path
from app.models import ToolRequest
from fastapi import HTTPException
import subprocess
import pytest


@pytest.fixture
def dog_endpoint() -> EndpointConfig:
    return EndpointConfig.model_validate(
        {
            "name": "dog-walk",
            "route": "/walk",
            "defaults": {"action": "walk", "speed": "slow"},
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
                        "valuePrefix": "",
                        "type": "text",
                    }
                ],
            },
        }
    )


def test_build_command_uses_body_field_for_placeholder_and_flag(dog_endpoint: EndpointConfig) -> None:
    request = ToolRequest(flag_values={"action": "jump", "speed": "fast"})

    command = build_command(dog_endpoint, request, upload_paths={}, output_values={})

    assert command == ["dog", "jump", "--speed", "fast"]


def test_build_command_prefers_overrides_over_body_values(dog_endpoint: EndpointConfig) -> None:
    request = ToolRequest(overrides={"action": "run"}, flag_values={"action": "jump", "speed": "fast"})

    command = build_command(dog_endpoint, request, upload_paths={}, output_values={})

    assert command == ["dog", "run", "--speed", "fast"]


def test_build_command_uses_default_flag_value_when_request_omits_it(dog_endpoint: EndpointConfig) -> None:
    request = ToolRequest(flag_values={"action": "walk"})

    command = build_command(dog_endpoint, request, upload_paths={}, output_values={})

    assert command == ["dog", "walk", "--speed", "slow"]


def test_build_command_rejects_invalid_enum_placeholder(dog_endpoint: EndpointConfig) -> None:
    request = ToolRequest(flag_values={"action": "fly"})

    with pytest.raises(HTTPException) as exc:
        build_command(dog_endpoint, request, upload_paths={}, output_values={})

    assert exc.value.status_code == 400
    assert "override 'action'" in str(exc.value.detail)


def test_build_command_includes_output_path_and_filename_placeholders() -> None:
    endpoint = EndpointConfig.model_validate(
        {
            "name": "file-out",
            "route": "/run",
            "command": {
                "executable": "tool",
                "args": ["{output_file}", "{output_filename}"],
            },
        }
    )

    command = build_command(
        endpoint,
        ToolRequest(),
        upload_paths={},
        output_values={
            "output_file": "/tmp/results/abc123.txt",
            "output_filename": "abc123.txt",
        },
    )

    assert command == ["tool", "/tmp/results/abc123.txt", "abc123.txt"]


def test_build_command_rejects_unknown_request_field(dog_endpoint: EndpointConfig) -> None:
    request = ToolRequest(flag_values={"speed": "fast", "unknown": "value"})

    with pytest.raises(HTTPException) as exc:
        build_command(dog_endpoint, request, upload_paths={}, output_values={})

    assert exc.value.status_code == 400
    assert "unsupported request fields" in str(exc.value.detail)


def test_app_config_rejects_duplicate_routes() -> None:
    with pytest.raises(ValueError) as exc:
        AppConfig.model_validate(
            {
                "endpoints": [
                    {
                        "name": "one",
                        "route": "/run",
                        "command": {"executable": "tool"},
                    },
                    {
                        "name": "two",
                        "route": "/run",
                        "command": {"executable": "tool"},
                    },
                ]
            }
        )

    assert "duplicate endpoint route detected" in str(exc.value)


def test_endpoint_config_rejects_duplicate_flag_names() -> None:
    with pytest.raises(ValueError) as exc:
        EndpointConfig.model_validate(
            {
                "name": "bad-flags",
                "route": "/run",
                "command": {"executable": "tool"},
                "request": {
                    "flags": [
                        {"name": "mode", "flag": "--mode", "type": "text"},
                        {"name": "mode", "flag": "--mode2", "type": "text"},
                    ]
                },
            }
        )

    assert "duplicate flag names" in str(exc.value)


def test_endpoint_config_rejects_conflicting_placeholders() -> None:
    with pytest.raises(ValueError) as exc:
        EndpointConfig.model_validate(
            {
                "name": "bad-placeholders",
                "route": "/run",
                "command": {"executable": "tool"},
                "uploads": [{"field_name": "input", "placeholder": "shared"}],
                "output_files": [{"placeholder": "shared"}],
            }
        )

    assert "reuses placeholder 'shared'" in str(exc.value)


def test_config_summary_includes_paths_and_counts() -> None:
    config = AppConfig.model_validate(
        {
            "api": {"default_root": "/api"},
            "endpoints": [
                {
                    "name": "one",
                    "route": "/run",
                    "root": "/tools",
                    "command": {"executable": "tool"},
                    "request": {"flags": [{"name": "mode", "flag": "--mode", "type": "text"}]},
                    "uploads": [{"field_name": "input", "placeholder": "input_file"}],
                    "output_files": [{"placeholder": "output_file"}],
                }
            ],
        }
    )

    summary = summarize_config(config)

    assert build_full_path(config.api.default_root, config.endpoints[0]) == "/tools/run"
    assert summary == [
        {
            "name": "one",
            "method": "POST",
            "path": "/tools/run",
            "flags": 1,
            "uploads": 1,
            "output_files": 1,
        }
    ]


def test_random_filename_token_is_64_hex_chars() -> None:
    token = _random_filename_token()

    assert len(token) == 64
    assert all(character in "0123456789abcdef" for character in token)


def test_execute_endpoint_returns_download_url(monkeypatch, tmp_path) -> None:
    endpoint = EndpointConfig.model_validate(
        {
            "name": "file-out",
            "route": "/run",
            "command": {
                "executable": "tool",
                "args": [],
            },
            "output_files": [
                {
                    "placeholder": "output_file",
                    "filename_placeholder": "output_filename",
                    "output_dir": str(tmp_path / "outputs"),
                    "suffix": ".txt",
                }
            ],
        }
    )

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("app.executor.subprocess.run", fake_run)

    response = execute_endpoint(
        endpoint,
        ToolRequest(),
        download_path_templates={"output_file": build_output_download_path("/tools/run", "output_file")},
    )

    output_file = response.output_files["output_file"]
    assert output_file["download_url"].startswith("/tools/run/downloads/output_file/")
    assert output_file["filename"].endswith(".txt")
    assert len(output_file["filename"].removesuffix(".txt")) == 64
