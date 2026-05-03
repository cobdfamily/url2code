# url2code

[![test](https://github.com/cobdfamily/url2code/actions/workflows/test.yml/badge.svg)](https://github.com/cobdfamily/url2code/actions/workflows/test.yml)

YAML-driven FastAPI wrapper for CLI tools. Each endpoint is declared in YAML, can live under its own API root, supports request-time argument overrides, and can parse non-JSON CLI output into JSON using regex named capture groups.

> Deploying url2code in production? See **[DEPLOYMENT.md](DEPLOYMENT.md)**
> for the full checklist (image pull from the kibble registry,
> configure / run / verify, upgrades).

## Features

- YAML config for API metadata and per-endpoint behavior
- Dynamic FastAPI route registration
- Per-endpoint route roots with `/` as the default
- Request-driven placeholder values and approved CLI flags
- Typed override validation for `number`, `bool`, `enum`, and `text`
- Approved per-endpoint CLI flags mapped from body/query params
- Optional per-endpoint file uploads with unique 64-character random temp filenames
- Optional per-endpoint generated output files with unique 64-character random saved filenames returned in JSON
- Download URLs for generated output files
- Optional `extra_args` passthrough for tools that need arbitrary flags
- Native JSON parsing or regex-to-JSON parsing for text output
- Structured JSON logs for success, failures, parsing errors, and timeouts
- Lightweight container via `python:3.12-slim`
- `uv` included in the container for installing Python-distributed CLI tools

## Project Layout

```text
src/url2code/
  config.py
  executor.py
  logging_config.py
  main.py
  models.py
  parser.py
  request_parser.py
config/
  tools.yaml
Dockerfile
pyproject.toml
```

## Run Locally

```bash
uv sync
uv run url2code
# or, with auto-reload during dev:
uv run uvicorn url2code.main:app --reload
```

Auto-generated docs at `/docs` and `/redocs`.

To use a different YAML file:

```bash
URL2CODE_CONFIG=/app/config/tools.yaml uv run url2code
```

The container also bundles `uv`, so additional Python CLI tools can
be installed with commands such as `uv tool install ...` or
`uv pip install --system ...`.

## YAML Configuration

```yaml
api:
  title: URL2Code CLI API
  version: 0.1.0
  default_root: /

logging:
  level: INFO

endpoints:
  - name: dog-walk
    route: /walk
    root: /dogs
    defaults:
      action: walk
    command:
      executable: dog
      args:
        - "{action}"
    request:
      validations:
        action:
          type: enum
          choices:
            - walk
            - jump
      flags:
        - name: speed
          flag: --speed
          valuePrefix: ""
          type: text
```

Notes:

- `route` is the endpoint path.
- `root` is optional. If omitted, `api.default_root` is used.
- `defaults` provides placeholder values for `command.args`.
- `request.validations` defines which placeholder names callers may supply and enforces type checks.
- `request.flags` defines approved request fields that render into CLI flags.
- `uploads` maps multipart form file fields to command placeholders.
- `uploads[*].name_template` (optional) — render the saved upload's
  filename from the same value bag command args see (defaults +
  validated overrides), instead of the default random hex token.
  Useful when the wrapped CLI uses the on-disk filename as the
  entry's identifier (`audfprint` does, for example). The
  rendered name is validated against `[A-Za-z0-9][A-Za-z0-9._-]*`
  to keep a request from smuggling a path traversal.
- `output_files` defines placeholders that should be replaced with unique persistent output paths.
- `filename_placeholder` exposes the generated filename separately when a script wants the basename instead of the full path.
- `allow_extra_args` enables raw extra CLI args appended to the command.
- `output.mode` can be `text`, `native_json`, or `regex_json`.

Validation types:

- `number`: integer or float input
- `bool`: `true/false`, `1/0`, `yes/no`, or `on/off`
- `enum`: string constrained to configured choices
- `text`: string input

Flag fields:

- `name`: request field name from JSON body, multipart form field, or query param
- `flag`: CLI flag to emit
- `valuePrefix`: string prepended to the validated value before it is passed
- `type`: `number`, `bool`, `enum`, or `text`
- `choices`: required for `enum`

Request field precedence:

- Explicit `overrides` entries win for command template placeholders
- Otherwise JSON body or multipart form fields are used
- Query params are used only when the body/form does not provide that field

## Request Format

For JSON-only endpoints:

```json
{
  "action": "jump",
  "speed": "fast",
  "stdin": null
}
```

For the example above, a request to `/dogs/walk` still hits the same backend endpoint, but the actual CLI command becomes:

```bash
dog jump --speed fast
```

If the URL is `/dogs/walk?action=walk&speed=slow` and the body is `{ "action": "jump" }`, the effective values are `action=jump` and `speed=slow`.

If you need to set a templated placeholder explicitly, you can still use:

```json
{
  "overrides": {
    "action": "jump"
  },
  "speed": "fast"
}
```

For upload-enabled endpoints, send `multipart/form-data`:

- `stdin`: optional string
- one text field per approved flag or override
- `extra_args`: optional JSON array string, only if that endpoint explicitly allows it
- one file part per configured upload field

Example:

```bash
curl -X POST http://localhost:8000/file2braille/translate \
  -F 'table=en-us-g2.ctb' \
  -F 'input_file=@sample.txt'
```

The uploaded file is stored under a unique cryptographic filename with at least 64 random hex characters, used in the command, and deleted after the command finishes.

## File Uploads And Outputs

Example YAML:

```yaml
- name: file2braille-translate
  route: /translate
  root: /file2braille
  command:
    executable: file2braille
    args:
      - "{input_file}"
      - "{output_filename}"
      - "{output_file}"
  defaults:
    table: en-us-g2.ctb
  request:
    flags:
      - name: table
        flag: --table
        valuePrefix: ""
        type: enum
        choices:
          - en-us-g2.ctb
          - en-us-g1.ctb
  uploads:
    - field_name: input_file
      placeholder: input_file
      temp_dir: /tmp/url2code/uploads/file2braille
  output_files:
    - placeholder: output_file
      filename_placeholder: output_filename
      output_dir: /tmp/url2code/outputs/file2braille
      suffix: .brf
      prefix: file2braille-
```

On success, the response JSON includes the saved output path:

```json
{
  "output_files": {
    "output_file": {
      "path": "/tmp/url2code/outputs/file2braille/file2braille-<64-random-hex>.brf",
      "filename": "file2braille-<64-random-hex>.brf",
      "download_url": "/file2braille/translate/downloads/output_file/file2braille-<64-random-hex>.brf"
    }
  }
}
```

Configured output files are preserved on success and deleted on failed command execution or failed output parsing. If `filename_placeholder` is set, both `{output_file}` and `{output_filename}` are available to the command template.

Generated file names use at least 64 random hex characters for both temporary uploads and saved outputs, making them difficult to guess.

Download route pattern:

```text
<endpoint-path>/downloads/<output-placeholder>/<filename>
```

## Regex Output Parsing

If a tool does not return JSON, use named capture groups:

```yaml
output:
  mode: regex_json
  regex:
    pattern: "(?P<track>.+)\\s+matched\\s+(?P<score>\\d+)\\s+times"
```

This produces:

```json
{
  "track": "example.wav",
  "score": "42"
}
```

Set `multiple: true` to collect all matches into a JSON array.

## Error Handling

- `400` for invalid request fields, invalid flag values, or missing placeholder values
- `500` when the executable is missing
- `502` when the command exits non-zero or output parsing fails
- `504` when the command times out

## Tests

```bash
uv sync
uv run pytest -q
uv run pytest --cov   # with branch coverage
```

The test suite covers request parsing precedence, approved flag
validation, command rendering, output placeholder handling, every
type-coercion branch, and the executor failure / timeout paths.
Branch-coverage gate is set at 85%.

## Config Validation

Configuration is validated at startup and fails fast on:

- duplicate endpoint names
- duplicate method/path combinations after root resolution
- duplicate flag names within an endpoint
- conflicting upload/output placeholders within an endpoint

Startup logs also include a compact config summary listing each endpoint path plus counts for flags, uploads, and output files.

## Container

A pre-built image is published to the kibble registry on every
`git tag v*` (see [DEPLOYMENT.md](DEPLOYMENT.md) for the full
production playbook):

```bash
docker pull kibble.apps.blindhub.ca/cobdfamily/url2code:latest
docker run --rm -p 8000:8000 \
  -v "$(pwd)/config:/app/config" \
  -e URL2CODE_CONFIG=/app/config/tools.yaml \
  kibble.apps.blindhub.ca/cobdfamily/url2code:latest
```

For local builds against a development checkout:

```bash
docker build -t url2code .
docker run --rm -p 8000:8000 -v "$(pwd)/config:/app/config" url2code
```

## Building a downstream image

A downstream image bakes in its own config + any extra CLI
binaries the YAML wraps. Sample shape (used by
`cobdfamily/needle`):

```Dockerfile
ARG URL2CODE_TAG=latest
FROM kibble.apps.blindhub.ca/cobdfamily/url2code:${URL2CODE_TAG}

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends <your tool> \
 && rm -rf /var/lib/apt/lists/*

USER url2code
RUN uv pip install --no-cache --python /app/.venv/bin/python <your-package>

# REQUIRED: replace url2code's bundled example tools.yaml with
# yours. The base image's URL2CODE_CONFIG defaults to
# /app/config/tools.yaml, so this single COPY (which overwrites
# every file the base image shipped under config/) is enough —
# operators don't need to set URL2CODE_CONFIG themselves.
COPY --chown=url2code:url2code config /app/config

# CMD inherited from base (uvicorn url2code.main:app ...).
```

The `service` field returned by `GET /` (the liveness probe)
echoes your `config/tools.yaml`'s `api.title`, so downstream
consumers report their own identity to monitoring rather than
"url2code". Set `api.title: needle` (or whatever) in your YAML
and `curl /` will show it.

## License

AGPL-3.0 — see `LICENSE`.
