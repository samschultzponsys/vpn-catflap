"""Alta Labs driver (milestone 1: .conf import only).

Alta Labs gateways are managed through the Alta cloud; peer management will be
added once its API is evaluated. Until then a downloaded .conf can be imported
and goes through the same safety rules.
"""

from __future__ import annotations

from .base import Capabilities, Driver


class AltaDriver(Driver):
    vendor = "alta"
    display_name = "Alta Labs"
    capabilities = Capabilities()
