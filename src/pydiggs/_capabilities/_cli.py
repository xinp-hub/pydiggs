"""``pydiggs capabilities``: what this pydiggs can do, as text or as diggs-capabilities JSON."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from pydiggs._capabilities import capabilities

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    """The ``pydiggs capabilities`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="pydiggs capabilities",
        description=(
            "List what this pydiggs can do: each operation, how stable it is, what it"
            " reads and gives, and whether the extras it needs are installed."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print a diggs-capabilities 1.0 document (pydiggs.capabilities().to_dict())",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``pydiggs capabilities``; exit 0 (2 on a usage error, from argparse).

    Examples:
        >>> main(["--json"])  # doctest: +ELLIPSIS
        {
          "$schema": "https://xinp-hub.github.io/pydiggs/schemas/diggs-capabilities-1.0.schema.json",
          ...
        0
    """
    args = build_parser().parse_args(argv)
    caps = capabilities()
    if args.json:
        print(json.dumps(caps.to_dict(), indent=2, ensure_ascii=False))
        return 0
    width = max(len(op.id) for op in caps.operations)
    print(f"pydiggs {caps.pydiggs_version}")
    for op in caps.operations:
        state = "" if op.available else f"  (needs pydiggs[{','.join(op.needs)}])"
        print(f"  {op.id:<{width}}  {op.stability:<12}  {op.label}{state}")
    return 0
