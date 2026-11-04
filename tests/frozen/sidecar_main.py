"""Entry script for the frozen-sidecar CI smoke (docs/sidecar-hosts.md, verbatim).

Built with: pyinstaller --onedir --console --name pydiggs --collect-data pydiggs sidecar_main.py
"""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from pydiggs.cli import main

    sys.exit(main(["serve", "--stdio", *sys.argv[1:]]))
