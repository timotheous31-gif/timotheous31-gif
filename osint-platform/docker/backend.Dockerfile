# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl build-essential libpq5 \
 && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml backend/README.md ./
COPY backend/app ./app

# The default image doubles as the CI/test image, so it carries the dev extras
# and the test suite. Build with --build-arg INSTALL_DEV=false for a lean
# production image.
ARG INSTALL_DEV=true
RUN if [ "$INSTALL_DEV" = "true" ]; then \
        pip install --no-cache-dir -e ".[dev]"; \
    else \
        pip install --no-cache-dir -e .; \
    fi

COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic
COPY backend/tests ./tests
COPY scripts ./scripts

# Run as an unprivileged user: the service makes outbound requests on behalf of
# investigators and should hold no more privilege than it needs.
RUN useradd --create-home --uid 10001 osint \
 && mkdir -p /srv/data/evidence \
 && chown -R osint:osint /srv
USER osint

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
