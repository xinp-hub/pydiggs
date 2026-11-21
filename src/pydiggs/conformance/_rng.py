"""A small deterministic random source: the same seed gives the same numbers everywhere.

A challenge is a pure function of (suite version, class, seed), on every Python version
and platform, including Pyodide. The standard :mod:`random` module does not promise
that across versions for every method, so the stream here is SHA-256 in counter mode
and every draw is integer arithmetic.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import TypeVar

__all__ = ["Rng"]

_T = TypeVar("_T")


class Rng:
    """Deterministic draws from a text seed.

    Examples:
        >>> a, b = Rng("suite|spt|42"), Rng("suite|spt|42")
        >>> [a.integer(0, 9) for _ in range(5)] == [b.integer(0, 9) for _ in range(5)]
        True
        >>> Rng("x").integer(3, 3)
        3
    """

    def __init__(self, seed: str) -> None:
        self._seed = seed.encode("utf-8")
        self._counter = 0

    def _word(self) -> int:
        block = hashlib.sha256(self._seed + b"|" + str(self._counter).encode("ascii")).digest()
        self._counter += 1
        return int.from_bytes(block[:8], "big")

    def integer(self, low: int, high: int) -> int:
        """An integer from ``low`` to ``high``, both included."""
        if high < low:
            raise ValueError("high must not be below low")
        span = high - low + 1
        # Rejection sampling: no modulo bias, and still deterministic.
        limit = (1 << 64) - ((1 << 64) % span)
        while True:
            word = self._word()
            if word < limit:
                return low + word % span

    def choice(self, items: Sequence[_T]) -> _T:
        """One of ``items``.

        Raises:
            ValueError: ``items`` is empty.
        """
        if not items:
            raise ValueError("cannot choose from an empty sequence")
        return items[self.integer(0, len(items) - 1)]

    def fork(self, label: str) -> Rng:
        """An independent stream named ``label``.

        Adding draws to one part of a scenario then does not move the values of another.
        """
        return Rng(self._seed.decode("utf-8") + "/" + label)
