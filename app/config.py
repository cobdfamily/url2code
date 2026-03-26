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
    allow_extra_args: bool = False

    @model_validator(mode="after")
    def merge_allowed_overrides(self) -> "RequestConfig":
        combined = set(self.allowed_overrides)
        combined.update(self.validations)
        self.allowed_overrides = sorted(combined)
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


class AppConfig(BaseModel):
    api: ApiConfig = Field(default_factory=ApiConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    endpoints: list[EndpointConfig]


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    data = yaml.safe_load(config_path.read_text()) or {}
    return AppConfig.model_validate(data)
