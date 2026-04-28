from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class ApiConfig(BaseModel):
    default_root: str = "/"
    title: str = "CLI Tool API"
    version: str = "0.1.0"


class LoggingConfig(BaseModel):
    level: str = "INFO"


class RegexOutputConfig(BaseModel):
    pattern: str
    flags: list[str] = Field(default_factory=list)
    multiple: bool = False


class OutputConfig(BaseModel):
    mode: Literal["text", "native_json", "regex_json"] = "text"
    regex: RegexOutputConfig | None = None

    @model_validator(mode="after")
    def validate_regex_requirement(self) -> "OutputConfig":
        if self.mode == "regex_json" and self.regex is None:
            raise ValueError("regex output mode requires a regex configuration")
        return self


class ArgumentValidationConfig(BaseModel):
    type: Literal["number", "bool", "enum", "text"]
    choices: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_choices_requirement(self) -> "ArgumentValidationConfig":
        if self.type == "enum" and not self.choices:
            raise ValueError("enum validation requires at least one choice")
        if self.type != "enum" and self.choices:
            raise ValueError("choices are only valid for enum validation")
        return self


class RequestConfig(BaseModel):
    allowed_overrides: list[str] = Field(default_factory=list)
    validations: dict[str, ArgumentValidationConfig] = Field(default_factory=dict)
    flags: list["FlagConfig"] = Field(default_factory=list)
    allow_extra_args: bool = False

    @model_validator(mode="after")
    def merge_allowed_overrides(self) -> "RequestConfig":
        combined = set(self.allowed_overrides)
        combined.update(self.validations)
        self.allowed_overrides = sorted(combined)
        return self


class FlagConfig(BaseModel):
    name: str
    flag: str
    valuePrefix: str = ""
    type: Literal["number", "bool", "enum", "text"]
    choices: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_choices_requirement(self) -> "FlagConfig":
        if self.type == "enum" and not self.choices:
            raise ValueError("enum flag requires at least one choice")
        if self.type != "enum" and self.choices:
            raise ValueError("choices are only valid for enum flags")
        return self


class UploadConfig(BaseModel):
    field_name: str
    placeholder: str
    temp_dir: str = "/tmp/url2code/uploads"


class OutputFileConfig(BaseModel):
    placeholder: str
    filename_placeholder: str | None = None
    output_dir: str = "/tmp/url2code/outputs"
    suffix: str | None = None
    prefix: str | None = None


class CommandConfig(BaseModel):
    executable: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    timeout_seconds: int = 30


class EndpointConfig(BaseModel):
    name: str
    route: str
    method: Literal["GET", "POST"] = "POST"
    root: str | None = None
    description: str | None = None
    defaults: dict[str, Any] = Field(default_factory=dict)
    command: CommandConfig
    request: RequestConfig = Field(default_factory=RequestConfig)
    uploads: list[UploadConfig] = Field(default_factory=list)
    output_files: list[OutputFileConfig] = Field(default_factory=list)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @model_validator(mode="after")
    def validate_unique_endpoint_fields(self) -> "EndpointConfig":
        flag_names = [flag.name for flag in self.request.flags]
        if len(flag_names) != len(set(flag_names)):
            raise ValueError(f"endpoint '{self.name}' has duplicate flag names")

        placeholders: set[str] = set()
        for upload in self.uploads:
            if upload.placeholder in placeholders:
                raise ValueError(f"endpoint '{self.name}' reuses placeholder '{upload.placeholder}'")
            placeholders.add(upload.placeholder)

        for output_file in self.output_files:
            names = [output_file.placeholder]
            if output_file.filename_placeholder:
                names.append(output_file.filename_placeholder)
            for name in names:
                if name in placeholders:
                    raise ValueError(f"endpoint '{self.name}' reuses placeholder '{name}'")
                placeholders.add(name)

        return self


class AppConfig(BaseModel):
    api: ApiConfig = Field(default_factory=ApiConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    endpoints: list[EndpointConfig]

    @model_validator(mode="after")
    def validate_unique_routes(self) -> "AppConfig":
        seen_routes: dict[tuple[str, str], str] = {}
        endpoint_names: set[str] = set()

        for endpoint in self.endpoints:
            if endpoint.name in endpoint_names:
                raise ValueError(f"duplicate endpoint name '{endpoint.name}'")
            endpoint_names.add(endpoint.name)

            route_key = (endpoint.method, build_full_path(self.api.default_root, endpoint))
            existing = seen_routes.get(route_key)
            if existing is not None:
                method, path = route_key
                raise ValueError(
                    f"duplicate endpoint route detected for {method} {path}: '{existing}' and '{endpoint.name}'"
                )
            seen_routes[route_key] = endpoint.name

        return self


def normalize_root(root: str) -> str:
    if not root or root == "/":
        return ""
    return "/" + root.strip("/")


def normalize_route(route: str) -> str:
    return "/" + route.strip("/")


def build_full_path(default_root: str, endpoint: EndpointConfig) -> str:
    root = endpoint.root if endpoint.root is not None else default_root
    normalized_root = normalize_root(root)
    normalized_route = normalize_route(endpoint.route)
    return normalized_route if not normalized_root else f"{normalized_root}{normalized_route}"


def summarize_config(config: AppConfig) -> list[dict[str, str | int]]:
    return [
        {
            "name": endpoint.name,
            "method": endpoint.method,
            "path": build_full_path(config.api.default_root, endpoint),
            "flags": len(endpoint.request.flags),
            "uploads": len(endpoint.uploads),
            "output_files": len(endpoint.output_files),
        }
        for endpoint in config.endpoints
    ]


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    data = yaml.safe_load(config_path.read_text()) or {}
    return AppConfig.model_validate(data)
