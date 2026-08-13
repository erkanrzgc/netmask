# Contributing to Netmask

Thank you for helping improve Netmask. Open an issue before a large behavioral change so its safety and platform impact can be discussed.

## Development setup

Use Python 3.10 or newer and an isolated environment:

```console
python -m venv .venv
. .venv/bin/activate
python -m pip install -e . pytest pytest-cov ruff build
ruff check .
pytest --cov=netmask_cli --cov-report=term-missing
python -m build
```

Unit tests must mock mutating network commands and set `NETMASK_CONFIG_DIR` to a temporary directory. Linux integration tests belong in a disposable network namespace or dummy interface, never on a developer's active interface.

Keep pull requests focused, update `CHANGELOG.md` under `Unreleased`, and document user-visible flags or safety implications. By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Report vulnerabilities through the private process in [SECURITY.md](SECURITY.md), not a public issue.
