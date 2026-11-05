#!/usr/bin/env bash
# Runs once when the container is created. Everything comes from uv.lock.
set -euo pipefail
cd "$(dirname "$0")/.."

uv python install 3.11 3.12 3.13 3.14
# 3.11 is the floor, and what CI's lint job uses.
uv sync --locked --all-groups --all-extras --python 3.11

# master accepts only verified commits. In Codespaces, GitHub signs them for you once
# "GPG verification" is on for this repository (github.com/settings/codespaces); it then
# sets commit.gpgsign itself. No signing key ever lives in the container.
if [ -n "${CODESPACES:-}" ] && [ "$(git config --get commit.gpgsign || true)" != "true" ]; then
  echo "::warning:: commits here will be unsigned and master will refuse them."
  echo "  Turn on GPG verification for xinp-hub/pydiggs at https://github.com/settings/codespaces,"
  echo "  then rebuild the codespace."
fi
