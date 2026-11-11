"""The vendor driver interface.

A driver talks to one gateway (controller/console) and translates its API and
.conf quirks into the normalized model in :mod:`catflap.model`. Drivers never
touch the local machine: no NetworkManager, no routes, no files outside what
they are handed.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from .. import netutil, wgconf
from ..model import ClientRecord, Network, PeerConfig, Site, WanInfo, WireGuardServer


class DriverError(RuntimeError):
    """A gateway-side problem, with a human-readable message."""


class NotSupported(DriverError):
    """The vendor API cannot do this; fall back to importing a .conf."""


@dataclass(frozen=True)
class Capabilities:
    list_sites: bool = False
    list_networks: bool = False
    read_server: bool = False
    create_client: bool = False
    revoke_client: bool = False
    wan_info: bool = False


@dataclass
class GatewayConfig:
    """What the user configured with ``catflap gateway add``."""
    name: str
    vendor: str
    url: str = ""
    username: str | None = None
    fingerprint: str | None = None
    ca_file: str | None = None
    insecure: bool = False
    options: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> "GatewayConfig":
        known = set(cls.__dataclass_fields__) - {"name"}
        return cls(name=name, **{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "name"}


class Driver(abc.ABC):
    vendor: ClassVar[str]
    display_name: ClassVar[str]
    capabilities: ClassVar[Capabilities] = Capabilities()

    def __init__(self, gw: GatewayConfig, secrets: dict[str, str | None]):
        self.gw = gw
        self.secrets = secrets

    # -- discovery -----------------------------------------------------------
    def list_sites(self) -> list[Site]:
        raise NotSupported(f"{self.display_name}: listing sites is not supported")

    def list_networks(self, site: Site) -> list[Network]:
        raise NotSupported(f"{self.display_name}: listing networks is not supported")

    def get_wireguard_server(self, site: Site) -> WireGuardServer:
        raise NotSupported(f"{self.display_name}: reading the WireGuard server is not supported")

    def get_wan_info(self, site: Site) -> WanInfo:
        return WanInfo()

    # -- peers ---------------------------------------------------------------
    def list_clients(self, site: Site, server: WireGuardServer) -> list[ClientRecord]:
        raise NotSupported(f"{self.display_name}: listing VPN clients is not supported")

    def create_client(self, site: Site, server: WireGuardServer, device_name: str,
                      public_key: str, address: str) -> ClientRecord:
        """Register a peer whose key pair was generated on this device."""
        raise NotSupported(
            f"{self.display_name}: creating VPN clients via the API is not supported; "
            "create the client in the gateway UI and use 'catflap import'")

    def revoke_client(self, site: Site, server: WireGuardServer, client_id: str) -> None:
        raise NotSupported(
            f"{self.display_name}: revoking clients via the API is not supported; "
            "delete the client in the gateway UI")

    # -- config files ----------------------------------------------------------
    def parse_config(self, path: str | Path) -> PeerConfig:
        """Parse a downloaded client .conf. Override to handle vendor quirks."""
        return wgconf.parse_file(path)

    # -- helpers ---------------------------------------------------------------
    @staticmethod
    def allocate_address(server: WireGuardServer, used: list[str]) -> str:
        """Pick a free /32 in the server's VPN subnet (skipping the gateway's own)."""
        if not server.subnet:
            raise DriverError("the WireGuard server has no VPN subnet configured")
        used = list(used) + ([server.server_address] if server.server_address else [])
        return netutil.free_host(server.subnet, used) + "/32"

    def find_site(self, name_or_id: str | None) -> Site:
        sites = self.list_sites()
        if name_or_id is None:
            if len(sites) == 1:
                return sites[0]
            raise DriverError("this gateway has several sites; pick one with --site: "
                              + ", ".join(s.name for s in sites))
        for s in sites:
            if name_or_id in (s.id, s.name, s.ref) or s.name.lower() == name_or_id.lower():
                return s
        raise DriverError(f"site {name_or_id!r} not found; available: "
                          + ", ".join(s.name for s in sites))
