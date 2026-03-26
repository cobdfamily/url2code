from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolRequest(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)
    flag_values: dict[str, Any] = Field(default_factory=dict)
    extra_args: list[str] = Field(default_factory=list)
    stdin: str | None = None


class ToolResponse(BaseModel):
    endpoint: str
    command: list[str]
    exit_code: int
    duration_ms: int
    parsed_output: Any | None = None
    output_files: dict[str, dict[str, str]] = Field(default_factory=dict)
    stdout: str
    stderr: str
