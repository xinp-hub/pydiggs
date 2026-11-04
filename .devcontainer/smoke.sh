#!/usr/bin/env bash
# What .github/workflows/devcontainer.yml runs inside the freshly built container.
set -euo pipefail
cd "$(dirname "$0")/.."

uv --version
gh --version | head -1
claude --version
for v in 3.11 3.12 3.13 3.14; do uv python find "$v"; done
uv run python -c "import pydiggs, lxml; print('pydiggs', pydiggs.__version__)"
uv run pydiggs --help >/dev/null
uv run ruff check --quiet .
uv run mypy
