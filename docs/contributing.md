# Contributing

Contributions are welcome, and they are greatly appreciated! Every little bit helps, and credit will always be given.

You can contribute in many ways:

## Types of Contributions

### Report Bugs

Report bugs at [https://github.com/xinp-hub/pydiggs/issues](https://github.com/xinp-hub/pydiggs/issues).

If you are reporting a bug, please include:

* Your operating system name and version.
* Any details about your local setup that might be helpful in troubleshooting.
* Detailed steps to reproduce the bug.

### Fix Bugs

Look through the GitHub issues for bugs. Anything tagged with "bug" and "help wanted" is open to whoever wants to implement it.

### Implement Features

Look through the GitHub issues for features. Anything tagged with "enhancement" and "help wanted" is open to whoever wants to implement it.

### Write Documentation

pydiggs could always use more documentation, whether as part of the official pydiggs docs, in docstrings, or even on the web in blog posts, articles, and such.

### Submit Feedback

The best way to send feedback is to file an issue at [https://github.com/xinp-hub/pydiggs/issues](https://github.com/xinp-hub/pydiggs/issues).

If you are proposing a feature:

* Explain in detail how it would work.
* Keep the scope as narrow as possible, to make it easier to implement.
* Remember that this is a volunteer-driven project, and that contributions are welcome :)

## Development Setup

### In a dev container (recommended)

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/xinp-hub/pydiggs?quickstart=1)

The repository ships a dev container (`.devcontainer/`) with uv, Python 3.11–3.14, the
GitHub CLI and Claude Code, all pinned. Open it in GitHub Codespaces from the badge, or
locally with *Dev Containers: Reopen in Container*. Nothing is installed on your machine,
so any computer with a browser gives you the same environment, and `uv sync` has already
run when it opens.

Commit signing in Codespaces: open
[github.com/settings/codespaces](https://github.com/settings/codespaces), turn on **GPG
verification** and add this repository. GitHub then signs commits made in your codespaces
and shows them as **Verified**; no signing key is copied into the container. To run the
same checks as CI inside it: `uv run ruff check .`, `uv run mypy`, `uv run pytest`.

### On your own machine

Ready to contribute? Here's how to set up `pydiggs` for local development.

1. Fork the `pydiggs` repo on GitHub.

2. Clone your fork locally:
```bash
git clone git@github.com:your_name_here/pydiggs.git
cd pydiggs
```

3. Install [uv](https://docs.astral.sh/uv/) if you haven't already:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

4. Sync the environment (reads `uv.lock`):
```bash
uv sync --all-groups
```

5. Create a branch for local development:
```bash
git checkout -b name-of-your-bugfix-or-feature
```

6. Make your changes locally. The project uses:

   * **pytest** for testing
   * **ruff** for lint and format
   * **mypy** for type checking
   * **pre-commit** for automated checks (optional locally)

   Install pre-commit hooks:
   ```bash
   uv run pre-commit install
   ```

7. When you're done making changes:
   * Run tests:
     ```bash
     uv run pytest
     ```
   * Lint and format:
     ```bash
     uv run ruff check .
     uv run ruff format .
     ```
   * Type check:
     ```bash
     uv run mypy src
     ```

8. Commit your changes and push your branch to GitHub:
```bash
git add .
git commit -m "Your detailed description of your changes."
git push origin name-of-your-bugfix-or-feature
```

9. Submit a pull request through the GitHub website.

## Pull Request Guidelines

Before you submit a pull request, check that it meets these guidelines:

1. The pull request should include tests.
2. If the pull request adds functionality:
   * Add docstrings to new functions/classes
   * Update the documentation under `docs/`
   * Add the feature to the list in README.md
3. The pull request should work for Python 3.11 and above.
4. Check that all tests pass in the GitHub Actions CI pipeline.
5. Sign your commits. `master` only accepts commits that GitHub shows as **Verified**,
   so an unsigned pull request cannot be merged even when it is approved and green. SSH
   signing is the simplest setup: register an SSH key on GitHub as a *signing* key, then
   run `git config --global gpg.format ssh`, point `user.signingkey` at the public key,
   and set `commit.gpgsign` to `true`.

## How Pull Requests Are Merged

Every pull request gets a `merge policy` check that puts it in one of three risk tiers.
The tier is worked out from the change itself — who made it, which files it touches and
what the diff contains — never from an automated reviewer's opinion.

* **Tier 0** — Dependabot updates that change only dependency versions: patch and minor
  updates, plus 0.x minor updates of development-only dependencies. These merge on their
  own once CI passes.
* **Tier 1** — small changes by the maintainers to source code, tests or documentation.
  An automated reviewer reads the change, and if it approves and CI passes, the pull
  request merges on its own.
* **Tier 2** — everything else: changes to CI workflows, packaging or dependencies by
  hand, GitHub Actions updates, larger changes, and every pull request from a fork. A
  maintainer reviews and merges these.

Merging never releases the package: release tags and PyPI uploads always need a
maintainer. A merge to `master` does rebuild and redeploy this documentation site.

## Documentation

To build and view the documentation locally:
```bash
uv sync --group docs
uv run mkdocs serve
```

Then visit `http://127.0.0.1:8000` in your web browser.

## Testing

To run a subset of tests:
```bash
uv run pytest tests/test_pydiggs.py
```

To run tests with coverage:
```bash
uv run pytest --cov=pydiggs --cov-report=term-missing
```

## Type Checking

The project uses type hints and mypy (strict) for type checking:
```bash
uv run mypy src
```

## Code Style

The project follows ruff's formatter and linter. Style is enforced with:
* ruff (lint + format)
* GitHub Actions CI

## Deploying

A reminder for the maintainers on how to deploy:

1. Update `CHANGELOG.md` and bump `version` in `pyproject.toml`.
2. Tag a release (`vX.Y.Z`) and push the tag (HG3 / HG4 in the FORGE process).
3. GitHub Actions `publish.yml` builds with a fixed `SOURCE_DATE_EPOCH` and publishes to PyPI via Trusted Publishing (OIDC; no API token). The `pypi` environment requires a human reviewer.
