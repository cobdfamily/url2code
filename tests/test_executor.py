from __future__ import annotations

from url2code.config import AppConfig, EndpointConfig, UploadConfig, build_full_path, summarize_config
from url2code.executor import (
    _random_filename_token,
    _render_upload_name,
    build_command,
    execute_endpoint,
)
from url2code.main import build_output_download_path
from url2code.models import ToolRequest
from fastapi import HTTPException
import asyncio
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


async def test_execute_endpoint_returns_download_url(monkeypatch, tmp_path) -> None:
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

    _patch_exec(monkeypatch, proc=_FakeProc(returncode=0, stdout=b"ok"))

    response = await execute_endpoint(
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


class _FakeProc:
    """Stand-in for an asyncio subprocess. `hang=True` makes
    communicate() sleep so a wait_for(timeout=0) trips the timeout
    path; otherwise communicate() returns the canned (stdout, stderr)
    bytes and `returncode` is reported as-is."""

    def __init__(self, returncode=0, stdout=b"", stderr=b"", hang=False):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._hang = hang

    async def communicate(self, input=None):
        if self._hang:
            await asyncio.sleep(30)  # cancelled by wait_for(timeout=0)
        return self._stdout, self._stderr

    def kill(self):
        pass

    async def wait(self):
        return self.returncode


def _patch_exec(monkeypatch, *, proc=None, exc=None, captured=None):
    """Replace asyncio.create_subprocess_exec (as the executor sees
    it) with a fake that either raises `exc` or returns `proc`,
    optionally recording the argv it was invoked with into
    `captured["argv"]`."""

    async def _create(*argv, **kwargs):
        if captured is not None:
            captured["argv"] = list(argv)
        if exc is not None:
            raise exc
        return proc

    monkeypatch.setattr(
        "url2code.executor.asyncio.create_subprocess_exec", _create,
    )


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


async def test_execute_endpoint_handles_file_not_found(monkeypatch, tmp_path):
    """``FileNotFoundError`` from the spawn -> 500 with a
    descriptive message naming the executable."""
    ep = _executable_endpoint(tmp_path)
    _patch_exec(monkeypatch, exc=FileNotFoundError("no such file"))

    with pytest.raises(HTTPException) as exc:
        await execute_endpoint(ep, ToolRequest())
    assert exc.value.status_code == 500
    assert "executable not found" in str(exc.value.detail)


async def test_execute_endpoint_handles_oserror(monkeypatch, tmp_path):
    """Any non-FileNotFound OSError (eg. permission denied) is a
    500 too, but with the underlying error attached for ops."""
    ep = _executable_endpoint(tmp_path)
    _patch_exec(monkeypatch, exc=PermissionError("denied"))

    with pytest.raises(HTTPException) as exc:
        await execute_endpoint(ep, ToolRequest())
    assert exc.value.status_code == 500
    assert "could not be launched" in str(exc.value.detail)


async def test_execute_endpoint_handles_timeout(monkeypatch, tmp_path):
    """A child that outruns its timeout -> 504, not 500. Tools that
    hang are a different operational class than tools that crash or
    are missing. timeout_seconds=0 + a sleeping fake trips wait_for
    immediately."""
    ep = EndpointConfig.model_validate(
        {
            "name": "slow",
            "route": "/run",
            "command": {"executable": "tool", "args": [], "timeout_seconds": 0},
        }
    )
    _patch_exec(monkeypatch, proc=_FakeProc(hang=True))

    with pytest.raises(HTTPException) as exc:
        await execute_endpoint(ep, ToolRequest())
    assert exc.value.status_code == 504
    assert "timed out" in str(exc.value.detail)


async def test_execute_endpoint_handles_nonzero_returncode(monkeypatch, tmp_path):
    """Non-zero exit -> 502 with a structured detail dict
    containing exit_code and stderr so the caller can render
    error UI without parsing the message string."""
    ep = _executable_endpoint(tmp_path)
    _patch_exec(monkeypatch, proc=_FakeProc(returncode=2, stderr=b"bad input"))

    with pytest.raises(HTTPException) as exc:
        await execute_endpoint(ep, ToolRequest())
    assert exc.value.status_code == 502
    assert exc.value.detail["exit_code"] == 2
    assert exc.value.detail["stderr"] == "bad input"


async def test_execute_endpoint_handles_output_parse_error(monkeypatch, tmp_path):
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
    _patch_exec(monkeypatch, proc=_FakeProc(returncode=0, stdout=b"not json"))

    with pytest.raises(HTTPException) as exc:
        await execute_endpoint(ep, ToolRequest())
    assert exc.value.status_code == 502
    assert "valid JSON" in str(exc.value.detail)


async def test_execute_endpoint_text_mode_returns_raw_stdout(monkeypatch, tmp_path):
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
    _patch_exec(monkeypatch, proc=_FakeProc(returncode=0, stdout=b"raw text\n"))

    response = await execute_endpoint(ep, ToolRequest())
    assert response.parsed_output is None
    assert response.stdout == "raw text\n"
    assert response.exit_code == 0


# ---------------------------------------------------------------------------
# upload name_template — render + sanitize + end-to-end
# ---------------------------------------------------------------------------


def _upload_config(**kw) -> UploadConfig:
    base = {"field_name": "audio", "placeholder": "audio",
            "temp_dir": "/tmp"}
    return UploadConfig.model_validate({**base, **kw})


def test_render_upload_name_returns_random_when_template_unset():
    """Default behaviour preserved: ``name_template`` unset ->
    random hex token + suffix. Length is 64 hex chars + suffix."""
    name = _render_upload_name(_upload_config(), {}, ".wav")
    stem = name.removesuffix(".wav")
    assert name.endswith(".wav")
    assert len(stem) == 64
    assert all(c in "0123456789abcdef" for c in stem)


def test_render_upload_name_substitutes_request_field():
    """``{id}`` in the template draws from the value bag —
    same dict the command args see (defaults + validated
    overrides)."""
    config = _upload_config(name_template="{id}")
    assert _render_upload_name(config, {"id": "tt0123456"}, ".m4a") \
        == "tt0123456.m4a"


def test_render_upload_name_supports_compound_templates():
    """Template can mix multiple fields and literal text."""
    config = _upload_config(name_template="{category}-{id}")
    assert _render_upload_name(
        config, {"category": "films", "id": "tt0123456"}, ".m4a",
    ) == "films-tt0123456.m4a"


def test_render_upload_name_400s_on_missing_field():
    """A template referencing a field that didn't validate
    raises 400 with a useful detail — operators see this when
    they typo a field name in the YAML."""
    config = _upload_config(name_template="{missing}")
    with pytest.raises(HTTPException) as exc:
        _render_upload_name(config, {"id": "tt0123456"}, ".m4a")
    assert exc.value.status_code == 400
    assert "unknown field" in exc.value.detail.lower()


@pytest.mark.parametrize("evil", [
    "../etc/passwd",
    "/abs/path",
    "..",
    ".hidden",
    "with spaces",
    "with/slash",
    "with\\backslash",
    "",
])
def test_render_upload_name_400s_on_unsafe_value(evil):
    """The rendered name must match
    ``[A-Za-z0-9][A-Za-z0-9._-]*``; anything else is rejected
    with 400. This is the load-bearing safety check — without
    it a client can choose any path on disk for the upload."""
    config = _upload_config(name_template="{id}")
    with pytest.raises(HTTPException) as exc:
        _render_upload_name(config, {"id": evil}, ".m4a")
    assert exc.value.status_code == 400


def test_render_upload_name_accepts_typical_canonical_ids():
    """Sanity — ids that look like IMDb tt-numbers, YouTube
    video ids, IG media ids all pass."""
    config = _upload_config(name_template="{id}")
    for ok in ("tt0123456", "dQw4w9WgXcQ", "C8jK_3DpQYZ",
               "ep.s01e02", "demo-1.0"):
        out = _render_upload_name(config, {"id": ok}, ".m4a")
        assert out == f"{ok}.m4a"


async def test_execute_endpoint_writes_upload_with_templated_name(
    monkeypatch, tmp_path,
):
    """End-to-end: an endpoint that templates the upload name
    on a request field actually saves the upload to that path
    on disk, and the rendered path is what the subprocess gets
    invoked with."""
    from io import BytesIO

    from fastapi import UploadFile

    upload_dir = tmp_path / "uploads"
    endpoint = EndpointConfig.model_validate({
        "name": "stable-name",
        "route": "/x",
        "command": {
            "executable": "tool",
            "args": ["{audio}"],
        },
        "request": {
            "validations": {"id": {"type": "text"}},
        },
        "uploads": [{
            "field_name": "audio",
            "placeholder": "audio",
            "temp_dir": str(upload_dir),
            "name_template": "{id}",
        }],
    })

    captured: dict = {}
    _patch_exec(monkeypatch, proc=_FakeProc(returncode=0, stdout=b"ok"), captured=captured)

    upload = UploadFile(
        filename="raw-input.m4a",
        file=BytesIO(b"fake audio bytes"),
    )
    request = ToolRequest(flag_values={"id": "tt0133093"})

    await execute_endpoint(endpoint, request, uploads={"audio": upload})

    # The argv should reference the templated path, not a
    # random-hex name.
    [_executable, audio_path] = captured["argv"]
    assert audio_path == str(upload_dir / "tt0133093.m4a")


# ---------------------------------------------------------------------------
# optional uploads (required: false)
# ---------------------------------------------------------------------------


def _doc_and_bib_endpoint(tmp_path) -> EndpointConfig:
    """A required `document` upload + an optional `bibliography`
    one; the argv is <exe> <doc-path> <bib-path>."""
    return EndpointConfig.model_validate({
        "name": "cite",
        "route": "/x",
        "command": {"executable": "tool", "args": ["{doc}", "{bib}"]},
        "uploads": [
            {"field_name": "document", "placeholder": "doc",
             "temp_dir": str(tmp_path / "u")},
            {"field_name": "bibliography", "placeholder": "bib",
             "temp_dir": str(tmp_path / "u"), "required": False},
        ],
    })


async def test_optional_upload_absent_renders_empty(monkeypatch, tmp_path):
    """An upload with required=false may be omitted: no 400, and
    its placeholder renders as an empty string in the argv."""
    from io import BytesIO

    from fastapi import UploadFile

    endpoint = _doc_and_bib_endpoint(tmp_path)
    captured: dict = {}
    _patch_exec(monkeypatch, proc=_FakeProc(returncode=0, stdout=b"ok"), captured=captured)

    doc = UploadFile(filename="d.md", file=BytesIO(b"hello"))
    await execute_endpoint(endpoint, ToolRequest(), uploads={"doc": doc})

    _exe, doc_path, bib = captured["argv"]
    assert doc_path.endswith(".md")
    assert bib == ""  # absent optional upload -> empty placeholder


async def test_optional_upload_present_is_written(monkeypatch, tmp_path):
    """When the optional upload IS supplied, its placeholder
    resolves to the written path like any other upload."""
    from io import BytesIO

    from fastapi import UploadFile

    endpoint = _doc_and_bib_endpoint(tmp_path)
    captured: dict = {}
    _patch_exec(monkeypatch, proc=_FakeProc(returncode=0, stdout=b"ok"), captured=captured)

    doc = UploadFile(filename="d.md", file=BytesIO(b"hello"))
    bib = UploadFile(filename="refs.bib", file=BytesIO(b"@book{x,title={Y}}"))
    await execute_endpoint(
        endpoint, ToolRequest(), uploads={"doc": doc, "bib": bib},
    )

    _exe, _doc_path, bib_path = captured["argv"]
    assert bib_path.endswith(".bib") and bib_path != ""


async def test_required_upload_absent_still_400(monkeypatch, tmp_path):
    """Regression: an omitted REQUIRED upload is still a 400."""
    endpoint = _doc_and_bib_endpoint(tmp_path)
    _patch_exec(monkeypatch, proc=_FakeProc(returncode=0, stdout=b"ok"))

    with pytest.raises(HTTPException) as exc:
        await execute_endpoint(endpoint, ToolRequest(), uploads={})
    assert exc.value.status_code == 400
    assert "missing upload content" in str(exc.value.detail)


# ---------------------------------------------------------------------------
# / liveness — service field echoes the configured api.title
# ---------------------------------------------------------------------------


def _make_app(title: str | None):
    """Tiny helper: build an AppConfig with the given api.title and
    return a TestClient against the resulting FastAPI app."""
    from fastapi.testclient import TestClient

    from url2code.main import create_app

    spec = {"endpoints": []}
    if title is not None:
        spec["api"] = {"title": title}
    return TestClient(create_app(AppConfig.model_validate(spec)))


def test_root_liveness_uses_api_title_as_service():
    """Downstream consumers (eg. cobdfamily/needle) override
    ``api.title`` in their tools.yaml. The / liveness response
    should reflect that identity, not the upstream "url2code"
    string. Without this, monitoring dashboards / load balancers
    that pin off ``service`` see every consumer reporting as
    url2code regardless of what's actually running."""
    client = _make_app(title="needle")
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "needle"
    assert body["status"] == "ok"
    assert body["version"]


def test_root_liveness_uses_default_title_when_unset():
    """A consumer that doesn't customize ``api.title`` gets
    the AppConfig default (``"CLI Tool API"``) reported as
    the service field, not the upstream ``"url2code"``
    string. (FastAPI itself rejects an empty title at app
    construction, so the only way to get a non-default title
    is to set one explicitly.)"""
    client = _make_app(title=None)
    r = client.get("/")
    body = r.json()
    assert body["service"] == "CLI Tool API"


# ---------------------------------------------------------------------------
# / liveness -- version field honours api.version when set
# ---------------------------------------------------------------------------


def _make_app_with(title: str | None = None, version: str | None = None):
    """Build an AppConfig with optional api.title and api.version
    and return a TestClient. None for either field means "don't
    set this in the YAML"."""
    from fastapi.testclient import TestClient

    from url2code.main import create_app

    spec: dict = {"endpoints": []}
    api: dict = {}
    if title is not None:
        api["title"] = title
    if version is not None:
        api["version"] = version
    if api:
        spec["api"] = api
    return TestClient(create_app(AppConfig.model_validate(spec)))


def test_root_liveness_reports_engine_version_when_api_version_unset():
    """No api.version in YAML -> the engine version (the
    hardcoded ENGINE_VERSION in main.py) is reported. This is
    the default path used by url2code itself when no consumer
    image is involved."""
    from url2code.main import ENGINE_VERSION

    client = _make_app_with(title="some-consumer")
    body = client.get("/").json()
    assert body["version"] == ENGINE_VERSION


def test_root_liveness_honours_explicit_api_version():
    """Downstream images (cobdfamily/needle, etc.) set
    api.version to their own image tag so the liveness
    response carries the consumer's identity. Without this,
    /v1/admin/categories consumers can't tell which build of
    the consumer is actually running -- only which engine."""
    client = _make_app_with(title="needle", version="0.2.1")
    body = client.get("/").json()
    assert body["version"] == "0.2.1"
    assert body["service"] == "needle"
