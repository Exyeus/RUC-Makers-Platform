# syntax=docker/dockerfile:1
FROM node:22.14.0-bookworm-slim AS node
FROM python:3.12.9-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /bin/uv
COPY --from=node /usr/local/ /usr/local/
ARG REVISION=unknown
LABEL org.opencontainers.image.revision=$REVISION
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH=/usr/src/app/.venv/bin:$PATH EVY_RUNNING_ENVIRONMENT=production
WORKDIR /usr/src/app
RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential gettext make libmagic1 libpq5 libcairo2 libpango-1.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY app/pyproject.toml app/uv.lock ./
RUN uv sync --locked --no-default-groups --no-install-project
COPY deployment/requirements-s3.txt /tmp/requirements-s3.txt
RUN uv pip install -r /tmp/requirements-s3.txt
COPY app/ ./
RUN npm install -g esbuild@0.25.0 && make npminstall \
    && python manage.py compilemessages -i .venv \
    && python manage.py compilejsi18n \
    && EVY_DOCS_BUILD=1 python manage.py collectstatic --noinput \
    && EVY_DOCS_BUILD=1 python manage.py compress --force
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app app \
    && mkdir -p /data /static && chown -R app:app /data /static eventyay/data
USER app
# No migrations, plugin installation or asset compilation on application startup.
ENTRYPOINT []
CMD ["gunicorn", "eventyay.config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "300"]
