"""Console script for pydiggs."""
import argparse
import sys

from pydiggs import validation
from rich import print as rprint


def main():

    """Console script for pydiggs."""

    command_list = ['check']

    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=command_list)
    parser.add_argument('file', type=str, nargs=1)

    args = parser.parse_args()

    print(args.command)
    print(args.file[0])
    if args.command == 'check':
        validation(args.file[0])


if __name__ == "__main__":
