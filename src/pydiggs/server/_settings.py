"""``ServerSettings``: the HTTP server's limits and switches. Stdlib only."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

__all__ = ["LOOPBACK_HOSTS", "ServerSettings"]

#: ``allowed_hosts`` when bound to a loopback address and none were given.
LOOPBACK_HOSTS: Final = ("127.0.0.1", "localhost", "[::1]")
_LOOPBACK_BINDS: Final = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
_MAX_TIMEOUT: Final = 86_400
_TRUE: Final = frozenset({"1", "true", "yes", "on"})
_FALSE: Final = frozenset({"0", "false", "no", "off"})

Isolation = Literal["process", "thread"]


def _default_workers() -> int:
    return max(1, min(4, (os.cpu_count() or 1) - 1))


def _positive(name: str, value: object) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive int, got {value!r}")


@dataclass(frozen=True, slots=True, kw_only=True)
class ServerSettings:
    """Limits and switches for ``create_app``, ``create_router`` and ``serve --http``.

    ``queue_depth`` None means twice ``workers``; :attr:`admission_slots` is how many
    requests may be in flight (running or queued) before ``503 busy``. ``isolation``
    None lets the factory choose: ``"process"`` for ``create_app`` and ``serve
    --http``, ``"thread"`` for ``create_router``. Thread isolation has no hard timeout
    and is unsafe for untrusted input. ``allowed_hosts`` None means the loopback names
    when bound to a loopback address, and must be set otherwise (see
    :meth:`hosts_for`). ``"*"`` is refused in ``cors_origins``.

    Example:
        >>> s = ServerSettings(workers=2)
        >>> s.queue_limit, s.admission_slots, s.max_body_bytes == 25 * 2**20
        (4, 6, True)
        >>> ServerSettings(cors_origins=("*",))
        Traceback (most recent call last):
        ...
        ValueError: cors_origins must list origins; '*' is not allowed
    """

    max_body_bytes: int = 25 * 2**20
    max_findings: int = 10_000
    timeout_s: float = 60.0
    workers: int = field(default_factory=_default_workers)
    queue_depth: int | None = None
    isolation: Isolation | None = None
    worker_memory_mb: int = 2048
    cors_origins: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] | None = None
    reject_cross_site: bool = True
    docs_enabled: bool = False
    versions_enabled: bool = True

    def __post_init__(self) -> None:
        for name in ("max_body_bytes", "max_findings", "workers", "worker_memory_mb"):
            _positive(name, getattr(self, name))
        if self.queue_depth is not None and (
            type(self.queue_depth) is not int or self.queue_depth < 0
        ):
            raise ValueError(f"queue_depth must be 0 or more, got {self.queue_depth!r}")
        timeout = self.timeout_s
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not 0 < timeout <= _MAX_TIMEOUT
            or not math.isfinite(timeout)
        ):
            raise ValueError(f"timeout_s must be positive seconds, at most {_MAX_TIMEOUT:,}")
        if self.isolation not in (None, "process", "thread"):
            raise ValueError(f"isolation must be 'process' or 'thread', got {self.isolation!r}")
        for name in ("cors_origins", "allowed_hosts"):
            value = getattr(self, name)
            if value is None and name == "allowed_hosts":
                continue
            if not isinstance(value, tuple) or not all(isinstance(v, str) and v for v in value):
                raise ValueError(f"{name} must be a tuple of non-empty strings")
        if "*" in self.cors_origins:
            raise ValueError("cors_origins must list origins; '*' is not allowed")
        if self.allowed_hosts is not None and "*" in self.allowed_hosts:
            raise ValueError("allowed_hosts must list host names; '*' is not allowed")
        for name in ("reject_cross_site", "docs_enabled", "versions_enabled"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a bool")

    @property
    def queue_limit(self) -> int:
        """Requests that may wait for a worker: ``queue_depth``, or twice ``workers``."""
        return 2 * self.workers if self.queue_depth is None else self.queue_depth

    @property
    def admission_slots(self) -> int:
        """Requests admitted at once (running plus queued); past this, ``503 busy``."""
        return self.workers + self.queue_limit

    def hosts_for(self, bind_host: str) -> tuple[str, ...]:
        """The ``Host`` names to accept when bound to ``bind_host``.

        Raises:
            ValueError: ``allowed_hosts`` is unset and ``bind_host`` is not loopback.

        Example:
            >>> ServerSettings().hosts_for("127.0.0.1")
            ('127.0.0.1', 'localhost', '[::1]')
            >>> ServerSettings(allowed_hosts=("diggs.example",)).hosts_for("0.0.0.0")
            ('diggs.example',)
        """
        if self.allowed_hosts is not None:
            return self.allowed_hosts
        if bind_host in _LOOPBACK_BINDS:
            return LOOPBACK_HOSTS
        raise ValueError(
            f"bound to the non-loopback address {bind_host!r}: set PYDIGGS_ALLOWED_HOSTS"
            " (or allowed_hosts) to the host names clients use"
        )

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None, *, isolation: Isolation | None = None
    ) -> ServerSettings:
        """Settings from ``PYDIGGS_*`` variables; unset ones keep their defaults.

        Variables: ``PYDIGGS_MAX_BODY_BYTES``, ``PYDIGGS_MAX_FINDINGS``,
        ``PYDIGGS_TIMEOUT_S``, ``PYDIGGS_WORKERS``, ``PYDIGGS_QUEUE_DEPTH``,
        ``PYDIGGS_ISOLATION``, ``PYDIGGS_WORKER_MEMORY_MB``, ``PYDIGGS_CORS_ORIGINS``
        and ``PYDIGGS_ALLOWED_HOSTS`` (comma-separated), ``PYDIGGS_REJECT_CROSS_SITE``,
        ``PYDIGGS_DOCS_ENABLED`` and ``PYDIGGS_VERSIONS_ENABLED`` (true/false).

        Args:
            environ: Where to read; default :data:`os.environ`.
            isolation: Used when ``PYDIGGS_ISOLATION`` is unset.

        Raises:
            ValueError: A variable is malformed (the message names it).

        Example:
            >>> ServerSettings.from_env({"PYDIGGS_WORKERS": "3"}).workers
            3
            >>> ServerSettings.from_env({"PYDIGGS_WORKERS": "many"})
            Traceback (most recent call last):
            ...
            ValueError: PYDIGGS_WORKERS: expected an integer
        """
        env = os.environ if environ is None else environ
        values: dict[str, object] = {}
        for name in (
            "max_body_bytes",
            "max_findings",
            "workers",
            "queue_depth",
            "worker_memory_mb",
        ):
            raw = env.get(f"PYDIGGS_{name.upper()}")
            if raw is not None:
                try:
                    values[name] = int(raw.strip())
                except ValueError:
                    raise ValueError(f"PYDIGGS_{name.upper()}: expected an integer") from None
        raw = env.get("PYDIGGS_TIMEOUT_S")
        if raw is not None:
            try:
                values["timeout_s"] = float(raw.strip())
            except ValueError:
                raise ValueError("PYDIGGS_TIMEOUT_S: expected a number of seconds") from None
        values["isolation"] = env.get("PYDIGGS_ISOLATION", isolation)
        for name in ("cors_origins", "allowed_hosts"):
            raw = env.get(f"PYDIGGS_{name.upper()}")
            if raw is not None:
                values[name] = tuple(p.strip() for p in raw.split(",") if p.strip())
        for name in ("reject_cross_site", "docs_enabled", "versions_enabled"):
            raw = env.get(f"PYDIGGS_{name.upper()}")
            if raw is not None:
                word = raw.strip().lower()
                if word not in _TRUE | _FALSE:
                    raise ValueError(f"PYDIGGS_{name.upper()}: expected true or false")
                values[name] = word in _TRUE
        try:
            return cls(**values)  # type: ignore[arg-type]  # each value checked in __post_init__
        except ValueError as exc:
            raise ValueError(f"PYDIGGS_* settings: {exc}") from None
