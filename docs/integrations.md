# Choose an integration

Every integration runs the same checks and returns the same
[diggs-findings 1.0](findings.md) document, so you can start with one and move to
another later. Pick by where your code runs:

| You are building | Use | Install | Start it |
|---|---|---|---|
| A Python script, notebook or service | the [Python API](reference.md) | `pydiggs` | `pydiggs.validate(path)` |
| CI checks or batch jobs | the [command line](quickstart.md) | `pydiggs` | `pydiggs validate PATH` |
| A web app or API in any language | the [HTTP API](http-api.md) | `pydiggs[server]` | `pydiggs serve --http` |
| A deployment on Docker or Kubernetes | the [container image](container.md) | `ghcr.io/xinp-hub/pydiggs:X.Y.Z` | `docker run` |
| A desktop or native app (Electron, Tauri, .NET, Java, Rust, …) | the [stdio sidecar](sidecar.md) | `pydiggs` | `pydiggs serve --stdio` |
| An AI assistant or agent | the [MCP server](mcp.md) | `pydiggs[mcp]` | `pydiggs serve --mcp` |
| A web page where files must stay on the user's machine | [Pyodide in the browser](browser.md) | `pydiggs` in Pyodide | a Web Worker |

## How they compare

| | Python API | HTTP API | Container | Stdio sidecar | MCP server | Browser |
|---|---|---|---|---|---|---|
| Runs | in your process | as a server | as a server | as a child process | as a child process | in the page |
| Talks | Python calls | HTTP + JSON | HTTP + JSON | JSON-RPC 2.0 over stdio | MCP over stdio | JS ↔ Python |
| Isolation and time limits | your choice | worker processes | worker processes | worker processes | one worker | a Web Worker |
| Opens a network port | no | yes | yes | no | no | no |
| Contract | [API reference](reference.md) | OpenAPI 3.1 | OpenAPI 3.1 | OpenRPC | MCP tool schemas | [example](browser.md) |

!!! warning "Findings are untrusted text"
    Finding messages quote values from the validated file. Whatever integration you
    use, escape every string before you put it in a web page, a terminal or a prompt.

## Still unsure?

- **One machine, one user, files on disk:** the command line or the Python API.
- **Many users over a network:** the HTTP API, ideally as the container image behind
  your reverse proxy.
- **An app you ship to users' computers:** the stdio sidecar; it needs no open port.
- **Users who cannot send their files anywhere:** the browser build.
