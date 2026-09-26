FROM python:3.14.7-slim-bookworm@sha256:82bc3c539b8813ada9d68c63b40158fa002f7f33de9bf3312a3dfdc0620dff56 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv

RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /build
COPY requirements.txt /build/requirements.txt
# pip is required only while assembling the virtual environment. Remove it
# afterwards so its bundled build-time libraries cannot become production
# vulnerabilities. HIVE never installs uploaded repository dependencies.
RUN python -m pip install --no-cache-dir --requirement /build/requirements.txt \
    && python -m pip check \
    && python -m pip uninstall --yes pip

# HIVE's repository QA executes real repository tooling. Keep a current Node
# runtime available without relying on Debian Bookworm's older nodejs package.
FROM node:22.23.2-bookworm-slim@sha256:48e4b67d85f87bd551df43704e24d252f56cc5f8e9718841aace50f19948f0f9 AS node_runtime

# Node 22.23.2 bundles npm 10.9.8, whose bundled dependencies trigger the
# production Trivy HIGH/CRITICAL gate. npm 11.19.1 supports this Node release
# and refreshes the affected tar, brace-expansion, ip-address and related
# transitive tooling dependencies while preserving npm/npx for repository QA.
RUN npm install --global npm@11.19.1 \
    && npm cache clean --force

FROM python:3.14.7-slim-bookworm@sha256:82bc3c539b8813ada9d68c63b40158fa002f7f33de9bf3312a3dfdc0620dff56 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    APP_DIR=backend \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/backend

WORKDIR /app

# The base interpreter's pip is also build-time tooling. Its vendored msgpack
# and setuptools copies are not used by HIVE but are correctly visible to the
# production image scanner, so keep the runtime package-manager free.
USER root
RUN python -m pip uninstall --yes pip

# Refresh Debian packages in the final runtime layer. The pinned Python image can
# pre-date Debian security updates even when the Python tag itself is current.
# Trivy is configured to fail on fixable HIGH/CRITICAL findings, so install all
# available Bookworm security fixes before assembling the application image.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# The official Python slim image already includes the CA certificate bundle.
# Keep the runtime build package-manager free: installing git here is unnecessary
# for HIVE's static repository QA and can fail on overlay-backed remote builders
# when dpkg attempts cross-device renames.
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
# Runtime task metadata is loaded from /app/tasks.
# Keep it in the production image so workflow enrichment remains deterministic.
COPY --chown=hive:hive tasks /app/tasks
COPY --chown=hive:hive .env.example /app/.env.example
COPY --chown=hive:hive HIVE-PRODUCTION-SHARED.env /app/HIVE-PRODUCTION-SHARED.env

RUN chmod +x /app/scripts/start.sh

USER hive
EXPOSE 8080
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\", \"8080\")}/livez', timeout=3)" || exit 1

CMD ["/app/scripts/start.sh"]
