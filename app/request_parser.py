from __future__ import annotations

import json

from fastapi import HTTPException, Request, UploadFile

from .config import EndpointConfig
from .models import ToolRequest


RESERVED_FIELDS = {"overrides", "extra_args", "stdin"}


def _coerce_json_dict(value: str | None, field_name: str) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON in form field '{field_name}'") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail=f"form field '{field_name}' must be a JSON object")
    return parsed


def _coerce_json_list(value: str | None, field_name: str) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON in form field '{field_name}'") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise HTTPException(status_code=400, detail=f"form field '{field_name}' must be a JSON array of strings")
    return parsed


async def parse_request(request: Request, endpoint: EndpointConfig) -> tuple[ToolRequest, dict[str, UploadFile]]:
    content_type = request.headers.get("content-type", "")
    uploads: dict[str, UploadFile] = {}
    query_values = dict(request.query_params)

    if "multipart/form-data" in content_type:
        form = await request.form()
        body_values: dict[str, str] = {}
        for upload_config in endpoint.uploads:
            value = form.get(upload_config.field_name)
            if value is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"missing upload field '{upload_config.field_name}' for endpoint '{endpoint.name}'",
                )
            if not isinstance(value, UploadFile):
                raise HTTPException(
                    status_code=400,
                    detail=f"form field '{upload_config.field_name}' must be a file upload",
                )
            uploads[upload_config.placeholder] = value

        for key, value in form.multi_items():
            if key in RESERVED_FIELDS or key in {upload.field_name for upload in endpoint.uploads}:
                continue
            if isinstance(value, UploadFile):
                raise HTTPException(status_code=400, detail=f"form field '{key}' must be a string, not a file upload")
            body_values[key] = value

        overrides_raw = form.get("overrides")
        if isinstance(overrides_raw, UploadFile):
            raise HTTPException(status_code=400, detail="form field 'overrides' must be a string, not a file upload")
        extra_args_raw = form.get("extra_args")
        if isinstance(extra_args_raw, UploadFile):
            raise HTTPException(status_code=400, detail="form field 'extra_args' must be a string, not a file upload")
        stdin_raw = form.get("stdin")
        if isinstance(stdin_raw, UploadFile):
            raise HTTPException(status_code=400, detail="form field 'stdin' must be a string, not a file upload")

        tool_request = ToolRequest(
            overrides=_coerce_json_dict(overrides_raw, "overrides"),
            flag_values={**query_values, **body_values},
            extra_args=_coerce_json_list(extra_args_raw, "extra_args"),
            stdin=stdin_raw,
        )
        return tool_request, uploads

    if endpoint.uploads:
        raise HTTPException(
            status_code=400,
            detail=f"endpoint '{endpoint.name}' requires multipart/form-data uploads",
        )

    raw_body = await request.body()
    if not raw_body:
        return ToolRequest(flag_values=query_values), uploads
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="request body must be valid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    reserved = {key: value for key, value in body.items() if key in RESERVED_FIELDS}
    body_values = {key: value for key, value in body.items() if key not in RESERVED_FIELDS}
    tool_request = ToolRequest.model_validate(
        {
            **reserved,
            "flag_values": {**query_values, **body_values},
        }
    )
    return tool_request, uploads
