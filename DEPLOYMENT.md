# Deployment

url2code ships as a container image to the kibble
registry on every `git tag v*`. The same image runs any
configured CLI-wrapper API — endpoints are declared in
the per-deployment `config.yaml`.

## Pre-flight checklist

- [ ] Public hostname for url2code (eg.
      `tools.cobd.ca`) with an A record pointing at
      the host. The service speaks plain HTTP on `:8000`
      behind your reverse proxy / TLS terminator.
- [ ] `config.yaml` written for the CLI tools you're
      wrapping (see `config/config.yaml.example`).
- [ ] Each wrapped CLI tool is on the container's
      PATH. The runtime image ships uv so you can add
      Python-distributed CLI tools at container start
      via a wrapper script or sidecar.

## Image distribution

The release workflow at `.github/workflows/release.yml`
builds and pushes the image on every `git tag v*`.
Anonymous push to kibble, no secrets to configure.

```sh
git tag -a v1.0.4 -m "Release 1.0.4"
git push origin v1.0.4
```

Within a couple of minutes:

- `kibble.apps.blindhub.ca/cobdfamily/url2code:1.0.4`
- `kibble.apps.blindhub.ca/cobdfamily/url2code:latest`

## Pin the base image (downstream consumers)

Downstream images (brl, needle, outofoffice, pandoc,
salmon, ...) build `FROM
kibble.apps.blindhub.ca/cobdfamily/url2code:<tag>`. Always
pin `<tag>` — via the Dockerfile's `ARG URL2CODE_TAG` — to a
**released engine version** (e.g. `1.0.8`), never `latest`.

Why: `latest` floats. A consumer built against `:latest` can
change behavior between two otherwise-identical rebuilds the
moment a new engine ships, with nothing in the consumer's own
history to explain it. Pinning makes the build reproducible
and turns an engine upgrade into a deliberate one-line bump
with its own commit, CI run, and changelog entry.

The running engine version is observable two ways: the `/`
liveness response (`version` field) and the
`url2code engine starting` startup log line, which records
both the engine version and the consumer's reported
`api.version`.

## Configure

Mount your `config.yaml` and a writable temp dir for
uploads:

```yaml
services:
  url2code:
    image: kibble.apps.blindhub.ca/cobdfamily/url2code:1.0.4
    container_name: url2code
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"
    environment:
      URL2CODE_CONFIG: /app/config/config.yaml
    volumes:
      - ./config.yaml:/app/config/config.yaml:ro
      - ./uploads:/app/uploads
      - ./outputs:/app/outputs
```

Bring it up:

```sh
mkdir -p /opt/url2code/{uploads,outputs}
chmod 700 /opt/url2code/{uploads,outputs}
cd /opt/url2code
docker compose pull
docker compose up -d
docker compose logs -f url2code
```

Behind your TLS reverse proxy, route
`https://tools.cobd.ca/*` to `127.0.0.1:8000`.

## Verify

```sh
# Liveness — returns the running version too:
# {"service":"url2code","status":"ok","version":"1.0.4"}
curl -fsS https://tools.cobd.ca/

# Readiness — 200 only if every wrapped CLI is installed:
# {"status":"ready","checked":3}
# 503 with a {"missing":[...]} list if a declared tool didn't
# make it into the image. Point your orchestrator's readiness
# check here; keep liveness on `/`.
curl -fsS https://tools.cobd.ca/readyz

# Prometheus metrics (plain-text exposition; point your scraper
# here). Per-process counters — scrape each worker if you run
# more than one.
curl -fsS https://tools.cobd.ca/metrics

# Generated OpenAPI docs:
#   https://tools.cobd.ca/docs    (Swagger UI)
#   https://tools.cobd.ca/redocs  (ReDoc, trailing s)

# Each endpoint declared in your config.yaml is now
# reachable. Exact URL depends on its `route_root` and
# `route` fields.
```

## Routine operations

### Upgrading

```sh
git tag -a v1.0.5 -m "Release 1.0.5"
git push origin v1.0.5
# CI builds and pushes the image.

# Deploy host:
sed -i 's|url2code:[^ ]*|url2code:1.0.5|' docker-compose.yml
docker compose pull
docker compose up -d --no-deps url2code
```

### Adding a wrapped CLI tool at runtime

The runtime image bundles uv. To add a tool:

```sh
docker compose exec --user root url2code uv pip install --system <package>
docker compose restart url2code
```

For a permanent wire-in, fold the install into a
custom downstream image that derives from the kibble
image and adds the extra deps.

### Backups

What must persist:

- `config.yaml` — secrets and the entire route surface
  live here.
- `outputs/` — generated files. If you've configured
  endpoints to save output files, those are
  produced here and need durable storage.

What's safe to lose:

- `uploads/` — temp files for in-flight requests.
- Container logs — ship them to your aggregator.
