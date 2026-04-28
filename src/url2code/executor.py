from __future__ import annotations

import logging
import os
import secrets
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from .config import EndpointConfig, FlagConfig
from .models import ToolRequest, ToolResponse
from .parser import OutputParseError, parse_output

logger = logging.getLogger("cli_api.executor")


def _random_filename_token() -> str:
    return secrets.token_hex(32)


def _write_upload(upload: UploadFile, temp_dir: str) -> str:
    Path(temp_dir).mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.filename or "").suffix
    path = Path(temp_dir) / f"{_random_filename_token()}{suffix}"
    with path.open("wb") as handle:
        upload.file.seek(0)
        handle.write(upload.file.read())
    return str(path)


def _build_output_path(output_dir: str, prefix: str | None, suffix: str | None) -> str:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    filename = f"{prefix or ''}{_random_filename_token()}{suffix or ''}"
    return str(Path(output_dir) / filename)


def _cleanup_files(paths: dict[str, str]) -> None:
    for path in paths.values():
        Path(path).unlink(missing_ok=True)


def _coerce_override_value(endpoint: EndpointConfig, key: str, value: Any) -> Any:
    validation = endpoint.request.validations.get(key)
    if validation is None:
        return value

    if validation.type == "number":
        if isinstance(value, bool):
            raise HTTPException(status_code=400, detail=f"override '{key}' must be a number")
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                return float(value) if "." in value else int(value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"override '{key}' must be a number") from exc
        raise HTTPException(status_code=400, detail=f"override '{key}' must be a number")

    if validation.type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        raise HTTPException(status_code=400, detail=f"override '{key}' must be a boolean")

    if validation.type == "enum":
        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"override '{key}' must be one of: {', '.join(validation.choices)}")
        if value not in validation.choices:
            raise HTTPException(status_code=400, detail=f"override '{key}' must be one of: {', '.join(validation.choices)}")
        return value

    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"override '{key}' must be text")
    return value


def _validated_overrides(endpoint: EndpointConfig, overrides: dict[str, Any]) -> dict[str, Any]:
    return {key: _coerce_override_value(endpoint, key, value) for key, value in overrides.items()}


def _resolved_override_values(endpoint: EndpointConfig, request: ToolRequest) -> dict[str, Any]:
    resolved = dict(request.overrides)
    for name in endpoint.request.allowed_overrides:
        if name in resolved:
            continue
        if name in request.flag_values:
            resolved[name] = request.flag_values[name]
    return _validated_overrides(endpoint, resolved)


def _validate_flag_value(flag_config: FlagConfig, value: Any) -> Any:
    if flag_config.type == "number":
        if isinstance(value, bool):
            raise HTTPException(status_code=400, detail=f"flag '{flag_config.name}' must be a number")
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                return float(value) if "." in value else int(value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"flag '{flag_config.name}' must be a number") from exc
        raise HTTPException(status_code=400, detail=f"flag '{flag_config.name}' must be a number")

    if flag_config.type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        raise HTTPException(status_code=400, detail=f"flag '{flag_config.name}' must be a boolean")

    if flag_config.type == "enum":
        if not isinstance(value, str):
            raise HTTPException(
                status_code=400,
                detail=f"flag '{flag_config.name}' must be one of: {', '.join(flag_config.choices)}",
            )
        if value not in flag_config.choices:
            raise HTTPException(
                status_code=400,
                detail=f"flag '{flag_config.name}' must be one of: {', '.join(flag_config.choices)}",
            )
        return value

    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"flag '{flag_config.name}' must be text")
    return value


def _render_flag_args(endpoint: EndpointConfig, request: ToolRequest) -> list[str]:
    rendered: list[str] = []
    allowed_names = {flag.name for flag in endpoint.request.flags}
    disallowed = sorted(set(request.flag_values) - allowed_names - set(endpoint.request.allowed_overrides))
    if disallowed:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported request fields for endpoint '{endpoint.name}': {', '.join(disallowed)}",
        )

    for flag_config in endpoint.request.flags:
        if flag_config.name in request.flag_values:
            raw_value = request.flag_values[flag_config.name]
        elif flag_config.name in endpoint.defaults:
            raw_value = endpoint.defaults[flag_config.name]
        else:
            continue
        value = _validate_flag_value(flag_config, raw_value)
        if flag_config.type == "bool":
            if not value:
                continue
            if flag_config.valuePrefix:
                rendered.extend([flag_config.flag, f"{flag_config.valuePrefix}true"])
            else:
                rendered.append(flag_config.flag)
            continue

        rendered.extend([flag_config.flag, f"{flag_config.valuePrefix}{_stringify_template_value(value)}"])

    return rendered


def _stringify_template_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def build_command(
    endpoint: EndpointConfig,
    request: ToolRequest,
    upload_paths: dict[str, str],
    output_values: dict[str, str],
) -> list[str]:
    allowed = set(endpoint.request.allowed_overrides)
    disallowed = sorted(set(request.overrides) - allowed)
    if disallowed:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported overrides for endpoint '{endpoint.name}': {', '.join(disallowed)}",
        )

    values: dict[str, str] = {
        key: _stringify_template_value(value) for key, value in dict(endpoint.defaults).items()
    }
    values.update({key: _stringify_template_value(value) for key, value in _resolved_override_values(endpoint, request).items()})
    values.update({key: _stringify_template_value(value) for key, value in upload_paths.items()})
    values.update({key: _stringify_template_value(value) for key, value in output_values.items()})

    rendered_args: list[str] = []
    try:
        for token in endpoint.command.args:
            rendered_args.append(token.format(**values))
    except KeyError as exc:
        missing_key = exc.args[0]
        raise HTTPException(
            status_code=400,
            detail=f"missing argument value for placeholder '{missing_key}'",
        ) from exc

    rendered_args.extend(_render_flag_args(endpoint, request))

    if request.extra_args:
        if not endpoint.request.allow_extra_args:
            raise HTTPException(
                status_code=400,
                detail=f"endpoint '{endpoint.name}' does not allow extra_args",
            )
        rendered_args.extend(request.extra_args)

    return [endpoint.command.executable, *rendered_args]


def execute_endpoint(
    endpoint: EndpointConfig,
    request: ToolRequest,
    uploads: dict[str, UploadFile] | None = None,
    download_path_templates: dict[str, str] | None = None,
) -> ToolResponse:
    uploads = uploads or {}
    download_path_templates = download_path_templates or {}
    upload_paths: dict[str, str] = {}
    output_file_results: dict[str, dict[str, str]] = {}
    output_values: dict[str, str] = {}

    for output_file in endpoint.output_files:
        output_path = _build_output_path(
            output_file.output_dir,
            output_file.prefix,
            output_file.suffix,
        )
        output_filename = Path(output_path).name
        output_file_results[output_file.placeholder] = {
            "path": output_path,
            "filename": output_filename,
        }
        if output_file.placeholder in download_path_templates:
            output_file_results[output_file.placeholder]["download_url"] = download_path_templates[
                output_file.placeholder
            ].format(filename=output_filename)
        output_values[output_file.placeholder] = output_path
        if output_file.filename_placeholder:
            output_values[output_file.filename_placeholder] = output_filename

    try:
        for upload_config in endpoint.uploads:
            upload = uploads.get(upload_config.placeholder)
            if upload is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"missing upload content for placeholder '{upload_config.placeholder}'",
                )
            upload_paths[upload_config.placeholder] = _write_upload(upload, upload_config.temp_dir)

        command = build_command(endpoint, request, upload_paths, output_values)
    except Exception:
        for upload in uploads.values():
            upload.file.close()
        _cleanup_files(upload_paths)
        _cleanup_files({key: value["path"] for key, value in output_file_results.items()})
        raise

    started = time.perf_counter()

    try:
        completed = subprocess.run(
            command,
            input=request.stdin,
            text=True,
            capture_output=True,
            cwd=endpoint.command.cwd,
            env={**os.environ, **endpoint.command.env},
            timeout=endpoint.command.timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        _cleanup_files({key: value["path"] for key, value in output_file_results.items()})
        logger.exception(
            "CLI executable not found",
            extra={
                "endpoint": endpoint.name,
                "command": shlex.join(command),
                "request_overrides": request.overrides,
                "status_code": 500,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"CLI executable not found: {endpoint.command.executable}",
        ) from exc
    except OSError as exc:
        _cleanup_files({key: value["path"] for key, value in output_file_results.items()})
        logger.exception(
            "CLI executable could not be launched",
            extra={
                "endpoint": endpoint.name,
                "command": shlex.join(command),
                "request_overrides": request.overrides,
                "status_code": 500,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"CLI executable could not be launched: {exc}",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        _cleanup_files({key: value["path"] for key, value in output_file_results.items()})
        logger.exception(
            "CLI command timed out",
            extra={
                "endpoint": endpoint.name,
                "command": shlex.join(command),
                "request_overrides": request.overrides,
                "duration_ms": duration_ms,
                "status_code": 504,
            },
        )
        raise HTTPException(
            status_code=504,
            detail=f"CLI command timed out after {endpoint.command.timeout_seconds}s",
        ) from exc
    finally:
        for upload in uploads.values():
            upload.file.close()
        for path in upload_paths.values():
            Path(path).unlink(missing_ok=True)

    duration_ms = int((time.perf_counter() - started) * 1000)

    if completed.returncode != 0:
        _cleanup_files({key: value["path"] for key, value in output_file_results.items()})
        logger.error(
            "CLI command failed",
            extra={
                "endpoint": endpoint.name,
                "command": shlex.join(command),
                "request_overrides": request.overrides,
                "duration_ms": duration_ms,
                "return_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "status_code": 502,
            },
        )
        raise HTTPException(
            status_code=502,
            detail={
                "message": "CLI command failed",
                "exit_code": completed.returncode,
                "stderr": completed.stderr,
            },
        )

    try:
        parsed_output = parse_output(completed.stdout, endpoint.output)
    except OutputParseError as exc:
        _cleanup_files({key: value["path"] for key, value in output_file_results.items()})
        logger.error(
            "CLI output parsing failed",
            extra={
                "endpoint": endpoint.name,
                "command": shlex.join(command),
                "request_overrides": request.overrides,
                "duration_ms": duration_ms,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "status_code": 502,
            },
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    logger.info(
        "CLI command succeeded",
        extra={
            "endpoint": endpoint.name,
            "command": shlex.join(command),
            "request_overrides": request.overrides,
            "duration_ms": duration_ms,
            "return_code": completed.returncode,
            "route": endpoint.route,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "output_files": output_file_results,
            "status_code": 200,
        },
    )

    return ToolResponse(
        endpoint=endpoint.name,
        command=command,
        exit_code=completed.returncode,
        duration_ms=duration_ms,
        parsed_output=parsed_output,
        output_files=output_file_results,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
