"""Secret storage: the system keyring (KWallet/GNOME Keyring via Secret Service,
Windows Credential Manager) with an explicit, permission-restricted file
fallback for headless use (e.g. provisioning from Ansible without a session).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .store import _atomic_write, config_dir

SERVICE = "catflap"


class SecretStore:
    def __init__(self, backend: str = "auto", root: Path | None = None):
        self.root = root or config_dir()
        self._keyring = None
        if backend in ("auto", "keyring") and os.environ.get("CATFLAP_SECRETS") != "file":
            try:
                import keyring  # type: ignore
                from keyring.backends import fail  # type: ignore

                if not isinstance(keyring.get_keyring(), fail.Keyring):
                    self._keyring = keyring
            except Exception:
                self._keyring = None
            if backend == "keyring" and self._keyring is None:
                raise RuntimeError("no usable system keyring found (install python3-keyring)")

    @property
    def backend_name(self) -> str:
        return "keyring" if self._keyring else f"file ({self._file})"

    @property
    def _file(self) -> Path:
        return self.root / "secrets.json"

    def _load(self) -> dict[str, str]:
        if self._file.exists():
            return json.loads(self._file.read_text(encoding="utf-8"))
        return {}

    def get(self, key: str) -> str | None:
        if self._keyring:
            return self._keyring.get_password(SERVICE, key)
        return self._load().get(key)

    def set(self, key: str, value: str) -> None:
        if self._keyring:
            self._keyring.set_password(SERVICE, key, value)
            return
        data = self._load()
        data[key] = value
        _atomic_write(self._file, json.dumps(data, indent=2))

    def delete(self, key: str) -> None:
        if self._keyring:
            try:
                self._keyring.delete_password(SERVICE, key)
            except Exception:
                pass
            return
        data = self._load()
        if data.pop(key, None) is not None:
            _atomic_write(self._file, json.dumps(data, indent=2))


def gateway_key(name: str, what: str) -> str:
    return f"gateway/{name}/{what}"


def profile_key(name: str, what: str) -> str:
    return f"profile/{name}/{what}"
