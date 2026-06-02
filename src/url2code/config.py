"""YAML-driven config loader + pydantic schemas for
url2code.

Each url2code instance reads a config file at startup
that declares one or more endpoints. The schemas in
this module are the contract the YAML must match -- a
malformed config fails validation at boot, not at
request time.

Top-level: AppConfig wraps ApiConfig (FastAPI metadata)
plus a list of EndpointConfig entries. Each endpoint
declares its CLI surface, allowed overrides, output
handling, and parser. load_config() does parse +
validation; summarize_config() emits a one-line
debug summary for the boot log.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class ApiConfig(BaseModel):
    default_root: str = "/"
    title: str = "CLI Tool API"
    # Optional service-specific version surfaced on / liveness +
    # OpenAPI metadata. None falls back to the url2code engine
    # version (see ENGINE_VERSION in main.py). Downstream images
    # like cobdfamily/needle set this to their own tag so a
    # `GET /` response reports the consumer's identity, not the
    # engine's. Pinned as None (not "0.1.0") so an unset YAML
    # value is distinguishable from an explicit "0.1.0" choice.
    version: str | None = None


class LoggingConfig(BaseModel):
    level: str = "INFO"


class RateLimitConfig(BaseModel):
    # Token bucket: `requests` tokens, refilled over
    # `window_seconds`. e.g. requests=60, window_seconds=60
    # -> ~1 req/s sustained with a burst of 60.
    requests: int = Field(gt=0)
    window_seconds: float = Field(default=60.0, gt=0)


class LimitsConfig(BaseModel):
    """Optional abuse-resistance limits. All fields default to
    None/unset, so a config without a `limits:` block (or an
    endpoint without one) behaves exactly as pre-1.3 url2code."""

    # Reject a request whose Content-Length exceeds this many
    # bytes with 413, before the body is read to disk. Uploads
    # dominate body size, so this caps them too. A client that
    # omits Content-Length bypasses the check (the reverse
    # proxy is the backstop there).
    max_request_bytes: int | None = Field(default=None, gt=0)
    # Per-(endpoint, client-IP) token-bucket rate limit. None
    # means unlimited.
    rate_limit: RateLimitConfig | None = None


class RegexOutputConfig(BaseModel):
    pattern: str
    flags: list[str] = Field(default_factory=list)
    multiple: bool = False


class OutputConfig(BaseModel):
    mode: Literal["text", "native_json", "regex_json"] = "text"
    regex: RegexOutputConfig | None = None
    # Optional response-shape template. When set, the route
    # handler renders this template against the run context
    # (parsed_output, stdout, stderr, request fields, static
    # values...) and returns the rendered shape as the
    # response body INSTEAD of the default ToolResponse
    # envelope. See templating.py for the substitution
    # rules. Endpoints that omit `template` keep the classic
    # envelope -- so every pre-1.1 url2code service works
    # without YAML edits.
    template: Any | None = None
    # Content-Type for templated responses. Defaults to
    # application/json; downstream surfaces with their own
    # media type (Redfish uses application/redfish+json,
    # HAL uses application/hal+json, etc.) override here.
    # Ignored when `template` is unset.
    template_content_type: str = "application/json"
    # Static values surfaced to the template under
    # `static.<key>`. Useful for OData/Redfish boilerplate
    # (Id strings, base URLs) that's identical across every
    # request to the endpoint and doesn't belong in the
    # parsed CLI output. Ignored when `template` is unset.
    template_static: dict[str, Any] = Field(default_factory=dict)

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
    # When set, the saved upload uses this template (rendered with
    # the same value bag command args see — defaults + validated
    # overrides) instead of a random hex token. The original
    # extension is still appended. The rendered name is validated
    # against ``^[A-Za-z0-9][A-Za-z0-9._-]*$`` to keep a request
    # from smuggling a path traversal in via a template field.
    # Leave unset to keep the default random-hex naming.
    name_template: str | None = None


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
    # Per-endpoint limits override. When set, each field falls
    # back to the app-level `limits` default if left None (see
    # effective_limits). When None, the app-level default applies
    # wholesale.
    limits: LimitsConfig | None = None

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
    # Fleet-wide default limits; per-endpoint `limits` override
    # individual fields. Empty default = no limits anywhere.
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
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


def effective_limits(app_limits: LimitsConfig, endpoint: EndpointConfig) -> LimitsConfig:
    """Resolve the limits that apply to one endpoint.

    An endpoint with no `limits` block inherits the app-level
    default wholesale. An endpoint that sets `limits` overrides
    field-by-field: any field it leaves None falls back to the
    app default, so a service can override just the rate limit
    while keeping the global size cap.
    """
    override = endpoint.limits
    if override is None:
        return app_limits
    return LimitsConfig(
        max_request_bytes=(
            override.max_request_bytes
            if override.max_request_bytes is not None
            else app_limits.max_request_bytes
        ),
        rate_limit=(
            override.rate_limit if override.rate_limit is not None else app_limits.rate_limit
        ),
    )


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
