"""``python -m pydiggs``: the same as the ``pydiggs`` command."""

from __future__ import annotations

from pydiggs import cli

if __name__ == "__main__":
    raise SystemExit(cli.main())
