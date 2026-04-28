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
app/
  config.py
  executor.py
  logging_config.py
  main.py
  models.py
  parser.py
config/
  tools.yaml
Dockerfile
requirements.txt
```

## Run Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

To use a different YAML file:

```bash
URL2CODE_CONFIG=/app/config/tools.yaml uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The container also includes `uv`, so additional Python CLI tools can be installed with commands such as `uv tool install ...` or `uv pip install --system ...`.

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

Install dependencies and run:

```bash
pytest
```

The test suite covers request parsing precedence, approved flag validation, command rendering, and output placeholder handling.

## Config Validation

Configuration is validated at startup and fails fast on:

- duplicate endpoint names
- duplicate method/path combinations after root resolution
- duplicate flag names within an endpoint
- conflicting upload/output placeholders within an endpoint

Startup logs also include a compact config summary listing each endpoint path plus counts for flags, uploads, and output files.

## Container

```bash
docker build -t url2code .
docker run --rm -p 8000:8000 -v "$(pwd)/config:/app/config" url2code
```
