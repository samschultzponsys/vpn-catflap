"""Encrypted export/import bundles for moving catflap to another PC.

Format: JSON with a scrypt-derived key and AES-256-GCM (``cryptography``).
A plaintext bundle is possible but must be asked for explicitly.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

FORMAT = "catflap-bundle"
FORMAT_VERSION = 1
_SCRYPT = {"n": 2**15, "r": 8, "p": 1}


class BundleError(ValueError):
    pass


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise BundleError("encrypted export needs the 'cryptography' package "
                          "(Fedora: python3-cryptography, Debian: python3-cryptography, "
                          "pip: cryptography)") from None
    return AESGCM


def _kdf(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=n, r=r, p=p, dklen=32,
                          maxmem=128 * 1024 * 1024)


def seal(payload: dict[str, Any], passphrase: str | None) -> str:
    if passphrase is None:
        return json.dumps({"format": FORMAT, "version": FORMAT_VERSION, "encrypted": False,
                           "payload": payload}, indent=2)
    if len(passphrase) < 8:
        raise BundleError("use a passphrase of at least 8 characters")
    AESGCM = _aesgcm()
    salt, nonce = os.urandom(16), os.urandom(12)
    key = _kdf(passphrase, salt, **_SCRYPT)
    ct = AESGCM(key).encrypt(nonce, json.dumps(payload).encode(), FORMAT.encode())
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return json.dumps({"format": FORMAT, "version": FORMAT_VERSION, "encrypted": True,
                       "kdf": {"name": "scrypt", "salt": b64(salt), **_SCRYPT},
                       "cipher": "AES-256-GCM", "nonce": b64(nonce), "data": b64(ct)}, indent=2)


def is_encrypted(text: str) -> bool:
    try:
        return bool(json.loads(text).get("encrypted"))
    except (ValueError, AttributeError):
        raise BundleError("not a catflap export file") from None


def open_bundle(text: str, passphrase: str | None) -> dict[str, Any]:
    try:
        doc = json.loads(text)
    except ValueError:
        raise BundleError("not a catflap export file") from None
    if doc.get("format") != FORMAT:
        raise BundleError("not a catflap export file")
    if doc.get("version", 0) > FORMAT_VERSION:
        raise BundleError("this export was made by a newer catflap; update first")
    if not doc.get("encrypted"):
        return doc["payload"]
    if passphrase is None:
        raise BundleError("this export is encrypted; a passphrase is required")
    AESGCM = _aesgcm()
    k = doc["kdf"]
    key = _kdf(passphrase, base64.b64decode(k["salt"]), k["n"], k["r"], k["p"])
    try:
        pt = AESGCM(key).decrypt(base64.b64decode(doc["nonce"]), base64.b64decode(doc["data"]),
                                 FORMAT.encode())
    except Exception:
        raise BundleError("wrong passphrase or corrupted export file") from None
    return json.loads(pt)
