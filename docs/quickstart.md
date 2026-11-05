# Quickstart

Validate your first DIGGS file in about five minutes. You need Python 3.11 or later and
a DIGGS instance file (any `.xml` file in DIGGS 2.5.a, 2.6 or 3.0.0).

## 1. Install

=== "uv"

    ```bash
    uv tool install pydiggs      # the pydiggs command, in its own environment
    ```

=== "pip"

    ```bash
    pip install pydiggs
    ```

Check it worked:

```bash
pydiggs --version
```

More options, including the optional extras, are on [Installation](installation.md).

## 2. Validate a file

```bash
pydiggs validate BH-01.xml
```

pyDIGGS detects the DIGGS version from the file's namespace and runs every check:
structure, schema, dictionary, Schematron, geometry, deprecation and context. The first
line is the verdict; each line after it is one finding with its line number, severity
and rule id:

```text
BH-01.xml: not-valid: 2 error(s), 29 warning(s), 20 info
  BH-01.xml:12: error schema.cvc-complex-type.2.4: Element '{http://diggsml.org/schemas/2.6}businessAssociate': This element is not expected. ...
  BH-01.xml:121: warning geometry.srs-attributes: Geometry: <PointLocation> has no srsName and srsDimension itself; ...
```

The exit status tells scripts what happened:

| Exit | Meaning |
|---|---|
| `0` | no errors (warnings and notes are allowed) |
| `1` | at least one error |
| `2` | a usage problem, or a file that could not be read |

!!! tip "Validate a whole folder"
    Pass a directory and pyDIGGS checks every `*.xml` file in it, recursively:
    `pydiggs validate data/`.

## 3. Look up a finding

Every finding has a stable **rule id**, such as `schema.cvc-complex-type.2.4` or
`dictionary.5`. The [rule ids](findings.md#rule-ids) section says what each family
means and how severe it is; the same ids appear in every output format, so you can
filter or suppress them reliably.

## 4. Use it from Python

`uv tool install` keeps pyDIGGS in its own environment, so your Python cannot import
it. Add it to the environment your code runs in first:

=== "uv"

    ```bash
    uv add pydiggs        # in your project; run scripts with: uv run python script.py
    ```

=== "pip"

    ```bash
    python -m pip install pydiggs
    ```

Then:

```python
import pydiggs

report = pydiggs.validate("BH-01.xml")  # prints nothing, writes no files
print(report.ok, report.counts)  # True {'error': 0, 'warning': 29, 'info': 20}

for finding in report.findings:
    print(finding.line, finding.severity, finding.rule_id, finding.message)
```

For an upload or any other document already in memory, use
`pydiggs.validate_bytes(data)`. Both are described in the
[Python API reference](reference.md).

## 5. Add it to CI

Write SARIF and code-scanning tools (GitHub, GitLab, Azure DevOps) show each finding on
the line it refers to:

```bash
pydiggs validate --format sarif --output pydiggs.sarif data/
```

Or write the [diggs-findings 1.0](findings.md) JSON document for your own tooling:

```bash
pydiggs validate --format json BH-01.xml > findings.json
```

## Next steps

<div class="grid cards" markdown>

-   :material-check-decagram-outline: __Understand the checks__

    ---

    What each step checks, how to pin a DIGGS version, and the known limits.

    [Validation checks](usage.md){ .pd-card-link }

-   :material-code-json: __Work with findings__

    ---

    The JSON and SARIF formats, rule ids, limits and privacy defaults.

    [Findings](findings.md){ .pd-card-link }

-   :material-puzzle-outline: __Integrate pyDIGGS__

    ---

    A web service, a desktop app, an AI assistant or the browser.

    [Choose an integration](integrations.md){ .pd-card-link }

</div>
