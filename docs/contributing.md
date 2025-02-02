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

3. Install Poetry if you haven't already:
```bash
curl -sSL https://install.python-poetry.org | python3 -
```

4. Install dependencies and set up your development environment:
```bash
poetry install
```

This will create a virtual environment and install all dependencies, including development dependencies.

5. Create a branch for local development:
```bash
git checkout -b name-of-your-bugfix-or-feature
```

6. Make your changes locally. The project uses several development tools:

   * **pytest** for testing
   * **pylint** for code quality
   * **mypy** for type checking
   * **pre-commit** for automated checks

   Install pre-commit hooks:
   ```bash
   poetry run pre-commit install
   ```

7. When you're done making changes:
   * Run tests with pytest:
     ```bash
     poetry run pytest
     ```
   * Check code quality:
     ```bash
     poetry run pylint pydiggs tests
     ```
   * Run type checking:
     ```bash
     poetry run mypy pydiggs
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
3. The pull request should work for Python 3.10 and above.
4. Check that all tests pass in the GitHub Actions CI pipeline.

## Documentation

To build and view the documentation locally:
```bash
poetry run mkdocs serve
```

Then visit `http://127.0.0.1:8000` in your web browser.

## Testing

To run a subset of tests:
```bash
poetry run pytest tests/test_pydiggs.py
```

To run tests with coverage:
```bash
poetry run pytest --cov=pydiggs
```

## Type Checking

The project uses type hints and mypy for type checking:
```bash
poetry run mypy pydiggs
```

## Code Style

The project follows PEP 8 style guidelines. Code quality is enforced using:
* pylint
* pre-commit hooks
* GitHub Actions CI

## Deploying

A reminder for the maintainers on how to deploy:
1. Update HISTORY.md with the new version changes
2. Update version in pyproject.toml:
   ```bash
   poetry version patch  # possible: major / minor / patch
   ```
3. Commit the changes:
   ```bash
   git add pyproject.toml HISTORY.md
   git commit -m "Bump version to x.x.x"
   git push
   ```
4. Create a new release on GitHub with the version number
5. GitHub Actions will automatically deploy to PyPI if tests pass. 