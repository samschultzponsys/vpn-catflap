"""Vendor driver registry."""

from __future__ import annotations

from .base import Capabilities, Driver, DriverError, GatewayConfig, NotSupported
from .alta import AltaDriver
from .generic import GenericDriver
from .omada import OmadaDriver
from .unifi import UniFiDriver

DRIVERS: dict[str, type[Driver]] = {
    d.vendor: d for d in (UniFiDriver, OmadaDriver, AltaDriver, GenericDriver)
}


def get_driver_class(vendor: str) -> type[Driver]:
    try:
        return DRIVERS[vendor]
    except KeyError:
        raise DriverError(f"unknown vendor {vendor!r}; known: {', '.join(DRIVERS)}") from None


__all__ = ["Capabilities", "DRIVERS", "Driver", "DriverError", "GatewayConfig",
           "NotSupported", "get_driver_class"]
