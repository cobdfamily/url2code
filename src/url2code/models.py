"""Per-request models passed across module boundaries.

ToolRequest is the normalised input shape coming out
of request_parser; ToolResponse is the JSON envelope
returned to the caller. Keeping them in their own
file means executor.py + main.py don't depend on
each other for the data shape.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolRequest(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)
    flag_values: dict[str, Any] = Field(default_factory=dict)
    extra_args: list[str] = Field(default_factory=list)
    stdin: str | None = None
    # Route path parameters (e.g. /Systems/{id} -> {"id": "1"}).
    # Merged into the command-arg value bag and the response-
    # template context (request.<name>). Empty for endpoints whose
    # route has no {placeholder} segments.
    path_params: dict[str, Any] = Field(default_factory=dict)


class ToolResponse(BaseModel):
    endpoint: str
    command: list[str]
    exit_code: int
    duration_ms: int
    parsed_output: Any | None = None
    output_files: dict[str, dict[str, str]] = Field(default_factory=dict)
    stdout: str
    stderr: str
