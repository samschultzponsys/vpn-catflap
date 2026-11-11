"""Generic driver: any WireGuard gateway, by importing a downloaded .conf."""

from __future__ import annotations

from .base import Capabilities, Driver


class GenericDriver(Driver):
    vendor = "generic"
    display_name = "Generic WireGuard (.conf import)"
    capabilities = Capabilities()
