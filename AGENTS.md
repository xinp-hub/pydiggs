# Working on pyDIGGS

Instructions for any coding agent, following the cross-tool `AGENTS.md` convention. A human
contributor should read [CONTRIBUTING.md](CONTRIBUTING.md); this file adds what an agent
needs to know before it proposes or changes anything.

## Scope and standing decisions

What the owner has decided pyDIGGS does and does not do. Read it before proposing or
designing anything. Work outside it is not a judgement call: say that it is outside, and
ask the owner first. If the owner changes the scope, record it here in the same pull
request.

- **DIGGS 3.x only for development.** New features, behaviour fixes, examples, docs and
  requests to the DIGGS maintainers target DIGGS 3.0.0 and later. DIGGS 2.6 and earlier
  are frozen: what already ships for them (validating and reading 2.5.a and 2.6 files)
  stays as it is and its tests keep passing, but nothing new is built for them, and a
  defect that only affects 2.x files is documented, not fixed. Two exceptions: a security
  fix, and a crash on a 2.x file, are fixed whatever the version. Removing 2.x support
  would be a breaking change and is not decided. (2026-11-17: 2.6 and earlier have open
  issues in the standard's own schemas and examples.)
- **DIGGS data work only.** pyDIGGS validates, reads, writes, converts and compares DIGGS
  data. It does no engineering calculation, correlation or classification (no SPT
  corrections, soil behaviour type, USCS and the like). (2026-11-16: those are a different
  product.)
- **What pyDIGGS writes is DIGGS 3.0.0.** The writer and the converters never write 2.6.
  (2026-11-11.)
- **No AGS data dictionary and no AGS example file is bundled.** The AGS4 reader, checker
  and converter work without them; test fixtures are written for the purpose. (2026-11-11:
  the terms for redistributing AGS material are not settled.)
- **Values are carried, never converted.** A converter writes a value in the unit the
  source states, or reports it as not converted. It never multiplies one.

## Set up and checks

```bash
uv sync --all-groups --all-extras     # never pip install; uv owns the environment and uv.lock
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest                         # -m "not slow and not scaling" is the fast loop
```

All four must pass before you report work as done. Paste the real output; do not
paraphrase it. A fix comes with a test that fails without it. A change users will notice
gets a line in [CHANGELOG.md](CHANGELOG.md) under "Unreleased", written for users.

## Non-negotiables

- **Public contracts are versioned.** The Python API, the CLI, and the documents pyDIGGS
  writes (diggs-findings, diggs-model, diggs-diff, diggs-capabilities, the conversion
  mapping table, the HTTP and sidecar protocols) each have a version and a schema. Adding
  is a minor change; changing or removing is breaking. Never change one without asking
  the owner.
- **Never weaken a check to make it pass.** No blanket `# noqa`, no bare `# type: ignore`,
  no lowered coverage floor, no `skip` on a failing test.
- **File content is untrusted.** DIGGS and AGS files come from anywhere. Parse XML only
  through `pydiggs._xml`, and bound the work a file can cause. Escape file text where a
  person reads it: terminal output, human-readable reports and logs. Machine-readable
  output (JSON, SARIF) keeps file data as it is, as its contract says
  (`docs/findings.md`); the consumer escapes it for display.
- **Generated files are regenerated, not edited:** `docs/convert-mapping.md`,
  `openapi.json`, `openrpc.json`, `js/src/generated/`, and the hashes in
  `src/pydiggs/PROVENANCE.toml`. Use the script that owns each one.
- **Commits.** Conventional Commits, one concern per pull request, every commit signed
  off (`git commit -s`). Never add a `Co-authored-by:` or similar trailer naming an AI
  agent, and never commit as an agent identity; CI refuses both.
- **No secrets.** Publishing uses PyPI Trusted Publishing. If you think you need a token,
  the setup is wrong.

## Releasing and merging: the owner decides

Do not tag, push to `master`, merge a pull request, approve one, or publish. Only the
owner releases, from a signed tag. If a check or a gate blocks you, that is the gate
working: explain the situation and stop. Never look for a way around it.
