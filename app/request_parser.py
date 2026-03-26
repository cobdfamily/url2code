from __future__ import annotations

import json

from fastapi import HTTPException, Request, UploadFile

from .config import EndpointConfig
from .models import ToolRequest


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

    if "multipart/form-data" in content_type:
        form = await request.form()
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

        tool_request = ToolRequest(
            overrides=_coerce_json_dict(form.get("overrides"), "overrides"),
            extra_args=_coerce_json_list(form.get("extra_args"), "extra_args"),
            stdin=form.get("stdin"),
        )
        return tool_request, uploads

    if endpoint.uploads:
        raise HTTPException(
            status_code=400,
            detail=f"endpoint '{endpoint.name}' requires multipart/form-data uploads",
        )

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    return ToolRequest.model_validate(body), uploads
