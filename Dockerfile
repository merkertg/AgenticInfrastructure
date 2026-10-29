# syntax=docker/dockerfile:1

# Base images are pinned by multi-architecture index digest: a tag can move,
# a digest cannot. Update deliberately with docker buildx imagetools inspect.
FROM python:3.12.15-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS python-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# The builder installs runtime dependencies exactly as uv.lock pins them.
FROM python-base AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 /uv /bin/uv

WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy

# Dependencies are a separate layer, so code changes do not reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# The runtime holds no uv, build cache, tests, fixtures, documents or secrets.
FROM python-base AS runtime
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"

# The service runs as a fixed unprivileged user; code stays owned by root.
RUN groupadd --gid 10001 app && \
    useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
# Alembic files serve the separate one-shot migration service.
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
USER app

EXPOSE 8000

# Healthy means storage answers at the schema revision this code expects.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import json, urllib.request; response = urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3); assert json.load(response)['status'] == 'ready'"]

# The exec form passes stop signals straight to Uvicorn.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
