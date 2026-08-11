FROM python:3.14.6-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv

RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /build
COPY requirements.txt /build/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install --requirement /build/requirements.txt

# HIVE's repository QA executes real repository tooling. Keep a current Node
# runtime available without relying on Debian Bookworm's older nodejs package.
FROM node:22-bookworm-slim AS node_runtime

FROM python:3.14.6-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    APP_DIR=backend \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/backend

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node_runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN groupadd --system hive \
    && useradd --system --gid hive --create-home --home-dir /home/hive hive \
    && mkdir -p /app/local-data \
    && chown -R hive:hive /app /home/hive

COPY --from=builder /opt/venv /opt/venv
COPY --chown=hive:hive backend /app/backend
COPY --chown=hive:hive scripts /app/scripts
COPY --chown=hive:hive .env.example /app/.env.example
COPY --chown=hive:hive HIVE-PRODUCTION-SHARED.env /app/HIVE-PRODUCTION-SHARED.env

RUN chmod +x /app/scripts/start.sh

USER hive
EXPOSE 8080
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\", \"8080\")}/livez', timeout=3)" || exit 1

CMD ["/app/scripts/start.sh"]
