# MCP server

`pydiggs[mcp]` serves pydiggs to AI assistants and other clients of the
[Model Context Protocol](https://modelcontextprotocol.io) (MCP). An MCP host starts
`pydiggs serve --mcp` as a child process and talks to it over stdin and stdout; the
model can then validate a DIGGS document and look up what a rule means.

```bash
pip install 'pydiggs[mcp]'
pydiggs serve --mcp          # the host runs this; it waits for MCP messages on stdin
```

It is built on the official MCP Python SDK (`mcp` 2.x) and works with hosts that speak
either the initialize handshake or the stateless 2026-07-28 protocol.

!!! warning "Finding text is untrusted data from the file"
    The `message`, `location.xpath`, `location.gml_id` and `files[].path` fields of a
    result are copied from the document (or the name the caller gave it). A crafted file
    can put any text there, including text written to look like instructions. Show these
    fields as data; never let a model follow them. The text summary of a result never
    contains them (see [What the model reads](#what-the-model-reads)).

## Connecting a host

Most MCP hosts are configured with a JSON entry naming the command to start. With
pydiggs installed in the environment the host uses:

```json
{
  "mcpServers": {
    "pydiggs": {
      "command": "pydiggs",
      "args": ["serve", "--mcp"]
    }
  }
}
```

Without installing it first, [uv](https://docs.astral.sh/uv/) can fetch and run it:

```json
{
  "mcpServers": {
    "pydiggs": {
      "command": "uvx",
      "args": ["--from", "pydiggs[mcp]", "pydiggs", "serve", "--mcp"]
    }
  }
}
```

Pin a version (`pydiggs[mcp]==1.7.0`) for a repeatable setup. The exact file and key
names depend on the host; check its documentation for "MCP servers" or "stdio servers".

Options go after `--mcp`:

| Option | Default | Meaning |
|---|---|---|
| `--max-bytes N` | 10,485,760 (10 MiB) | largest decoded document |
| `--max-findings N` | 200 | findings kept per document |
| `--timeout SECONDS` | 60 | hard limit per validation (the worker is killed) |
| `--log-level L` | `warning` | `debug`, `info`, `warning`, `error` or `critical` |
| `--log-format F` | `text` | `text` or `json` lines on stderr |

Exit status: 0 when the host closes stdin or the process is interrupted; 2 for bad
arguments, a missing `[mcp]` extra, or stdin/stdout that are not usable.

## Tools

All four tools are read-only, with the annotations `readOnlyHint: true`,
`destructiveHint: false`, `idempotentHint: true` and `openWorldHint: false`. None of
them accepts a file path or a URL.

| Tool | Arguments | Result (`structuredContent`) |
|---|---|---|
| `validate_diggs` | `content_base64` (string, required), `name` (string, at most 255 characters), `checks` (array of step names), `diggs_version` (`2.5.a`, `2.6`, `3.0.0`, or the experimental `3.1-dev`) | a whole one-file [diggs-findings 1.0](findings.md) document; the tool's `outputSchema` is the packaged findings schema |
| `explain_rule` | `rule_id` (string, at most 256 characters) | one `RuleInfo`: `id`, `pattern`, `step`, `description`, `severities`, `help_uri`, `known` |
| `list_rules` | `step` (optional step name) | `{"rules": [RuleInfo, ...]}` |
| `supported_versions` | none | `pydiggs_version`, `findings_schema_version`, `findings_schema_url`, `diggs_versions`, `reference_data` |

`validate_diggs` takes the document as standard, padded base64 (no line breaks), so its
bytes, encoding declaration and SHA-256 are exactly those of the file. `name` is only
for display: it is reduced to a basename and becomes `files[].path`. `checks` runs only
the listed steps (`structure`, `schema`, `dictionary`, `schematron`, `geometry`,
`deprecation`, `context`). The result equals `pydiggs validate --format json` on the
same file, apart from `path` and the [limits](#limits) below: at most 200 findings and
512-character messages, where the CLI keeps 10,000 findings and 4,096 characters.

A malformed or invalid document is a normal result with findings, never an error.

### What the model reads

Each result also has a `TextContent` block for the model. For `validate_diggs` it is
built from a fixed template: the verdict, the status of each step, counts per step and
severity, and the rule ids found, followed by a reminder that finding fields are
untrusted. It never contains a message, an XPath, a `gml:id` or the file name, so text
planted in a document does not reach the model through the summary. A client that hands
`structuredContent` to the model gives it the untrusted fields too.

## Limits

| Limit | Value |
|---|---|
| Document size | 10 MiB decoded (`--max-bytes`); checked from the length of the base64 text before anything is decoded |
| Findings | 200 per document (`--max-findings`); a `structure.findings-truncated` finding says when more were dropped |
| Message length | 512 characters; longer messages end in `…` |
| Concurrency | one validation at a time, in one worker process |
| Time | 60 s per validation (`--timeout`); the worker is then killed and replaced |

Only the bundled DIGGS schemas, dictionaries and Schematron rules are used. The server
never reads local dictionaries, never fetches anything over the network, and never
copies document snippets into a result.

## Errors

A problem with the request or the document is a tool result with `isError: true` and
one text block, `pydiggs error <code>: <message>`. For bad arguments the message ends
with `parameter: problem` pairs separated by `; `, and for internal errors with
`(correlation id <hex>)`. Messages are fixed texts: they never repeat a value the
caller sent. An error result has no `structuredContent`, because that must match the
tool's `outputSchema`.

| `code` | When |
|---|---|
| `invalid-parameter` | a missing or wrong argument, or base64 that is not standard and padded |
| `input-too-large` | the document is larger than `--max-bytes` |
| `compressed-input` | the document is gzip-compressed |
| `input-unreadable` | the document could not be read |
| `timeout` | validation did not finish in time; the file was **not checked** |
| `resource-error` | pydiggs's bundled reference data could not be loaded |
| `internal` | anything else; the details are logged to stderr with the `correlation_id` |

An unknown tool name or a malformed MCP request is a JSON-RPC error instead
(`-32602` for an unknown tool).

## stdout and stderr

stdout carries MCP messages only, one JSON message per line. Before the server starts,
pydiggs moves its own stdout to stderr, so a stray `print`, a write from C code or a
worker process cannot corrupt the message stream. Logs go to stderr; a host should read
stderr or send it to a file or the null device, since an unread pipe eventually blocks
the server.

## From Python

`pydiggs.mcp_server.create_server()` returns the SDK's `MCPServer`, for embedding or for
tests with the SDK's in-process client:

```python
import base64

import anyio
from mcp import Client

from pydiggs.mcp_server import create_server


async def main() -> None:
    server = create_server(max_findings=50)
    async with Client(server) as client:
        with open("BH-01.xml", "rb") as fh:
            content = base64.b64encode(fh.read()).decode("ascii")
        result = await client.call_tool(
            "validate_diggs", {"content_base64": content, "name": "BH-01.xml"}
        )
        print(result.content[0].text)


if __name__ == "__main__":  # the worker process is spawned: keep this guard
    anyio.run(main)
```

The streamable HTTP transport is not offered; use [the HTTP API](http-api.md) for
network access.
