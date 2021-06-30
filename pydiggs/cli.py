"""Console script for pydiggs."""
import argparse
import sys

from pydiggs import validator
from rich import print as rprint


def main():

    """Console script for pydiggs."""

    command_list = ['schema_check', 'schematron_check']

    parser = argparse.ArgumentParser(description='A Python package for Data Interchange for Geotechnical and Geoenvironmental Specialists (DIGGS).')
    parser.add_argument('command', choices=command_list, help='Available commands for execution')
    parser.add_argument('diggs_file', type=str, nargs=1, help='Relative or full path of the DIGGS instance file')
    parser.add_argument('--schema_path', type=str, nargs=1, help='Relative or full path of the DIGGS schema file')
    parser.add_argument('--schematron_path', type=str, nargs=1, help='Relative or full path of DIGGS schematron schema file')

    args = parser.parse_args()

    if args.command == 'schema_check':
        validation = validator(args.diggs_file[0])
        if args.schema_path is not None:
            validation = validator(args.diggs_file[0], schema_path = args.schema_path[0])      
        validation.schema_check()

    if args.command == 'schematron_check' and args.schematron_path is not None:
        validation = validator(args.diggs_file[0], schematron_path = args.schematron_path[0])
        validation.schematron_check()

if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
