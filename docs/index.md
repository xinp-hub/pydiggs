---
hide:
  - navigation
  - toc
---

<div class="pd-hero" markdown>

# pyDIGGS

<p class="pd-tagline">Validate DIGGS geotechnical and geoenvironmental XML offline:
schema, dictionaries, Schematron business rules and context checks, with findings you
can read, script against or feed to code-scanning tools.</p>

[Get started](quickstart.md){ .md-button .md-button--primary }
[Install](installation.md){ .md-button }

</div>

```bash
pip install pydiggs
pydiggs validate BH-01.xml
```

## Choose your path

<div class="grid cards" markdown>

-   :material-console:{ .lg .middle } __Check files from the command line__

    ---

    One command runs every check on a file or a whole folder, as text, JSON or SARIF.

    [Quickstart](quickstart.md){ .pd-card-link }

-   :material-language-python:{ .lg .middle } __Validate in Python__

    ---

    `validate()` for files and `validate_bytes()` for uploads return a `Report` of
    findings with stable rule ids, lines and XPaths.

    [Findings](findings.md){ .pd-card-link }

-   :material-web:{ .lg .middle } __Run it as a web service__

    ---

    An HTTP API with OpenAPI 3.1, or a hardened, attested container image.

    [HTTP API](http-api.md){ .pd-card-link }

-   :material-application-cog-outline:{ .lg .middle } __Embed it in a desktop app__

    ---

    A stdio JSON-RPC sidecar any host language can drive: Electron, Tauri, .NET, Java,
    Rust and more.

    [Stdio sidecar](sidecar.md){ .pd-card-link }

-   :material-robot-outline:{ .lg .middle } __Connect an AI assistant__

    ---

    An MCP server with read-only validation and rule tools.

    [MCP server](mcp.md){ .pd-card-link }

-   :material-shield-lock-outline:{ .lg .middle } __Keep files on the user's machine__

    ---

    Run pyDIGGS in a web page with Pyodide: the document never leaves the browser.

    [Browser](browser.md){ .pd-card-link }

</div>

Not sure which fits? [Compare the integrations](integrations.md).

## What gets checked

| Step | What it checks |
|---|---|
| Structure | Well-formed XML with a `Diggs` root and one `DocumentInformation` |
| Schema | The bundled DIGGS XSD for **2.5.a**, **2.6** or **3.0.0**, picked from the file's namespace (unknown namespaces use 3.0.0) |
| Dictionary | `codeSpace` values and units against the DIGGS Standard dictionaries from [diggsml.org/def](https://diggsml.org/def/), offline |
| Schematron | DIGGS business rules from [DIGGSml/validation](https://github.com/DIGGSml/validation), adapted to run offline |
| Geometry | `srsName` and `srsDimension` on each geometry object |
| Deprecation | Anything the DIGGS schema marks as deprecated |
| Context | In-document `xlink:href` targets and dataBlock arity |

Everything runs offline by default: no file leaves your machine and pyDIGGS never
contacts a server. The only other files a DIGGS file can make it read are local
dictionaries its `codeSpace` values name inside the file's own folder; turn that off
with `--local-dictionaries off` or `validate(..., local_dictionaries="off")`. See
[Validation checks](usage.md) for each step and its known limits.

!!! tip "Prefer a web application?"
    [DXplorer](https://www.dx-plorer.com) brings pyDIGGS validation to the browser
    without any Python.

!!! info "Stay updated"
    Hear about each feature release by GitHub notification, feed or email: see
    [Stay updated](stay-updated.md).
