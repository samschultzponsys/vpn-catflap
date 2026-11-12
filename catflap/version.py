"""catflap version and the MAJOR.MINOR rollover scheme.

Versions are always ``MAJOR.MINOR``: a minor number from 0 to 9. After ``.9``
the next release rolls over to the next major (1.9 -> 2.0). There are no
patch numbers; every release, however small, gets the next version.
"""

from __future__ import annotations

import re

__version__ = "1.1"

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d)$")


class VersionError(ValueError):
    pass


def parse(version: str) -> tuple[int, int]:
    """Parse ``"1.4"`` or ``"v1.4"`` into ``(1, 4)``. Rejects anything else."""
    m = _VERSION_RE.match(version.strip())
    if not m:
        raise VersionError(
            f"invalid version {version!r}: expected MAJOR.MINOR with minor 0-9 (e.g. 1.4)"
        )
    return int(m.group(1)), int(m.group(2))


def fmt(v: tuple[int, int]) -> str:
    return f"{v[0]}.{v[1]}"


def next_version(version: str) -> str:
    """Return the version after ``version``: 1.1 -> 1.2, 1.9 -> 2.0."""
    major, minor = parse(version)
    if minor >= 9:
        return fmt((major + 1, 0))
    return fmt((major, minor + 1))


def is_newer(candidate: str, current: str) -> bool:
    return parse(candidate) > parse(current)
