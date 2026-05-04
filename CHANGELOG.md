# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: SemVer; major bumps may break.

## [Unreleased]

## [1.0.7] - 2026-05-03

### Added
- `bin/cat-yaml-as-json` -- a small shell + python3
  helper that reads a YAML file and emits a single-line
  JSON document on stdout. Designed to plug into the
  `native_json` output mode for catalog-discovery
  endpoints; downstream images can `cat` a YAML catalog
  through it as a one-line endpoint definition without
  shipping the script themselves.

  The helper lives at `/app/bin/cat-yaml-as-json` in the
  runtime image. Downstream Dockerfiles that need it
  no longer have to copy + chmod their own copy. Existing
  downstream `bin/` directories still layer on top via
  `COPY --chown=url2code:url2code bin /app/bin` --
  Docker COPY adds, so per-service wrappers and the new
  helper coexist.

  Migration for existing downstream images that ship
  their own `bin/cat-yaml-as-json`: delete the local
  copy, drop the chmod for it from the Dockerfile, and
  rebuild. tools.yaml endpoint definitions referencing
  `/app/bin/cat-yaml-as-json` keep working unchanged.

## [1.0.6] - 2026-05-03

### Fixed
- ``GET /`` (the liveness probe) now reports the configured
  ``api.title`` as the ``service`` field instead of the
  hard-coded string ``"url2code"``. Downstream images that
  set ``api.title: needle`` (or anything else) in their
  ``tools.yaml`` will see their own identity in the
  liveness response, which is what monitoring / load
  balancers that pin off ``service`` expect.

  FastAPI's OpenAPI assembly already asserts ``title`` is
  non-empty at app construction, so the field is always a
  real string. A consumer who doesn't set ``api.title`` in
  their YAML gets the AppConfig default
  (``"CLI Tool API"``).

  Surfaced by cobdfamily/needle's first smoke test —
  needle's liveness was reporting ``service=url2code``
  despite the YAML title saying ``needle``. Fix is one
  line in ``main.py``; two new tests in
  ``test_executor.py`` lock the contract.

### Added
- README now documents the conventions a downstream
  image needs to follow:

  - ``COPY --chown=url2code:url2code config /app/config``
    — required to override the base image's bundled
    example ``tools.yaml`` with the consumer's own. A
    sample Dockerfile in the new "Building a downstream
    image" section shows the full shape including
    ``apt-get`` + ``uv pip install`` lines.
  - The ``api.title`` -> liveness ``service`` field
    relationship is called out so consumers know what
    string they're picking when they set the title.

  Both were tribal knowledge before — needle hit the
  config-not-copied trap on its first build.

## [1.0.5] - 2026-05-03

### Added
- ``UploadConfig.name_template`` (optional). When set, the
  uploaded file is saved to
  ``<temp_dir>/<rendered template><.ext>`` instead of
  ``<temp_dir>/<random hex><.ext>``. The template is
  rendered against the same value bag the command args
  see (defaults + validated overrides) — so a YAML like

      uploads:
        - field_name: audio
          placeholder: audio
          temp_dir: /tmp/uploads
          name_template: "{id}"
      request:
        validations:
          id: { type: text }

  saves a request with form field ``id=tt0123456`` to
  ``/tmp/uploads/tt0123456.<ext>``.

  Wraps a use-case from cobdfamily/needle: the audfprint
  CLI records the upload's on-disk filename as the entry
  name in its fingerprint database, and the random hex
  url2code used by default produced unstable / unusable
  ids. ``name_template`` lets the operator preserve a
  canonical id from the request.

### Security
- The rendered upload name is validated against
  ``^[A-Za-z0-9][A-Za-z0-9._-]*$`` and rejects anything
  with ``/``, ``..``, leading dots, spaces, etc. Without
  this, a request smuggling a path-traversal value into
  the template field could write to anywhere the
  service has FS write permission. ``name_template``
  unset preserves the previous random-hex behaviour
  unchanged.

### Tests
- 14 new tests in ``tests/test_executor.py`` cover the
  render helper (random fallback, simple substitution,
  compound templates, missing-field 400, eight unsafe
  inputs, typical canonical ids), plus an end-to-end
  ``execute_endpoint`` test that confirms the templated
  path is what the subprocess gets invoked with.

## [1.0.4] - 2026-05-03

### Tests
- Coverage push from 62% to 90%. The previous suite tested
  ``build_command`` happy paths and the config validators
  but skipped most of ``parser.py``, the
  ``_validate_flag_value`` /``_coerce_override_value``
  branches in ``executor.py``, and the multipart-form +
  JSON-error paths in ``request_parser.py``.

  Added 55 tests across:

  - ``tests/test_parser.py`` (new): full coverage of
    text / native_json / regex_json modes, regex flags
    (IGNORECASE, MULTILINE, DOTALL), single + multiple
    matches, no-match errors, unsupported-flag errors.
  - ``tests/test_executor.py`` (augmented): every
    type-coercion branch on flags and overrides
    (number, bool, enum, text), bool valuePrefix
    rendering, the build_command error paths
    (extra_args toggling, missing placeholders, unknown
    overrides), and execute_endpoint failure paths
    (FileNotFoundError -> 500, OSError -> 500,
    TimeoutExpired -> 504, non-zero return -> 502 with
    structured detail, output parse error -> 502).
  - ``tests/test_request_parser.py`` (augmented):
    invalid / non-dict JSON bodies, empty body
    falling back to query params, multipart
    overrides / extra_args validation (invalid JSON,
    wrong shape), missing-required-upload, upload
    field arriving as a string, non-upload field
    arriving as a file, and the
    uploads-required-without-multipart path.

  parser.py is now at 100% coverage, request_parser.py at
  96%, executor.py at 85%.

### Changed
- ``tool.coverage.report.fail_under`` raised from 60 to 85
  to reflect the new floor. The 5-point buffer absorbs
  short-term drift when new code lands ahead of its tests.

## [1.0.3] - 2026-05-02

### Added
- Health endpoint at ``/`` now returns ``"version"``. Sourced
  from ``app.version`` so it stays in lockstep with
  pyproject.toml.

## [1.0.2] - 2026-05-02

### Fixed
- Coverage gate (`tool.coverage.report.fail_under`) lowered
  from 70% to 60% to match what the current test suite
  actually covers. The 70% gate had been failing CI on every
  push since v1.0.0 (real coverage is ~62%). Tests of the
  parser and the deeper executor branches are still missing
  — raising the gate back to 70% is a follow-up that
  requires writing those tests, not just bumping the number.

## [1.0.1] - 2026-05-02

### Changed
- Liveness endpoint moved from `GET /healthz`
  (`{"status":"ok"}`) to `GET /`
  (`{"service":"url2code","status":"ok"}`) to match the
  cobdfamily microservice fleet convention.
- ReDoc moved from the FastAPI default `/redoc` to
  `/redocs` (note trailing `s`) via `redoc_url` on the
  FastAPI constructor.

## [1.0.0] - 2026-04-28

First containerised release. Brings the project into the
cobdfamily project shape (uv pyproject + src layout +
two-stage uv Dockerfile + CI test/release workflows
publishing to the kibble registry).

### Added
- `pyproject.toml` (uv-managed) replaces
  `requirements.txt`. Dev deps live in
  `[dependency-groups] dev`. `[project.scripts]`
  registers `url2code` as a console entrypoint, plus
  `[tool.coverage.*]` with branch coverage and an
  85% (relaxed to 70%) `fail_under` floor, and
  `[tool.ruff]` config.
- Two-stage `Dockerfile` (uv build -> python:3.12-slim
  runtime, non-root user). uv stays in the runtime
  image so operators can install Python-distributed
  CLI tools url2code wraps.
- `.github/workflows/test.yml`: ruff lint + pytest +
  coverage on every push/PR.
- `.github/workflows/release.yml`: pushes a container
  image to
  `kibble.apps.blindhub.ca/cobdfamily/url2code` on
  every `git tag v*`.
- `CHANGELOG.md` (this file) and `DEPLOYMENT.md`.
- README test-workflow status badge.
- `run()` console entrypoint at
  `url2code.main:run`.

### Changed
- `app/` -> `src/url2code/` (src layout). Imports
  switch from `from app.X` to `from url2code.X`.
- Tests use the new package path; the
  `monkeypatch.setattr("app.executor.subprocess.run", ...)`
  target moves to `url2code.executor.subprocess.run`.

### Fixed
- `app/request_parser.py` was checking
  `isinstance(value, fastapi.UploadFile)` on the
  return of `request.form()`, which is
  `starlette.datastructures.UploadFile`. Since
  `fastapi.UploadFile` is a subclass, the isinstance
  check returned False and every multipart upload
  hit a 400. Switched to importing `UploadFile` from
  `starlette.datastructures` directly.
- `tests/test_request_parser.py` fixture was passing
  raw dicts to `EndpointConfig.model_copy(update=...)`
  which skips validation, so `endpoint.uploads`
  contained dicts instead of `UploadConfig`
  instances. Switched the fixture to construct
  `UploadConfig(...)` directly.

[Unreleased]: https://github.com/cobdfamily/url2code/compare/v1.0.7...HEAD
[1.0.7]: https://github.com/cobdfamily/url2code/compare/v1.0.6...v1.0.7
[1.0.6]: https://github.com/cobdfamily/url2code/compare/v1.0.5...v1.0.6
[1.0.5]: https://github.com/cobdfamily/url2code/compare/v1.0.4...v1.0.5
[1.0.4]: https://github.com/cobdfamily/url2code/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/cobdfamily/url2code/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/cobdfamily/url2code/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/cobdfamily/url2code/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/cobdfamily/url2code/commits/v1.0.0
