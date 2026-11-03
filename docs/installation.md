# Installation

## Prerequisites

pydiggs requires Python 3.11 or later and depends on:

* lxml — XML processing and validation
* rich — colored console output

## Stable release

```bash
uv pip install pydiggs
# or
pip install pydiggs
```

This installs the latest release from PyPI with its runtime dependencies.

Optional extras (for example `uv add "pydiggs[server]"`):

| Extra | Adds | For |
|---|---|---|
| `server` | FastAPI, Starlette, uvicorn, python-multipart | the HTTP API (`pydiggs serve --http`) |

The stdio sidecar (`pydiggs serve --stdio`) needs no extra.

## Development install

Clone the repository and sync with [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/xinp-hub/pydiggs.git
cd pydiggs
uv sync --all-groups
```

Or from a local checkout without groups:

```bash
uv sync
```

## Verification

```bash
uv run python -c "import pydiggs; print(pydiggs.__version__)"
# or, if installed into the ambient environment:
python -c "import pydiggs; print(pydiggs.__version__)"
```
