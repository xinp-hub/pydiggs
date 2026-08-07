"""Console script for pydiggs."""

from __future__ import annotations

import argparse
import sys

from pydiggs import validator


def main(argv: list[str] | None = None) -> int:
    """Console script for pydiggs."""
    parser = argparse.ArgumentParser(
        description=(
            "A Python package for Data Interchange for Geotechnical "
            "and Geoenvironmental Specialists (DIGGS)."
        )
    )
    parser.add_argument(
        "command",
        choices=["schema_check", "schematron_check", "dictionary_check", "context_check"],
        help="Available commands for execution",
    )
    parser.add_argument(
        "diggs_file",
        type=str,
        help="Relative or full path of the DIGGS instance file",
    )
    parser.add_argument(
        "--schema_path",
        type=str,
        default=None,
        help=(
            "Relative or full path of the DIGGS schema file "
            "(default: auto-detect 2.5.a / 2.6 / 3.0.0 from instance NS)"
        ),
    )
    parser.add_argument(
        "--dictionary_path",
        type=str,
        default=None,
        help="Primary DIGGS dictionary file (default: bundled properties.xml)",
    )
    parser.add_argument(
        "--schematron_path",
        type=str,
        default=None,
        help="Schematron schema file (default: bundled DIGGS lxml-adapted rules)",
    )
    parser.add_argument(
        "--output_log",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write validation logs to the current working directory (default: True)",
    )

    args = parser.parse_args(argv)

    # validator is a published lowercase class name; callers rely on keyword args.
    validation = validator(  # type: ignore[no-untyped-call]
        args.diggs_file,
        schema_path=args.schema_path,
        dictionary_path=args.dictionary_path,
        schematron_path=args.schematron_path,
        output_log=args.output_log,
    )

    if args.command == "schema_check":
        ok = validation.schema_check()
    elif args.command == "schematron_check":
        ok = validation.schematron_check()
    elif args.command == "dictionary_check":
        ok = validation.dictionary_check()
    else:
        ok = validation.context_check()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
