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
git tag -a v1.0.1 -m "Release 1.0.1"
git push origin v1.0.1
```

Within a couple of minutes:

- `kibble.apps.blindhub.ca/cobdfamily/url2code:1.0.1`
- `kibble.apps.blindhub.ca/cobdfamily/url2code:latest`

## Configure

Mount your `config.yaml` and a writable temp dir for
uploads:

```yaml
services:
  url2code:
    image: kibble.apps.blindhub.ca/cobdfamily/url2code:1.0.0
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
# Liveness
curl -fsS https://tools.cobd.ca/

# Each endpoint declared in your config.yaml is now
# reachable. Exact URL depends on its `route_root` and
# `route` fields.
```

## Routine operations

### Upgrading

```sh
git tag -a v1.0.1 -m "Release 1.0.1"
git push origin v1.0.1
# CI builds and pushes the image.

# Deploy host:
sed -i 's|url2code:[^ ]*|url2code:1.0.1|' docker-compose.yml
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
