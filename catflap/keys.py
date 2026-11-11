"""WireGuard (Curve25519) key generation.

Keys are generated on the client so the private key never leaves the device.
Uses ``cryptography`` when installed; otherwise a small pure-Python RFC 7748
implementation (fast enough: key generation happens once per provisioning).
"""

from __future__ import annotations

import base64
import binascii
import os

_P = 2**255 - 19
_A24 = 121665


def _clamp(k: bytes) -> int:
    b = bytearray(k)
    b[0] &= 248
    b[31] &= 127
    b[31] |= 64
    return int.from_bytes(b, "little")


def _x25519(k: bytes, u: bytes) -> bytes:
    """RFC 7748 scalar multiplication (Montgomery ladder)."""
    scalar = _clamp(k)
    x1 = int.from_bytes(u, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in reversed(range(255)):
        kt = (scalar >> t) & 1
        swap ^= kt
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = kt
        a, b = (x2 + z2) % _P, (x2 - z2) % _P
        aa, bb = a * a % _P, b * b % _P
        e = (aa - bb) % _P
        c, d = (x3 + z3) % _P, (x3 - z3) % _P
        da, cb = d * a % _P, c * b % _P
        x3 = (da + cb) ** 2 % _P
        z3 = x1 * (da - cb) ** 2 % _P
        x2 = aa * bb % _P
        z2 = e * (aa + _A24 * e) % _P
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


_BASEPOINT = (9).to_bytes(32, "little")


def public_from_private(private_b64: str) -> str:
    raw = decode_key(private_b64)
    try:
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives import serialization

        pub = X25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    except ImportError:
        pub = _x25519(raw, _BASEPOINT)
    return base64.b64encode(pub).decode()


def generate_keypair() -> tuple[str, str]:
    """Return ``(private_b64, public_b64)``."""
    raw = bytearray(os.urandom(32))
    raw[0] &= 248
    raw[31] &= 127
    raw[31] |= 64
    priv = base64.b64encode(bytes(raw)).decode()
    return priv, public_from_private(priv)


def decode_key(b64: str) -> bytes:
    try:
        raw = base64.b64decode(b64.strip(), validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"not a valid WireGuard key: {e}") from None
    if len(raw) != 32:
        raise ValueError("not a valid WireGuard key: must decode to 32 bytes")
    return raw


def is_valid_key(b64: str) -> bool:
    try:
        decode_key(b64)
        return True
    except ValueError:
        return False
