# url2code

YAML-driven FastAPI wrapper for CLI tools. Each endpoint is declared in YAML, can live under its own API root, supports request-time argument overrides, and can parse non-JSON CLI output into JSON using regex named capture groups.

## Features

- YAML config for API metadata and per-endpoint behavior
- Dynamic FastAPI route registration
- Per-endpoint route roots with `/` as the default
- Named argument overrides from the API request
- Typed override validation for `number`, `bool`, `enum`, and `text`
- Optional per-endpoint file uploads with unique temp filenames
- Optional per-endpoint generated output files with unique saved filenames returned in JSON
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
      speed: slow
    command:
      executable: dog
      args:
        - "{action}"
        - --speed
        - "{speed}"
    request:
      validations:
        action:
          type: enum
          choices:
            - walk
            - jump
        speed:
          type: text
```

Notes:

- `route` is the endpoint path.
- `root` is optional. If omitted, `api.default_root` is used.
- `defaults` provides placeholder values for `command.args`.
- `request.validations` defines which placeholders callers may override and enforces type checks.
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

## Request Format

For JSON-only endpoints:

```json
{
  "overrides": {
    "action": "jump",
    "speed": "fast"
  },
  "extra_args": [],
  "stdin": null
}
```

For the example above, a request to `/dogs/walk` still hits the same backend endpoint, but the actual CLI command becomes:

```bash
dog jump --speed fast
```

For upload-enabled endpoints, send `multipart/form-data`:

- `overrides`: JSON object string
- `extra_args`: JSON array string
- `stdin`: optional string
- one file part per configured upload field

Example:

```bash
curl -X POST http://localhost:8000/file2braille/translate \
  -F 'overrides={"table":"en-us-g2.ctb"}' \
  -F 'input_file=@sample.txt'
```

The uploaded file is stored under a unique temporary filename, used in the command, and deleted after the command finishes.

## File Uploads And Outputs

Example YAML:

```yaml
- name: file2braille-translate
  route: /translate
  root: /file2braille
  command:
    executable: file2braille
    args:
      - --table
      - "{table}"
      - "{input_file}"
      - "{output_filename}"
      - "{output_file}"
  defaults:
    table: en-us-g2.ctb
  request:
    validations:
      table:
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
      "path": "/tmp/url2code/outputs/file2braille/file2braille-<uuid>.brf",
      "filename": "file2braille-<uuid>.brf"
    }
  }
}
```

Configured output files are preserved on success and deleted on failed command execution or failed output parsing. If `filename_placeholder` is set, both `{output_file}` and `{output_filename}` are available to the command template.

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

- `400` for invalid override keys or missing placeholder values
- `500` when the executable is missing
- `502` when the command exits non-zero or output parsing fails
- `504` when the command times out

## Container

```bash
docker build -t url2code .
docker run --rm -p 8000:8000 -v "$(pwd)/config:/app/config" url2code
```
