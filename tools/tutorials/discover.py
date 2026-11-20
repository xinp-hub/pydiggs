"""What a package offers its users, found from the package itself: the gate's inventory.

The manifest's ``discover`` maps a label to a source; every name a source yields becomes
the item ``<label>:<name>``, which some feature must cover with a tutorial or a waiver.
Sources, each read from what the package ships, never from a hand-kept list:

``module:attr``                   an iterable in an importable module (``__all__``, a dict of
                                  subcommands, a tuple of tool names); a callable is called
``{python: module:attr, pick: a.b}``  the same, then a dotted path into the result (via
                                  ``to_dict()`` when it has one); a list on the way is mapped
``{help: [command, ...]}``        the long options each command's ``--help`` prints, as
                                  ``"<command minus its first word> --option"``
``{openapi: path}``               each operation, ``"POST /v1/x"``, and each of its query
                                  parameters, ``"POST /v1/x?name"``
``{openrpc: path}``               each method name
``{npm_exports: path}``           each entry point of a package.json ``exports`` map

So a new option, endpoint, method, tool or entry point fails the gate until it is given a
tutorial or a stated reason not to have one. Matching is exact on purpose: a wildcard
would wave the next new item through without anyone deciding.

Standard library only.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

KINDS = ("python", "help", "openapi", "openrpc", "npm_exports")
_LONG_OPTION = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")
_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "patch", "head", "options"})


class DiscoverError(ValueError):
    """A discovery source is malformed or cannot be read."""


def _value_problem(kind: str, value: Any) -> str:
    if kind == "help":
        if not isinstance(value, list) or not all(isinstance(c, str) and c for c in value):
            return "help lists commands"
        return ""
    if not isinstance(value, str) or not value:
        return f"{kind} names one {'module:attribute' if kind == 'python' else 'file'}"
    return ""


def problems(source: Any) -> list[str]:
    """Why a manifest ``discover`` value is not a valid source (empty when it is)."""
    if isinstance(source, str):
        return [] if ":" in source else [f"{source!r} is not module:attribute"]
    if not isinstance(source, dict):
        return ["a source is module:attribute or a mapping"]
    kinds = [kind for kind in KINDS if kind in source]
    if len(kinds) != 1:
        return [f"a source names exactly one of {', '.join(KINDS)}"]
    extra = set(source) - {kinds[0], "pick"}
    if extra or ("pick" in source and kinds[0] != "python"):
        return [f"unknown keys for {kinds[0]}: {', '.join(sorted(extra or {'pick'}))}"]
    problem = _value_problem(kinds[0], source[kinds[0]])
    return [problem] if problem else []


def _attribute(target: str) -> Any:
    module_name, _, attribute = target.partition(":")
    value = getattr(importlib.import_module(module_name), attribute)
    return value() if callable(value) else value


def _pick(value: Any, path: str) -> list[Any]:
    items = [value]
    for part in path.split("."):
        next_items: list[Any] = []
        for item in items:
            data = item.to_dict() if hasattr(item, "to_dict") else item
            got = data[part] if isinstance(data, dict) else getattr(data, part)
            next_items += got if isinstance(got, list) else [got]
        items = next_items
    return items


def _executable(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    beside = Path(sys.executable).parent / name  # the environment the gate runs in
    if beside.exists():
        return str(beside)
    msg = f"{name} is not on PATH"
    raise DiscoverError(msg)


def _help_options(commands: list[str]) -> set[str]:
    found: set[str] = set()
    for command in commands:
        argv = shlex.split(command)
        result = subprocess.run(  # noqa: S603 - commands come from the repository's manifest
            [_executable(argv[0]), *argv[1:], "--help"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env={**os.environ, "COLUMNS": "200", "NO_COLOR": "1"},
        )
        if result.returncode != 0:
            msg = f"`{command} --help` exited with {result.returncode}: {result.stderr[-300:]}"
            raise DiscoverError(msg)
        prefix = " ".join(argv[1:])
        options = set(_LONG_OPTION.findall(result.stdout)) - {"--help"}
        found |= {f"{prefix} {option}".strip() for option in options}
    return found


def _openapi(document: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for route, item in (document.get("paths") or {}).items():
        shared = [p for p in item.get("parameters", []) if isinstance(p, dict)]
        for method, operation in item.items():
            if method not in _HTTP_METHODS:
                continue
            name = f"{method.upper()} {route}"
            found.add(name)
            for parameter in [*shared, *operation.get("parameters", [])]:
                if parameter.get("in") == "query":
                    found.add(f"{name}?{parameter['name']}")
    return found


def _json(root: Path, relative: str) -> Any:
    path = (root / relative).resolve()
    if root.resolve() not in path.parents:
        msg = f"{relative} is outside the repository"
        raise DiscoverError(msg)
    return json.loads(path.read_text(encoding="utf-8"))


def names(source: str | dict[str, Any], root: Path) -> set[str]:
    """Every name one source yields.

    Raises:
        DiscoverError: the source is malformed or cannot be read.
    """
    found = problems(source)
    if found:
        raise DiscoverError("; ".join(found))
    if isinstance(source, str):
        value: Iterable[Any] = _attribute(source)
        return {str(name) for name in value}
    if "python" in source:
        value = _attribute(source["python"])
        if "pick" in source:
            return {str(name) for name in _pick(value, source["pick"])}
        return {str(name) for name in value}
    if "help" in source:
        return _help_options(source["help"])
    kind = next(k for k in ("openapi", "openrpc", "npm_exports") if k in source)
    return _FILE_SOURCES[kind](_json(root, source[kind]))


def _openrpc(document: dict[str, Any]) -> set[str]:
    return {str(method["name"]) for method in document.get("methods", [])}


def _npm_exports(document: dict[str, Any]) -> set[str]:
    exports = document.get("exports") or {}
    return {str(key) for key in (exports if isinstance(exports, dict) else {".": exports})}


_FILE_SOURCES = {"openapi": _openapi, "openrpc": _openrpc, "npm_exports": _npm_exports}
