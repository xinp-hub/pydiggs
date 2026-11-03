"""Version-compatibility helpers (Python 3.11 and later)."""

from __future__ import annotations

import warnings

__all__ = ["_deprecated"]


def _deprecated(msg: str, since: str, removal: str, *, stacklevel: int = 3) -> None:
    """Warn that something is deprecated, naming when it was and when it goes.

    :func:`warnings.deprecated` needs Python 3.13; this works on every supported
    version and gives every pydiggs deprecation the same wording. The default
    ``stacklevel`` points at the caller of the function that calls this.

    Args:
        msg: What is deprecated and what to use instead.
        since: The pydiggs release that deprecated it.
        removal: The release that will remove it.
        stacklevel: As for :func:`warnings.warn`, counted from this function.

    Example:
        >>> import warnings
        >>> with warnings.catch_warnings(record=True) as caught:
        ...     warnings.simplefilter("always")
        ...     _deprecated("old() is deprecated; use new()", "1.3.0", "2.0.0")
        >>> str(caught[0].message)
        'old() is deprecated; use new() (deprecated since pydiggs 1.3.0, removal in 2.0.0)'
    """
    warnings.warn(
        f"{msg} (deprecated since pydiggs {since}, removal in {removal})",
        DeprecationWarning,
        stacklevel=stacklevel,
    )
