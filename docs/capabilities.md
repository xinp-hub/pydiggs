# Capabilities

`pydiggs.capabilities()` says what the installed pyDIGGS can do: every operation, how
stable it is, what it reads and gives, its options, the extras it needs, and whether
they are installed. An app that bundles pyDIGGS builds its menus from this answer, so a
new pyDIGGS release reaches the app without hand edits.

- **JSON Schema:**
  [`schemas/diggs-capabilities-1.0.schema.json`](https://xinp-hub.github.io/pydiggs/schemas/diggs-capabilities-1.0.schema.json)
  (also `pydiggs.capabilities_schema()`; `pydiggs.CAPABILITIES_SCHEMA_URL` and
  `pydiggs.CAPABILITIES_SCHEMA_VERSION`).
- **The same document everywhere:** `pydiggs capabilities --json` on the command line,
  [`GET /v1/capabilities`](http-api.md#endpoints) over HTTP, and the
  [`pydiggs://capabilities`](mcp.md#resources) resource of the MCP server.

=== "Python"

    ```python
    import pydiggs

    caps = pydiggs.capabilities()
    for op in caps.operations:
        print(op.id, op.stability, op.available)
    caps.to_dict()  # a diggs-capabilities 1.0 document
    ```

=== "Command line"

    ```console
    $ pydiggs capabilities
    pydiggs 1.8.0
      validate             stable        Check a DIGGS file against the schema, dictionaries and business rules
      read                 stable        Read what a DIGGS file holds: boreholes, samples, tests and results
      ...
      export.gpkg          stable        Save features as a GeoPackage (.gpkg)  (needs pydiggs[gis])
    $ pydiggs capabilities --json
    ```

## The document

```json
{
  "$schema": "https://xinp-hub.github.io/pydiggs/schemas/diggs-capabilities-1.0.schema.json",
  "schema_version": "1.0",
  "pydiggs_version": "1.8.0",
  "operations": [
    {
      "id": "read",
      "label": "Read what a DIGGS file holds: boreholes, samples, tests and results",
      "stability": "stable",
      "inputs": ["diggs-2.5.a", "diggs-2.6", "diggs-3.0.0", "diggs-3.1-dev"],
      "outputs": ["diggs-model-1.0"],
      "options": {
        "results": {
          "type": "boolean",
          "label": "Keep the values of result tables (off: only their columns and size)",
          "default": true,
          "choices": null,
          "required": false
        }
      },
      "needs": [],
      "available": true
    }
  ]
}
```

| field | meaning |
|---|---|
| `id` | a stable identifier: `validate`, `read`, `ags4.check`, `export.gpkg` … |
| `label` | what the operation does, in plain language |
| `stability` | `stable` (Semantic Versioning), `preview` (may change in a minor release) or `experimental` (may change or go at any time) |
| `inputs`, `outputs` | a DIGGS version (`diggs-2.6`), an AGS4 version (`ags4-4.1.1`), a pyDIGGS document (`diggs-findings-1.1`, `diggs-model-1.0`), a media type (`application/geo+json`), or the Python type of an in-memory result (`pandas.DataFrame`) |
| `options` | option name (the keyword argument, and the request parameter where a surface takes it) to its `type` (`boolean`, `integer`, `number`, `string`, `string[]` or `file`), `label`, `default` (`null`: not given), `choices` (`null`: any) and `required` |
| `needs` | the pyDIGGS extras it needs (`pip install 'pydiggs[gis]'`); empty for the core |
| `available` | whether everything it needs is installed in the interpreter that answered |

`available` is worked out when you ask, by looking for each module an operation needs
without importing it. The same wheel answers correctly under CPython with extras and in
the browser ([Pyodide](browser.md)), where only the core is installed and the
data-frame and GIS exports report `available: false`. Over HTTP and MCP the answer is
about the server's own interpreter.

## Operations

| id | stability | needs | Python |
|---|---|---|---|
| `validate` | stable | | `pydiggs.validate()`, `validate_bytes()` |
| `read` | stable | | `pydiggs.read()`, `read_bytes()` |
| `detect` | stable | | `pydiggs.detect_diggs_version()` |
| `rules.list` | stable | | `pydiggs.rule_catalogue()` |
| `rules.explain` | stable | | `pydiggs.explain_rule()` |
| `ags4.read` | preview | | `pydiggs.ags4.read()`, `read_bytes()` |
| `ags4.write` | preview | | `pydiggs.ags4.dumps()`, `write()` |
| `ags4.check` | preview | | `pydiggs.ags4.check()` |
| `convert.ags4-to-diggs` | stable | | `pydiggs.convert.ags4_to_diggs()` |
| `convert.diggs-to-ags4` | stable | | `pydiggs.convert.diggs_to_ags4()`, `model_to_ags4()` |
| `export.pandas` | stable | `frames` | `pydiggs.frames.to_pandas()` |
| `export.polars` | stable | `polars` | `pydiggs.frames.to_polars()` |
| `gis.transform` | stable | `gis` | `pydiggs.gis.transform()` |
| `export.geodataframe` | stable | `gis` | `pydiggs.gis.to_geodataframe()` |
| `export.gpkg`, `export.geoparquet`, `export.geojson` | stable | `gis` | `pydiggs.gis.write()` |

The AGS4 reading, writing and checking operations are a preview; conversion
(`pydiggs.convert`) is stable from pyDIGGS 1.10, both ways. `POST /v1/convert` and the
sidecar's `convert` are either conversion operation, chosen by their `to` parameter. The
experimental DIGGS `3.1-dev` profile is a choice of the `diggs_version` option (see
[Validation checks](usage.md#experimental-diggs-31-dev)).

## Versioning

diggs-capabilities follows the rules of the other pyDIGGS documents. Objects accept
unknown fields, so a 1.0 consumer accepts any 1.x document. A new optional field, a new
operation, or a new token in `inputs`, `outputs` or `needs` is a minor version; a new
`stability` or option `type`, or any other change, is a new major. An operation's `id`
does not change while it is listed; a stable one is removed only in a major release of
pyDIGGS.
