# Installation

pyDIGGS needs **Python 3.11 or later** on Windows, macOS or Linux. Its only required
dependencies are [lxml](https://lxml.de/) (XML parsing and validation) and
[rich](https://rich.readthedocs.io/) (coloured console output for the legacy checks).

## Install from PyPI

=== "uv"

    ```bash
    # Just the command line tool, in its own environment
    uv tool install pydiggs
    pydiggs --version

    # Or as a dependency of your project (import it from your code)
    uv add pydiggs
    uv run pydiggs --version    # project commands run through uv run
    ```

=== "pip"

    ```bash
    python -m pip install pydiggs    # into the active environment
    pydiggs --version
    ```

!!! tip "Use the published wheels"
    The PyPI wheels of lxml bundle a current libxml2. Building lxml against an older
    system libxml2 can change parser behaviour, so prefer the wheels.

## Optional extras

Install an extra only for the integration you use:

| Extra | Adds | For |
|---|---|---|
| `server` | FastAPI, Starlette, uvicorn, python-multipart | the [HTTP API](http-api.md) (`pydiggs serve --http`) |
| `mcp` | the official MCP Python SDK (`mcp` 2.x), python-multipart | the [MCP server](mcp.md) (`pydiggs serve --mcp`) |

=== "uv"

    ```bash
    uv add "pydiggs[server]"
    ```

=== "pip"

    ```bash
    python -m pip install "pydiggs[server]"
    ```

The [stdio sidecar](sidecar.md) (`pydiggs serve --stdio`) needs no extra, and the
[container image](container.md) already contains the `server` extra.

## Exact, tested dependency versions

Every [GitHub release](https://github.com/xinp-hub/pydiggs/releases) has a
`constraints-X.Y.Z.txt` file listing the dependency versions that release was tested
with. Pass it to pip to reproduce them:

```bash
python -m pip install "pydiggs[server]==X.Y.Z" -c constraints-X.Y.Z.txt
```

## Verify a download

Releases are published from GitHub Actions with
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/) and carry PyPI
provenance attestations. The [security policy](https://github.com/xinp-hub/pydiggs/security/policy)
shows how to check a wheel's digest and attestation.

## Install from source

To work on pyDIGGS itself, clone the repository and sync with
[uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/xinp-hub/pydiggs.git
cd pydiggs
uv sync --all-groups
uv run pydiggs --version
```

See [Contributing](contributing.md) for the full development workflow.
