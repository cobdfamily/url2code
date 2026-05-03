from __future__ import annotations

from url2code.config import AppConfig, EndpointConfig, build_full_path, summarize_config
from url2code.executor import _random_filename_token, build_command, execute_endpoint
from url2code.main import build_output_download_path
from url2code.models import ToolRequest
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

    monkeypatch.setattr("url2code.executor.subprocess.run", fake_run)

    response = execute_endpoint(
        endpoint,
        ToolRequest(),
        download_path_templates={"output_file": build_output_download_path("/tools/run", "output_file")},
    )

    output_file = response.output_files["output_file"]
    assert output_file["download_url"].startswith("/tools/run/downloads/output_file/")
    assert output_file["filename"].endswith(".txt")
    assert len(output_file["filename"].removesuffix(".txt")) == 64


# ---------------------------------------------------------------------------
# _validate_flag_value — type coercion + rejection branches
# ---------------------------------------------------------------------------


def _flag_endpoint(flag_type: str, **flag_extra) -> EndpointConfig:
    """Helper: build an endpoint with a single flag of the given
    type so the validation paths can be exercised in isolation."""
    return EndpointConfig.model_validate(
        {
            "name": "flag-test",
            "route": "/run",
            "command": {"executable": "tool", "args": []},
            "request": {
                "flags": [
                    {
                        "name": "x",
                        "flag": "--x",
                        "type": flag_type,
                        **flag_extra,
                    }
                ]
            },
        }
    )


def test_flag_number_from_int_string():
    ep = _flag_endpoint("number")
    cmd = build_command(ep, ToolRequest(flag_values={"x": "42"}), {}, {})
    assert cmd == ["tool", "--x", "42"]


def test_flag_number_from_float_string():
    ep = _flag_endpoint("number")
    cmd = build_command(ep, ToolRequest(flag_values={"x": "3.14"}), {}, {})
    assert cmd == ["tool", "--x", "3.14"]


def test_flag_number_rejects_bool():
    ep = _flag_endpoint("number")
    with pytest.raises(HTTPException) as exc:
        build_command(ep, ToolRequest(flag_values={"x": True}), {}, {})
    assert exc.value.status_code == 400
    assert "must be a number" in str(exc.value.detail)


def test_flag_number_rejects_invalid_string():
    ep = _flag_endpoint("number")
    with pytest.raises(HTTPException) as exc:
        build_command(ep, ToolRequest(flag_values={"x": "notanumber"}), {}, {})
    assert "must be a number" in str(exc.value.detail)


def test_flag_number_rejects_other_types():
    ep = _flag_endpoint("number")
    with pytest.raises(HTTPException) as exc:
        build_command(ep, ToolRequest(flag_values={"x": [1, 2]}), {}, {})
    assert "must be a number" in str(exc.value.detail)


def test_flag_bool_truthy_strings_render():
    ep = _flag_endpoint("bool")
    for truthy in ("true", "1", "yes", "on"):
        cmd = build_command(ep, ToolRequest(flag_values={"x": truthy}), {}, {})
        assert cmd == ["tool", "--x"]


def test_flag_bool_falsy_strings_skip_flag():
    """When a bool flag's value is false-y, the flag is omitted
    from the rendered command entirely (CLI tools toggle by
    presence)."""
    ep = _flag_endpoint("bool")
    for falsy in ("false", "0", "no", "off"):
        cmd = build_command(ep, ToolRequest(flag_values={"x": falsy}), {}, {})
        assert cmd == ["tool"]


def test_flag_bool_with_value_prefix_renders_value():
    """When valuePrefix is set, the flag emits both the flag and
    a key=value-style payload — eg. ``--debug debug=true``."""
    ep = _flag_endpoint("bool", valuePrefix="enabled=")
    cmd = build_command(ep, ToolRequest(flag_values={"x": True}), {}, {})
    assert cmd == ["tool", "--x", "enabled=true"]


def test_flag_bool_rejects_other_types():
    ep = _flag_endpoint("bool")
    with pytest.raises(HTTPException) as exc:
        build_command(ep, ToolRequest(flag_values={"x": 42}), {}, {})
    assert "must be a boolean" in str(exc.value.detail)


def test_flag_enum_rejects_non_string():
    ep = _flag_endpoint("enum", choices=["fast", "slow"])
    with pytest.raises(HTTPException) as exc:
        build_command(ep, ToolRequest(flag_values={"x": 1}), {}, {})
    assert "must be one of" in str(exc.value.detail)


def test_flag_enum_rejects_outside_choices():
    ep = _flag_endpoint("enum", choices=["fast", "slow"])
    with pytest.raises(HTTPException) as exc:
        build_command(ep, ToolRequest(flag_values={"x": "medium"}), {}, {})
    assert "must be one of" in str(exc.value.detail)


def test_flag_text_rejects_non_string():
    ep = _flag_endpoint("text")
    with pytest.raises(HTTPException) as exc:
        build_command(ep, ToolRequest(flag_values={"x": [1, 2]}), {}, {})
    assert "must be text" in str(exc.value.detail)


# ---------------------------------------------------------------------------
# _coerce_override_value — same shape, hits via overrides instead
# ---------------------------------------------------------------------------


def _override_endpoint(validation_type: str, **kw) -> EndpointConfig:
    return EndpointConfig.model_validate(
        {
            "name": "ov-test",
            "route": "/run",
            "command": {"executable": "tool", "args": ["{x}"]},
            "request": {
                "validations": {"x": {"type": validation_type, **kw}},
                "allowed_overrides": ["x"],
            },
        }
    )


def test_override_number_from_string():
    ep = _override_endpoint("number")
    cmd = build_command(ep, ToolRequest(overrides={"x": "5"}), {}, {})
    assert cmd == ["tool", "5"]


def test_override_number_rejects_bool():
    ep = _override_endpoint("number")
    with pytest.raises(HTTPException) as exc:
        build_command(ep, ToolRequest(overrides={"x": True}), {}, {})
    assert "override 'x'" in str(exc.value.detail)


def test_override_bool_normalizes_strings():
    ep = _override_endpoint("bool")
    cmd = build_command(ep, ToolRequest(overrides={"x": "yes"}), {}, {})
    assert cmd == ["tool", "true"]


def test_override_bool_rejects_invalid_string():
    ep = _override_endpoint("bool")
    with pytest.raises(HTTPException) as exc:
        build_command(ep, ToolRequest(overrides={"x": "maybe"}), {}, {})
    assert "must be a boolean" in str(exc.value.detail)


def test_override_text_rejects_non_string():
    ep = _override_endpoint("text")
    with pytest.raises(HTTPException) as exc:
        build_command(ep, ToolRequest(overrides={"x": 99}), {}, {})
    assert "must be text" in str(exc.value.detail)


def test_override_no_validation_passes_through():
    """When the override key isn't in the validations map, the
    raw value goes through unchanged."""
    ep = EndpointConfig.model_validate(
        {
            "name": "no-validate",
            "route": "/run",
            "command": {"executable": "tool", "args": ["{x}"]},
            "request": {"allowed_overrides": ["x"]},
        }
    )
    cmd = build_command(ep, ToolRequest(overrides={"x": "anything"}), {}, {})
    assert cmd == ["tool", "anything"]


# ---------------------------------------------------------------------------
# build_command — extra_args + missing-placeholder paths
# ---------------------------------------------------------------------------


def test_build_command_extra_args_when_allowed():
    ep = EndpointConfig.model_validate(
        {
            "name": "extras-ok",
            "route": "/run",
            "command": {"executable": "tool", "args": []},
            "request": {"allow_extra_args": True},
        }
    )
    cmd = build_command(
        ep,
        ToolRequest(extra_args=["--quiet", "-v"]),
        {},
        {},
    )
    assert cmd == ["tool", "--quiet", "-v"]


def test_build_command_extra_args_rejected_when_not_allowed():
    ep = EndpointConfig.model_validate(
        {
            "name": "extras-no",
            "route": "/run",
            "command": {"executable": "tool", "args": []},
            "request": {"allow_extra_args": False},
        }
    )
    with pytest.raises(HTTPException) as exc:
        build_command(ep, ToolRequest(extra_args=["--bad"]), {}, {})
    assert exc.value.status_code == 400
    assert "extra_args" in str(exc.value.detail)


def test_build_command_missing_placeholder_value_raises():
    """If the args template references a placeholder that nothing
    supplies — no flag, no override, no upload, no output, no
    default — we 400 with a clear error rather than KeyError."""
    ep = EndpointConfig.model_validate(
        {
            "name": "needs-x",
            "route": "/run",
            "command": {"executable": "tool", "args": ["{x}"]},
        }
    )
    with pytest.raises(HTTPException) as exc:
        build_command(ep, ToolRequest(), {}, {})
    assert exc.value.status_code == 400
    assert "missing argument value" in str(exc.value.detail)
    assert "'x'" in str(exc.value.detail)


def test_build_command_rejects_unknown_override():
    """Override keys must be on the endpoint's allowed_overrides
    allow-list. Anything else 400s before any other validation."""
    ep = EndpointConfig.model_validate(
        {
            "name": "tight",
            "route": "/run",
            "command": {"executable": "tool", "args": []},
            "request": {"allowed_overrides": ["allowed"]},
        }
    )
    with pytest.raises(HTTPException) as exc:
        build_command(ep, ToolRequest(overrides={"forbidden": "x"}), {}, {})
    assert exc.value.status_code == 400
    assert "unsupported overrides" in str(exc.value.detail)


# ---------------------------------------------------------------------------
# execute_endpoint — failure-path coverage
# ---------------------------------------------------------------------------


def _executable_endpoint(tmp_path) -> EndpointConfig:
    return EndpointConfig.model_validate(
        {
            "name": "echo",
            "route": "/run",
            "command": {"executable": "tool", "args": []},
            "output_files": [
                {
                    "placeholder": "output_file",
                    "output_dir": str(tmp_path / "out"),
                }
            ],
        }
    )


def test_execute_endpoint_handles_file_not_found(monkeypatch, tmp_path):
    """``FileNotFoundError`` from subprocess.run -> 500 with a
    descriptive message naming the executable."""
    ep = _executable_endpoint(tmp_path)

    def fake_run(*a, **kw):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr("url2code.executor.subprocess.run", fake_run)

    with pytest.raises(HTTPException) as exc:
        execute_endpoint(ep, ToolRequest())
    assert exc.value.status_code == 500
    assert "executable not found" in str(exc.value.detail)


def test_execute_endpoint_handles_oserror(monkeypatch, tmp_path):
    """Any non-FileNotFound OSError (eg. permission denied) is a
    500 too, but with the underlying error attached for ops."""
    ep = _executable_endpoint(tmp_path)

    def fake_run(*a, **kw):
        raise PermissionError("denied")

    monkeypatch.setattr("url2code.executor.subprocess.run", fake_run)

    with pytest.raises(HTTPException) as exc:
        execute_endpoint(ep, ToolRequest())
    assert exc.value.status_code == 500
    assert "could not be launched" in str(exc.value.detail)


def test_execute_endpoint_handles_timeout(monkeypatch, tmp_path):
    """``subprocess.TimeoutExpired`` -> 504, not 500. Tools that
    hang are a different operational class than tools that crash
    or are missing."""
    ep = _executable_endpoint(tmp_path)

    def fake_run(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="tool", timeout=5)

    monkeypatch.setattr("url2code.executor.subprocess.run", fake_run)

    with pytest.raises(HTTPException) as exc:
        execute_endpoint(ep, ToolRequest())
    assert exc.value.status_code == 504
    assert "timed out" in str(exc.value.detail)


def test_execute_endpoint_handles_nonzero_returncode(monkeypatch, tmp_path):
    """Non-zero exit -> 502 with a structured detail dict
    containing exit_code and stderr so the caller can render
    error UI without parsing the message string."""
    ep = _executable_endpoint(tmp_path)

    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(
            args=a[0], returncode=2, stdout="", stderr="bad input",
        )

    monkeypatch.setattr("url2code.executor.subprocess.run", fake_run)

    with pytest.raises(HTTPException) as exc:
        execute_endpoint(ep, ToolRequest())
    assert exc.value.status_code == 502
    assert exc.value.detail["exit_code"] == 2
    assert exc.value.detail["stderr"] == "bad input"


def test_execute_endpoint_handles_output_parse_error(monkeypatch, tmp_path):
    """The CLI succeeded but its stdout doesn't match the
    configured output schema -> 502 (the CLI gave us garbage,
    not the caller). Output files still get cleaned up."""
    ep = EndpointConfig.model_validate(
        {
            "name": "json-out",
            "route": "/run",
            "command": {"executable": "tool", "args": []},
            "output": {"mode": "native_json"},
        }
    )

    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(
            args=a[0], returncode=0, stdout="not json", stderr="",
        )

    monkeypatch.setattr("url2code.executor.subprocess.run", fake_run)

    with pytest.raises(HTTPException) as exc:
        execute_endpoint(ep, ToolRequest())
    assert exc.value.status_code == 502
    assert "valid JSON" in str(exc.value.detail)


def test_execute_endpoint_text_mode_returns_raw_stdout(monkeypatch, tmp_path):
    """``mode: text`` returns parsed_output=None and lets the
    caller use stdout directly."""
    ep = EndpointConfig.model_validate(
        {
            "name": "text-out",
            "route": "/run",
            "command": {"executable": "tool", "args": []},
            "output": {"mode": "text"},
        }
    )

    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(
            args=a[0], returncode=0, stdout="raw text\n", stderr="",
        )

    monkeypatch.setattr("url2code.executor.subprocess.run", fake_run)

    response = execute_endpoint(ep, ToolRequest())
    assert response.parsed_output is None
    assert response.stdout == "raw text\n"
    assert response.exit_code == 0
