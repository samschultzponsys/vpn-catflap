"""Normalized, vendor-neutral data model.

Drivers translate vendor APIs/config files *into* these types; everything on
the client side (safety rules, preflight, NetworkManager/Windows backends, the
tray) only ever consumes these types.
"""

from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Site:
    id: str                      # vendor's stable id (UniFi: integration UUID)
    name: str                    # human name
    ref: str = ""                # vendor's internal short ref (UniFi: "default")


@dataclass
class Network:
    name: str
    cidr: str                    # normalized network, e.g. "192.168.10.0/24"
    vlan: int | None = None
    gateway_ip: str | None = None  # the gateway's own address on that network
    id: str = ""
    purpose: str = ""            # "corporate", "guest", "vpn", ...

    @property
    def net(self) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
        return ipaddress.ip_network(self.cidr, strict=False)


@dataclass
class WireGuardServer:
    id: str
    name: str
    enabled: bool
    port: int | None = None
    subnet: str | None = None        # VPN client subnet, e.g. "10.250.1.0/28"
    server_address: str | None = None  # gateway's address inside the VPN subnet
    public_key: str | None = None
    created: bool = True             # False => configured in the UI but never "Created"/applied


@dataclass
class WanInfo:
    wan_ip: str | None = None        # IP the gateway itself sees on its WAN port
    ddns_hostname: str | None = None


@dataclass
class ClientRecord:
    """A peer as it exists on the gateway."""
    id: str
    name: str
    address: str                     # tunnel address, e.g. "10.250.1.2/32"
    public_key: str = ""


@dataclass
class PeerConfig:
    """Normalized WireGuard client config (what a .conf describes)."""
    private_key: str
    addresses: list[str]
    peer_public_key: str
    endpoint_host: str
    endpoint_port: int
    allowed_ips: list[str]
    preshared_key: str | None = None
    dns: list[str] = field(default_factory=list)
    keepalive: int | None = None
    mtu: int | None = None


@dataclass
class Profile:
    """A provisioned/imported site connection as catflap stores it.

    Secrets (private key, PSK) are NOT stored here; they live in the keyring.
    """
    name: str                        # user-facing name, e.g. "home"
    ifname: str                      # sanitized interface name, <= 15 chars
    vendor: str
    addresses: list[str]
    peer_public_key: str
    endpoint_host: str
    endpoint_port: int
    routes: list[str]                # split-tunnel AllowedIPs
    keepalive: int = 25
    gateway: str | None = None       # name of the configured gateway (if API-provisioned)
    site_id: str | None = None
    site_name: str | None = None
    client_id: str | None = None     # peer id on the gateway, for revocation
    device: str | None = None        # device this client belongs to
    vpn_subnet: str | None = None
    ping_target: str | None = None
    networks: list[dict[str, Any]] = field(default_factory=list)
    dns_servers: list[str] = field(default_factory=list)   # only used with dns_domain
    dns_domain: str | None = None    # routing domain, never "~"
    mtu: int | None = None
    has_psk: bool = False
    backend_id: str | None = None    # NM connection UUID / Windows tunnel name
    wan_ip: str | None = None
    warnings: list[str] = field(default_factory=list)
    created: str = ""
    color: str = ""                  # tray colour for this connection, "#RRGGBB"
    label: str = ""                  # friendly display name, e.g. "Ponsys Office"

    @property
    def display(self) -> str:
        return self.label or self.name

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Profile":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})
