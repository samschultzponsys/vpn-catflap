#!/usr/bin/env python3
"""Regenerate python-deps.json (pinned wheels + sha256 from PyPI) for the Flatpak.

flatpak-builder builds offline, so every dependency is listed as a file
source. Run this when bumping a dependency or the runtime's Python version:

    packaging/flatpak/gen-python-deps.py --python 313 > packaging/flatpak/python-deps.json
"""

from __future__ import annotations

import argparse
import json
import urllib.request

# name -> pinned version (keep in sync with pyproject.toml's lower bounds)
PINS = {
    "keyring": "25.7.0",
    "SecretStorage": "3.5.0",
    "jeepney": "0.9.0",
    "jaraco.classes": "3.4.0",
    "jaraco.functools": "4.6.0",
    "jaraco.context": "6.1.2",
    "more-itertools": "11.2.1",
    "cryptography": "50.0.2",
    "cffi": "2.1.1",
    "pycparser": "3.12",
}
ARCHES = {"x86_64": "x86_64", "aarch64": "aarch64"}


def pick(files: list[dict], py: str, arch: str) -> dict | None:
    wheels = [f for f in files if f["packagetype"] == "bdist_wheel"]
    pure = [f for f in wheels if f["filename"].endswith("-none-any.whl")]
    if pure:
        return pure[0]
    tags = (f"cp{py}-cp{py}-", "-abi3-")
    for f in wheels:
        n = f["filename"]
        if any(t in n for t in tags) and "manylinux" in n and arch in n and "musl" not in n:
            if "abi3" in n and int(n.split("-cp")[1].split("-")[0]) > int(py):
                continue
            return f
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default="313", help="runtime CPython version, e.g. 313")
    a = ap.parse_args()
    sources = []
    for name, ver in PINS.items():
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{ver}/json") as r:
            files = json.load(r)["urls"]
        seen = set()
        for arch in ARCHES:
            f = pick(files, a.python, arch)
            if not f:
                raise SystemExit(f"no wheel for {name} {ver} cp{a.python} {arch}")
            if f["filename"] in seen:
                continue
            seen.add(f["filename"])
            src = {"type": "file", "url": f["url"], "sha256": f["digests"]["sha256"]}
            if not f["filename"].endswith("-none-any.whl"):
                src["only-arches"] = [arch]
            sources.append(src)
    module = {
        "name": "python-deps",
        "buildsystem": "simple",
        "build-commands": [
            "pip3 install --no-index --no-deps --find-links=. --prefix=${FLATPAK_DEST} *.whl"
        ],
        "sources": sources,
    }
    print(json.dumps(module, indent=2))


if __name__ == "__main__":
    main()
