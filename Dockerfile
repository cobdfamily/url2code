# Two-stage build: uv builds the venv from the lockfile, runtime
# image is python:3.12-slim with the venv copied in. uv stays in
# the runtime image so operators can install Python-distributed CLI
# tools at runtime via the same toolchain url2code wraps.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-default-groups

COPY src ./src
RUN uv sync --frozen --no-default-groups


FROM python:3.12-slim AS runtime

# Install uv in the runtime image too so per-deployment CLI tools
# (the things url2code wraps) can be added at container start.
RUN apt-get update -y \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && curl -LsSf https://astral.sh/uv/install.sh | sh \
 && mv /root/.local/bin/uv /usr/local/bin/uv

RUN groupadd --system --gid 1000 url2code \
 && useradd --system --uid 1000 --gid 1000 \
       --home /app --shell /sbin/nologin url2code

WORKDIR /app
COPY --from=builder --chown=url2code:url2code /app /app
COPY --chown=url2code:url2code config /app/config

# Helper scripts shared by every url2code-pattern downstream
# image. Today: cat-yaml-as-json -- reads a YAML file and
# emits a JSON document on stdout, designed to plug into the
# `native_json` output mode for catalog-discovery endpoints.
# Downstream images can layer their own scripts into /app/bin
# without clobbering these (Docker COPY adds, doesn't delete).
COPY --chown=url2code:url2code bin /app/bin
USER root
RUN chmod 0755 /app/bin/cat-yaml-as-json
USER url2code

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER url2code
EXPOSE 8000

CMD ["uvicorn", "url2code.main:app", "--host", "0.0.0.0", "--port", "8000"]
