# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: SemVer; major bumps may break.

## [Unreleased]

## [2.0.0rc1] - 2026-06-01

Release candidate for 2.0 — the async executor. Tagged as an RC to
bake before GA (streaming I/O lands in 2.0.0).

### Changed (BREAKING)
- **Async subprocess executor.** `execute_endpoint` is now a
  coroutine and runs the wrapped CLI via
  `asyncio.create_subprocess_exec` + `asyncio.wait_for` instead of
  the blocking `subprocess.run`. Concurrent long-running
  conversions (LibreOffice, pandoc, audfprint) no longer pin a
  worker / block the event loop — a slow request on one endpoint
  doesn't stall every other in-flight request. The route handler
  now `await`s it.
- Semantics preserved exactly: missing executable → 500, non-zero
  exit → 502 (structured `{exit_code, stderr}`), timeout → 504 (the
  child is killed and reaped), parse failure → 502. stdout/stderr
  are decoded UTF-8 with `errors="replace"` so a tool emitting
  stray bytes can't 500 the request.

### Why the major bump
- `execute_endpoint` went from a sync function to a coroutine —
  breaking for anything importing/calling it directly. The HTTP
  contract is unchanged; no YAML/config changes. `ENGINE_VERSION`
  `1.7.0 -> 2.0.0rc1`.

## [1.7.0] - 2026-06-01

### Added
- **Route path parameters.** An endpoint whose `route` contains
  `{name}` segments (e.g. `/Systems/{id}`) now exposes each
  captured segment as a command-arg placeholder (`{id}`) and in
  the response-template context as `request.<name>`. Path params
  merge into the value bag last, so the URL's resource identifier
  wins for a same-named placeholder; each is run through the
  endpoint's `validations` entry when one is declared (type /
  enum / number coercion) and passed through as text otherwise.
  This lets one endpoint serve a family of resources — the
  enabler for multi-member Redfish collections in
  cobdfamily/salmon.

### Changed
- `ENGINE_VERSION` `1.6.0 -> 1.7.0`.
- `ToolRequest` gains a `path_params` field; `main.py` populates
  it from `request.path_params` (Starlette captures route matches
  even when the handler doesn't declare them in its signature).

### Compatibility
- Additive: endpoints whose route has no `{...}` segments are
  unchanged.

## [1.6.0] - 2026-06-01

### Added
- **Optional OpenTelemetry tracing.** When
  `OTEL_EXPORTER_OTLP_ENDPOINT` (or
  `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`) is set, the engine exports
  spans over OTLP/HTTP: a request span per call (`<METHOD> <path>`,
  attributes `url2code.endpoint`, `http.request.method`,
  `http.response.status_code`) and a child `cli.execute` span
  (attributes `cli.executable`, `cli.exit_code`). With no endpoint
  set — or `OTEL_SDK_DISABLED=true` — spans are the OTel API no-op
  and cost essentially nothing. Standard `OTEL_*` env vars
  configure endpoint / headers / resource the usual way.

### Changed
- `ENGINE_VERSION` `1.5.0 -> 1.6.0`.
- New `url2code.otel` module; the route handler opens the request
  and `cli.execute` spans.
- **First runtime dependencies added since 1.0.0:**
  `opentelemetry-api`, `opentelemetry-sdk`,
  `opentelemetry-exporter-otlp-proto-http`. Everything before this
  stayed stdlib + FastAPI; tracing is the feature that justified
  the deps.

### Compatibility
- Tracing is off unless an OTLP endpoint is configured, so
  behavior is unchanged for every existing consumer. Opt in by
  setting the env on the container.

## [1.5.0] - 2026-06-01

### Added
- **Prometheus metrics at `GET /metrics`** — hand-rolled text
  exposition (format 0.0.4), no new runtime dependency. Series:
    * `url2code_requests_total{endpoint,status}` — counter of
      every endpoint request by final HTTP status (200, 413,
      429, 502, 504, 500, ...).
    * `url2code_in_flight_requests{endpoint}` — gauge of
      concurrent in-flight requests.
    * `url2code_request_duration_seconds{endpoint}` — histogram
      of CLI wall time (observed only when the command ran),
      buckets 0.05s–30s plus `_sum` / `_count`.
  Plain text, so it's screen-reader / CLI friendly with no
  dashboard required. The infra routes (`/`, `/readyz`,
  `/metrics`) are not themselves counted.

### Changed
- `ENGINE_VERSION` `1.4.0 -> 1.5.0`.
- The route handler now records every request — success or raised
  `HTTPException` — by final status, tracks in-flight in a
  `finally`, and observes CLI duration when the command ran.
- New `url2code.metrics` module (thread-safe, dependency-free
  registry + exposition renderer).

### Compatibility
- Additive: `/metrics` is a new route; nothing else changes.
  Counters are per process — with multiple workers each exposes
  its own series (same multi-worker caveat as the rate limiter).

## [1.4.0] - 2026-06-01

### Added
- **Readiness probe, split from liveness.** New `GET /readyz`
  probes every endpoint's `command.executable` (a path-like value
  must exist and be executable; a bare name must resolve on
  `PATH`) and returns `200 {"status":"ready","checked":N}` when
  all are present, or `503 {"status":"not ready","missing":[...]}`
  when any is absent. `GET /` stays liveness-only — the process
  is up — and always reports `200`. This catches a downstream
  image that declared an endpoint but never installed the CLI,
  before it 500s on first real request.
- **Graceful drain on shutdown.** `run()` now sets uvicorn's
  `timeout_graceful_shutdown` from `URL2CODE_DRAIN_SECONDS`
  (default 30). On SIGTERM uvicorn stops accepting new requests
  and waits up to that window for in-flight CLI runs to finish,
  so a rolling deploy doesn't kill a mid-conversion request. A
  shutdown log line marks the drain.

### Changed
- `ENGINE_VERSION` `1.3.0 -> 1.4.0`.
- `config` gains `resolve_executable` + `missing_executables`
  helpers (pure, unit-tested) backing the readiness check.

### Compatibility
- Additive: `/readyz` is a new route and the drain default
  preserves prior shutdown behavior within the 30s window.
  Existing consumers need no changes; they can wire `/readyz`
  into their compose / orchestrator healthcheck when they next
  roll a release.

## [1.3.0] - 2026-06-01

### Added
- **Optional rate limiting + request-size caps.** New
  `limits:` block, settable fleet-wide at the top level and
  overridable per endpoint (field-by-field):
    * `limits.max_request_bytes` — reject a request whose
      `Content-Length` exceeds the cap with `413`, before the
      body is read to disk. Uploads dominate body size, so
      this bounds them too. A client that omits `Content-
      Length` bypasses the check (the reverse proxy is the
      backstop there).
    * `limits.rate_limit: {requests, window_seconds}` — an
      in-process token bucket keyed per (endpoint, client IP).
      Over-limit requests get `429` with a `Retry-After`
      header. With multiple uvicorn workers each worker holds
      its own buckets, so the effective ceiling is ~N x the
      configured rate; use the reverse proxy for a hard,
      coordinated global limit.
  Client IP comes from the left-most `X-Forwarded-For` hop
  (url2code runs behind a TLS proxy), falling back to the
  socket peer.
- New `url2code.ratelimit` module (token bucket + client-IP
  helper), plus `config.effective_limits` to resolve the
  app-default / per-endpoint merge.

### Changed
- `ENGINE_VERSION` `1.1.1 -> 1.3.0`.

### Compatibility
- Fully backwards compatible: a config without a `limits:`
  block (and an endpoint without one) behaves exactly as
  1.1.x — every existing consumer keeps working unchanged.

### Note
- **1.2.0 is intentionally skipped.** It was reserved for
  optional API-key auth, which the fleet cancelled (auth is
  gated at the reverse proxy by design). The next engine minor
  after 1.1.x is therefore 1.3.0.

## [1.1.1] - 2026-06-01

### Added
- Startup log line recording the resolved engine version.
  `build_application` now emits an `url2code engine starting`
  log carrying `engine_version` (the hard-coded
  `ENGINE_VERSION`) and `reported_version` (the consumer's
  `api.version` when set, else the engine version). An
  operator can confirm from the logs which engine build a
  downstream image is running, not just from the `/` liveness
  probe.

### Changed
- `ENGINE_VERSION` `1.1.0 -> 1.1.1`.

### Docs
- DEPLOYMENT.md: new "Pin the base image (downstream
  consumers)" section. Downstream images must pin
  `URL2CODE_TAG` to a released engine version (e.g. `1.0.8`),
  never `latest`, so a base-image change can't silently alter
  a consumer's behavior between rebuilds.

### Note
- First tagged release since 1.0.8. It also ships the 1.1.0
  response-shape templating work (see the 1.1.0 entry below),
  which was merged to main but never tagged.

## [1.1.0] - 2026-05-26

### Added
- **Optional response-shape templating.** New
  `output.template` field on each endpoint config.
  When set, the route handler renders that JSON-shape
  template against the run context and returns the
  result as the response body, instead of the default
  `ToolResponse` envelope. Endpoints without
  `output.template` keep the classic envelope --
  brl, needle, outofoffice, and pandoc all keep
  working unchanged.

  Substitution rules:
    * Whole-leaf form `"{path.to.value}"` -- the leaf
      is replaced with the native value at that path
      (type preserved: int stays int, list stays
      list).
    * Embedded form `"prefix-{x}-{y}-suffix"` -- each
      `{path}` is substituted with the stringified
      value; the result is a string.
    * Dicts and lists in the template render
      recursively.
    * Path resolution walks dicts by key and objects
      by attribute -- pydantic models work without
      `model_dump()` first.
    * Unknown paths raise `TemplateRenderError`,
      surfaced as a 500 with the template error AND
      the raw envelope in the response body. Loud
      failure beats silent half-correct output.

- New `output.template_content_type` field (default
  `application/json`). Downstream surfaces with their
  own media type (Redfish uses
  `application/redfish+json`, HAL uses
  `application/hal+json`) override here.

- New `output.template_static` field (default `{}`).
  YAML-side dict of fixed values surfaced under
  `static.<key>` in templates -- OData / Redfish
  boilerplate that doesn't belong in `parsed_output`.

### Internal
- `_request_template_values` in executor.py renamed
  to public `request_template_values` so main.py can
  build the response-template context without
  reaching into a private name.

### Engine version
- `ENGINE_VERSION` bumps `1.0.8 -> 1.1.0`. Additive
  feature, no breaking changes for existing
  consumers.

## [1.0.8] - 2026-05-22

### Changed
- `ApiConfig.version` is now `Optional[str]` (default
  `None`) instead of `"0.1.0"`. When unset in YAML, the /
  liveness response reports the hardcoded
  `ENGINE_VERSION` (the url2code engine's own version).
  When set, the response reports the consumer's
  identity.

  Before: every downstream image (cobdfamily/needle,
  cobdfamily/pandoc, ...) reported url2code's
  hardcoded `"1.0.7"` regardless of what consumer was
  actually running. Operators couldn't tell which
  build of needle was up by hitting `/`.

  After: needle (and any other consumer) sets
  `api.version: "0.2.0"` (or whatever its current tag
  is) in its tools.yaml. Liveness reports that. Falls
  back to the engine version for the no-override path.

### Tests
- Two new tests in `test_executor.py` covering the
  override behaviour: explicit `api.version` wins;
  unset `api.version` reports the engine version.

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

[Unreleased]: https://github.com/cobdfamily/url2code/compare/v2.0.0rc1...HEAD
[2.0.0rc1]: https://github.com/cobdfamily/url2code/compare/v1.7.0...v2.0.0rc1
[1.7.0]: https://github.com/cobdfamily/url2code/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/cobdfamily/url2code/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/cobdfamily/url2code/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/cobdfamily/url2code/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/cobdfamily/url2code/compare/v1.1.1...v1.3.0
[1.1.1]: https://github.com/cobdfamily/url2code/compare/v1.0.8...v1.1.1
[1.0.7]: https://github.com/cobdfamily/url2code/compare/v1.0.6...v1.0.7
[1.0.6]: https://github.com/cobdfamily/url2code/compare/v1.0.5...v1.0.6
[1.0.5]: https://github.com/cobdfamily/url2code/compare/v1.0.4...v1.0.5
[1.0.4]: https://github.com/cobdfamily/url2code/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/cobdfamily/url2code/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/cobdfamily/url2code/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/cobdfamily/url2code/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/cobdfamily/url2code/commits/v1.0.0
