FROM python:3.14.8-slim-bookworm@sha256:44e000a07b48bef1517a2b14adc93c17af728ca98858b5e24c6e1b18de1b2ef2 AS builder

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
FROM node:26.13.0-bookworm-slim@sha256:add5c17c14752c54d2f3d519ec46a3815684d41c33f198bcb09fb998f7dcbf68 AS node_runtime

# Node 26.13.0 bundles npm 11.x; npm 11.19.1 supports this Node release.
RUN npm install --global npm@11.19.1 \
    && npm cache clean --force

# npm 11.19.1 still bundles brace-expansion 5.0.9 and undici 6.28.0, which the
# production Trivy HIGH/CRITICAL gate correctly rejects (CVE-2026-102276,
# CVE-2026-102278 and CVE-2026-19534). No published npm release refreshes those
# transitive copies yet, so replace them in place here. The patched versions
# stay inside their consumers' semver ranges (minimatch's ^5.0.5 for
# brace-expansion, node-gyp's ^6.25.0 for undici), and the assertion fails the
# build if npm's bundled layout ever stops matching this assumption.
RUN npm_dir=/usr/local/lib/node_modules/npm \
    && patch_dir="$(mktemp -d)" \
    && npm pack brace-expansion@5.0.12 undici@6.29.1 --pack-destination "$patch_dir" --silent \
    && for entry in brace-expansion:5.0.12 undici:6.29.1; do \
         name="${entry%%:*}"; version="${entry##*:}"; \
         rm -rf "${npm_dir}/node_modules/${name}"; \
         mkdir -p "${npm_dir}/node_modules/${name}"; \
         tar -xzf "${patch_dir}/${name}-${version}.tgz" -C "${npm_dir}/node_modules/${name}" --strip-components=1; \
       done \
    && rm -rf "$patch_dir" \
    && node -e "const base='/usr/local/lib/node_modules/npm/node_modules'; for (const [name, expected] of [['brace-expansion','5.0.12'],['undici','6.29.1']]) { const {version} = require(base + '/' + name + '/package.json'); if (version !== expected) { throw new Error(name + ' is ' + version + ', expected ' + expected); } }"

FROM python:3.14.8-slim-bookworm@sha256:44e000a07b48bef1517a2b14adc93c17af728ca98858b5e24c6e1b18de1b2ef2 AS runtime

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
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 \
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
