"""TP-Link Omada driver (milestone 1: .conf import only).

The Omada Open API (controller 5.9+, OAuth client credentials) exposes sites
and LAN networks; WireGuard peer management will be implemented on top of it in
a later milestone. Until then this driver handles Omada's .conf quirks so an
exported client config can be imported safely.
"""

from __future__ import annotations

from pathlib import Path

from ..model import PeerConfig
from .base import Capabilities, Driver


class OmadaDriver(Driver):
    vendor = "omada"
    display_name = "TP-Link Omada"
    capabilities = Capabilities()

    def parse_config(self, path: str | Path) -> PeerConfig:
        cfg = super().parse_config(path)
        # Omada exports may leave Address without a prefix length.
        cfg.addresses = [a if "/" in a else f"{a}/32" for a in cfg.addresses]
        return cfg
