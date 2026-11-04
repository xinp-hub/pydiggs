# pydiggs HTTP API container image (docs/container.md).
#
# Built from the repository root with the wheel to ship in dist/:
#   uv build --wheel
#   docker buildx build --load -t pydiggs:dev .
# The release job builds the same way, with dist/ holding the exact wheel downloaded
# from PyPI after its sha256 was checked against the PyPI JSON API.
#
# Dependencies come from uv.lock (hashes required); the pydiggs wheel goes in with no
# dependencies of its own. Base images are pinned by multi-arch index digest; Dependabot
# (docker ecosystem, 14-day cooldown) proposes the bumps. The builder and runtime bases
# must carry the same Python patch release (3.14.8 here): the venv links to it.

# ghcr.io/astral-sh/uv 0.13.4 (2026-10-20), Python 3.14.8 on Debian trixie.
FROM ghcr.io/astral-sh/uv:0.13.4-python3.14-trixie-slim@sha256:db955a4c5fd85c9e08ae2c02f3fcedf76765aebc7c9bc10c55a6bbcc1b0510be AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.14

WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY dist/ ./dist/

ARG VERSION=""
# The release job passes the wheel's sha256 from its dist-digests.txt, so the build
# checks the wheel again and the provenance (mode=max) records which wheel it was.
ARG WHEEL_SHA256=""

# 1. The locked [server] dependency set, every file checked against its lock hash.
# 2. The one pydiggs wheel, without dependencies (they are all in step 1).
# 3. Nothing to install packages with is left in the venv.
RUN set -eu; \
    uv export --frozen --no-dev --extra server --no-emit-project \
        --format requirements-txt --output-file /build/requirements.txt; \
    set -- dist/pydiggs-*-py3-none-any.whl; \
    if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then \
        echo "expected exactly one pydiggs wheel in dist/, found: $*" >&2; exit 1; \
    fi; \
    if [ -n "$VERSION" ] && [ "$1" != "dist/pydiggs-${VERSION}-py3-none-any.whl" ]; then \
        echo "wheel $1 is not version $VERSION" >&2; exit 1; \
    fi; \
    if [ -n "$WHEEL_SHA256" ]; then echo "$WHEEL_SHA256  $1" | sha256sum -c -; fi; \
    uv venv /opt/venv; \
    uv pip install --python /opt/venv/bin/python --require-hashes --no-deps \
        -r /build/requirements.txt; \
    uv pip install --python /opt/venv/bin/python --no-deps "$1"; \
    uv pip check --python /opt/venv/bin/python; \
    rm -rf /opt/venv/lib/python3.14/site-packages/pip \
           /opt/venv/lib/python3.14/site-packages/pip-*.dist-info \
           /opt/venv/lib/python3.14/site-packages/setuptools \
           /opt/venv/lib/python3.14/site-packages/setuptools-*.dist-info \
           /opt/venv/bin/pip /opt/venv/bin/pip3*; \
    /opt/venv/bin/pydiggs --version


# python:3.14-slim-trixie, Python 3.14.8 (index digest published 2026-10-06).
FROM python:3.14-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS runtime

ARG VERSION=""
ARG REVISION=""

LABEL org.opencontainers.image.title="pydiggs" \
      org.opencontainers.image.description="Offline validation for DIGGS geotechnical XML (HTTP API)" \
      org.opencontainers.image.source="https://github.com/xinp-hub/pydiggs" \
      org.opencontainers.image.url="https://xinp-hub.github.io/pydiggs/container/" \
      org.opencontainers.image.documentation="https://xinp-hub.github.io/pydiggs/container/" \
      org.opencontainers.image.licenses="Apache-2.0 AND MPL-2.0 AND MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

# No package installer in the image: pip, setuptools and ensurepip go from the base
# Python, and the build fails if any of them can still be imported.
RUN set -eu; \
    rm -rf /usr/local/lib/python3.14/site-packages/pip \
           /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
           /usr/local/lib/python3.14/site-packages/setuptools \
           /usr/local/lib/python3.14/site-packages/setuptools-*.dist-info \
           /usr/local/lib/python3.14/ensurepip \
           /usr/local/bin/pip /usr/local/bin/pip3*; \
    for module in pip setuptools ensurepip; do \
        if python3 -c "import $module" 2>/dev/null; then \
            echo "$module is still importable" >&2; exit 1; \
        fi; \
    done

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN set -eu; \
    for module in pip setuptools ensurepip; do \
        if python -c "import $module" 2>/dev/null; then \
            echo "$module is importable from the venv" >&2; exit 1; \
        fi; \
    done; \
    pydiggs --version

# Numeric, so Kubernetes runAsNonRoot can verify it; no home directory is needed.
USER 10001:10001
WORKDIR /tmp

EXPOSE 8000

# PYDIGGS_ALLOWED_HOSTS must list the host names clients use: bound to 0.0.0.0, the
# server refuses to start without it. The probe sends the first of them as its Host.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as u; host = os.environ.get('PYDIGGS_ALLOWED_HOSTS', 'localhost').split(',')[0].strip() or 'localhost'; u.urlopen(u.Request('http://127.0.0.1:8000/livez', headers={'Host': host}), timeout=4)"]

CMD ["pydiggs", "serve", "--http", "--host", "0.0.0.0", "--port", "8000"]
