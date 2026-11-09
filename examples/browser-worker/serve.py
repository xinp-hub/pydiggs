"""Serve the browser example on http://127.0.0.1:8000/ with its security headers.

A ``<meta>`` Content-Security-Policy covers the page only. A worker takes its policy
from the worker script's own response headers, so this server sends one for
``worker.mjs`` as well. Any static host works the same way: send ``WORKER_CSP`` with
``worker.mjs`` and ``PAGE_CSP`` with the page.

    python3 serve.py                      # installs pydiggs from PyPI
    python3 serve.py --wheel ../../dist/pydiggs-1.5.0-py3-none-any.whl

Standard library only; it binds to localhost and is not a production server.
"""

from __future__ import annotations

import argparse
import contextlib
import http.server
from pathlib import Path

HERE = Path(__file__).resolve().parent
CDN = "https://cdn.jsdelivr.net"
PYPI = "https://pypi.org https://files.pythonhosted.org"

PAGE_CSP = (
    "default-src 'none'; script-src 'self'; worker-src 'self'; style-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
# 'wasm-unsafe-eval' compiles WebAssembly; blob: is the SRI-checked pyodide.mjs;
# connect-src is Pyodide's files and packages (CDN) and pydiggs (PyPI, or 'self' for
# a local wheel). For a self-hosted Pyodide, replace the CDN with 'self'.
WORKER_CSP = (
    f"default-src 'none'; script-src 'self' blob: {CDN} 'wasm-unsafe-eval'; "
    f"connect-src 'self' {CDN} {PYPI}"
)
FILES = {"index.html", "app.mjs", "worker.mjs", "style.css"}


class Handler(http.server.SimpleHTTPRequestHandler):
    """Serves only the example's files and the optional wheel, with headers."""

    wheel: Path | None = None

    extensions_map = {  # noqa: RUF012  # the base class declares it this way
        ".html": "text/html; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".whl": "application/octet-stream",
    }

    def translate_path(self, path: str) -> str:
        """Map ``/`` and the four files to this directory, the wheel to ``/wheels/``."""
        name = path.split("?", 1)[0].lstrip("/") or "index.html"
        if self.wheel is not None and name == f"wheels/{self.wheel.name}":
            return str(self.wheel)
        return str(HERE / name) if name in FILES else str(HERE / "missing")

    def end_headers(self) -> None:
        """Add the CSP for the page or the worker, and a few hardening headers."""
        worker = self.path.split("?", 1)[0].endswith("/worker.mjs")
        self.send_header("Content-Security-Policy", WORKER_CSP if worker else PAGE_CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main() -> None:
    """Parse the options and serve until interrupted."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--wheel", type=Path, help="a local pure pydiggs wheel to install")
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}/"
    if args.wheel is not None:
        Handler.wheel = args.wheel.resolve(strict=True)
        url += f"?wheel=/wheels/{Handler.wheel.name}"
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Serving the pydiggs browser example on {url}")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
