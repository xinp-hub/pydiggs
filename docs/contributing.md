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
