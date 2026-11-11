"""Per-user state: gateways and site profiles (JSON, XDG / %APPDATA% paths).

Everything lives in the *user's* config directory; catflap never needs root
for its own state (problem #13).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from .model import Profile


def config_dir() -> Path:
    env = os.environ.get("CATFLAP_CONFIG_DIR")
    if env:
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "catflap"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "catflap"


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Store:
    def __init__(self, root: Path | None = None):
        self.root = root or config_dir()
        self.path = self.root / "state.json"
        self._data: dict[str, Any] = {"gateways": {}, "profiles": {}, "settings": {}}
        self._lock = threading.RLock()
        self._mtime: int | None = None
        self.reload()

    def _file_mtime(self) -> int | None:
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def reload(self) -> None:
        """Re-read the file if something else (e.g. the CLI) changed it since we last
        read or wrote it. Safe to call from a worker thread; the settings dict keeps
        its identity."""
        with self._lock:
            mtime = self._file_mtime()
            if mtime is None or mtime == self._mtime:
                return
            self._load()
            self._mtime = mtime

    def _load(self) -> None:
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        settings = self._data.get("settings", {})
        new = loaded.pop("settings", {})
        settings.update(new)              # never empty, even briefly (read from other threads)
        for k in [k for k in settings if k not in new]:
            settings.pop(k, None)
        self._data.update(loaded)
        self._data["settings"] = settings

    def save(self) -> None:
        with self._lock:
            _atomic_write(self.path, json.dumps(self._data, indent=2, sort_keys=True) + "\n")
            self._mtime = self._file_mtime()

    # gateways ---------------------------------------------------------------
    def gateways(self) -> dict[str, dict[str, Any]]:
        return self._data["gateways"]

    def gateway(self, name: str) -> dict[str, Any]:
        try:
            return self._data["gateways"][name]
        except KeyError:
            raise KeyError(f"no gateway named {name!r} (see 'catflap gateway list')") from None

    def put_gateway(self, name: str, cfg: dict[str, Any]) -> None:
        self._data["gateways"][name] = cfg

    def remove_gateway(self, name: str) -> None:
        self._data["gateways"].pop(name, None)

    # profiles ---------------------------------------------------------------
    def profiles(self) -> dict[str, Profile]:
        return {k: Profile.from_dict(v) for k, v in self._data["profiles"].items()}

    def profile(self, name: str) -> Profile:
        try:
            return Profile.from_dict(self._data["profiles"][name])
        except KeyError:
            raise KeyError(f"no site profile named {name!r} (see 'catflap list')") from None

    def put_profile(self, p: Profile) -> None:
        self._data["profiles"][p.name] = p.to_dict()

    def remove_profile(self, name: str) -> None:
        self._data["profiles"].pop(name, None)

    # settings ---------------------------------------------------------------
    def settings(self) -> dict[str, Any]:
        return self._data["settings"]
