#!/usr/bin/env python3
"""Bump catflap to the next MAJOR.MINOR version (1.9 -> 2.0).

Usage: scripts/bump_version.py            # next version
       scripts/bump_version.py --check    # verify all files agree (CI)
       scripts/bump_version.py --tag v1.3 # verify a release tag matches (CI)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from catflap import version as v  # noqa: E402

VERSION_PY = ROOT / "catflap" / "version.py"
SPEC = ROOT / "packaging" / "rpm" / "catflap.spec"


def spec_version() -> str:
    return re.search(r"^Version:\s*(\S+)", SPEC.read_text(), re.M).group(1)


def main(argv: list[str]) -> int:
    cur = v.__version__
    if argv[:1] == ["--check"]:
        if spec_version() != cur:
            print(f"version mismatch: version.py={cur} spec={spec_version()}")
            return 1
        v.parse(cur)
        print(cur)
        return 0
    if argv[:1] == ["--tag"]:
        tag = argv[1]
        if v.fmt(v.parse(tag)) != cur or not tag.startswith("v"):
            print(f"tag {tag} does not match version {cur} (expected v{cur})")
            return 1
        return 0
    new = v.next_version(cur)
    VERSION_PY.write_text(re.sub(r'__version__ = "[^"]+"', f'__version__ = "{new}"', VERSION_PY.read_text()))
    SPEC.write_text(re.sub(r"^(Version:\s*)\S+", rf"\g<1>{new}", SPEC.read_text(), flags=re.M))
    print(f"{cur} -> {new}\nNext: add a CHANGELOG.md entry, commit, then: git tag v{new} && git push --tags")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
