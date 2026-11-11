"""Linux backend: NetworkManager over D-Bus (no nmcli / iproute2 needed).

Used inside the Flatpak sandbox (which has no nmcli or ip) and anywhere nmcli
is missing. It talks to org.freedesktop.NetworkManager on the system bus with
``jeepney`` (pure Python). The sandbox only needs
``--system-talk-name=org.freedesktop.NetworkManager``; NetworkManager's polkit
rules authorize the changes for the desktop user, as with nmcli (problem #13).

The connection is created directly from settings, so no .conf (and no private
key) is ever written to disk by catflap.
"""

from __future__ import annotations

import socket
import struct
import uuid as _uuid
from pathlib import Path
from typing import Any, Protocol

from .. import netutil
from ..model import PeerConfig, Profile
from .base import (Backend, BackendError, LocalAddress, LocalNetwork, LocalRoute,
                   TunnelStatus, run)
from .networkmanager import check_settings

NM = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
SETTINGS_PATH = "/org/freedesktop/NetworkManager/Settings"
IF_SETTINGS = NM + ".Settings"
IF_CONN = NM + ".Settings.Connection"
IF_ACTIVE = NM + ".Connection.Active"
IF_DEVICE = NM + ".Device"
IF_IP4 = NM + ".IP4Config"
ACTIVATED = 2


class Bus(Protocol):
    def call(self, path: str, interface: str, method: str, signature: str = "",
             args: tuple = ()) -> tuple: ...

    def get(self, path: str, interface: str, prop: str) -> Any: ...


class JeepneyBus:
    """Minimal blocking system-bus client for NetworkManager."""

    def __init__(self) -> None:
        try:
            from jeepney.io.blocking import open_dbus_connection
        except ImportError:
            raise BackendError("the D-Bus backend needs the 'jeepney' package "
                               "(Fedora: python3-jeepney)") from None
        try:
            self._conn = open_dbus_connection(bus="SYSTEM")
        except OSError as e:
            raise BackendError(f"cannot reach the system D-Bus: {e}") from e

    def call(self, path, interface, method, signature="", args=()):
        from jeepney import DBusAddress, new_method_call
        from jeepney.wrappers import DBusErrorResponse, unwrap_msg
        addr = DBusAddress(path, bus_name=NM, interface=interface)
        msg = new_method_call(addr, method, signature or None, tuple(args))
        try:
            return unwrap_msg(self._conn.send_and_get_reply(msg, timeout=30))
        except DBusErrorResponse as e:
            text = " ".join(str(x) for x in (e.data or ()))
            if "PermissionDenied" in e.name or "NotAuthorized" in text:
                text += ("\nPolkit denied the change. catflap must run as your desktop user in "
                         "an active session; do not run it with sudo.")
            raise BackendError(f"NetworkManager {method}: {e.name}: {text}") from None

    def get(self, path, interface, prop):
        (sig, value), = self.call(path, "org.freedesktop.DBus.Properties", "Get", "ss",
                                  (interface, prop))
        return value


def _ip4_u32(ip: str) -> int:
    # NetworkManager's legacy "ipv4.dns" is an array of uint32 in network byte order.
    return struct.unpack("=I", socket.inet_aton(ip))[0]


def _plain(settings: dict) -> dict:
    """Strip D-Bus variant signatures: {'ipv6': {'method': ('s', 'x')}} -> {'ipv6': {'method': 'x'}}."""
    return {g: {k: (v[1] if isinstance(v, tuple) and len(v) == 2 else v) for k, v in d.items()}
            for g, d in settings.items()}


def build_settings(profile: Profile, cfg: PeerConfig, conn_uuid: str) -> dict:
    """The NetworkManager connection, with catflap's safe defaults (#3-#7)."""
    peer: dict[str, tuple] = {
        "public-key": ("s", cfg.peer_public_key),
        "endpoint": ("s", f"{cfg.endpoint_host}:{cfg.endpoint_port}" if ":" not in cfg.endpoint_host
                     else f"[{cfg.endpoint_host}]:{cfg.endpoint_port}"),
        "allowed-ips": ("as", list(cfg.allowed_ips)),
        "persistent-keepalive": ("u", int(cfg.keepalive or 25)),
    }
    if cfg.preshared_key:
        peer["preshared-key"] = ("s", cfg.preshared_key)
        peer["preshared-key-flags"] = ("u", 0)
    wg: dict[str, tuple] = {
        "private-key": ("s", cfg.private_key),
        "private-key-flags": ("u", 0),
        "peer-routes": ("b", True),
        "ip4-auto-default-route": ("i", 0),
        "ip6-auto-default-route": ("i", 0),
        "peers": ("aa{sv}", [peer]),
    }
    if cfg.mtu:
        wg["mtu"] = ("u", int(cfg.mtu))
    addrs = []
    for a in cfg.addresses:
        n = netutil.net(a)
        ip = a.split("/")[0]
        addrs.append({"address": ("s", ip), "prefix": ("u", n.prefixlen)})
    ipv4: dict[str, tuple] = {
        "method": ("s", "manual"),
        "address-data": ("aa{sv}", addrs),
        "never-default": ("b", True),
        "ignore-auto-dns": ("b", True),
        "dns": ("au", []),
        "dns-search": ("as", []),
    }
    if profile.dns_domain and profile.dns_servers:
        ipv4["dns"] = ("au", [_ip4_u32(s) for s in profile.dns_servers])
        ipv4["dns-search"] = ("as", [f"~{profile.dns_domain}"])
        ipv4["dns-priority"] = ("i", 100)
    return {
        "connection": {"id": ("s", f"catflap {profile.name}"), "uuid": ("s", conn_uuid),
                       "type": ("s", "wireguard"), "interface-name": ("s", profile.ifname),
                       "autoconnect": ("b", False)},
        "wireguard": wg,
        "ipv4": ipv4,
        "ipv6": {"method": ("s", "disabled")},
    }


class NetworkManagerDBusBackend(Backend):
    name = "networkmanager-dbus"

    def __init__(self, runner=run, bus: Bus | None = None):
        super().__init__(runner)
        self._bus = bus

    @property
    def bus(self) -> Bus:
        if self._bus is None:
            self._bus = JeepneyBus()
        return self._bus

    # ---------------------------------------------------------------- helpers
    def _conn_path(self, conn_uuid: str) -> str | None:
        try:
            (path,) = self.bus.call(SETTINGS_PATH, IF_SETTINGS, "GetConnectionByUuid", "s", (conn_uuid,))
            return path
        except BackendError:
            return None

    def _active_path(self, conn_uuid: str) -> str | None:
        for ap in self.bus.get(NM_PATH, NM, "ActiveConnections"):
            try:
                if self.bus.get(ap, IF_ACTIVE, "Uuid") == conn_uuid:
                    return ap
            except BackendError:
                continue  # went away while we looked
        return None

    def _delete_by_ifname(self, ifname: str) -> None:
        (paths,) = self.bus.call(SETTINGS_PATH, IF_SETTINGS, "ListConnections")
        for p in paths:
            try:
                (s,) = self.bus.call(p, IF_CONN, "GetSettings")
            except BackendError:
                continue
            c = _plain(s).get("connection", {})
            if c.get("type") == "wireguard" and c.get("interface-name") == ifname:
                self.bus.call(p, IF_CONN, "Delete")

    # ---------------------------------------------------------------- lifecycle
    def install(self, profile: Profile, cfg: PeerConfig) -> str:
        if profile.backend_id and (old := self._conn_path(profile.backend_id)):
            self.bus.call(old, IF_CONN, "Delete")
        else:
            self._delete_by_ifname(profile.ifname)
        conn_uuid = str(_uuid.uuid4())
        settings = build_settings(profile, cfg, conn_uuid)
        (path,) = self.bus.call(SETTINGS_PATH, IF_SETTINGS, "AddConnection", "a{sa{sv}}", (settings,))
        problems = self.verify_path(path, profile)
        if problems:
            self.bus.call(path, IF_CONN, "Delete")
            raise BackendError("NetworkManager did not keep the safe settings: " + "; ".join(problems))
        return conn_uuid

    def verify_path(self, path: str, profile: Profile) -> list[str]:
        (s,) = self.bus.call(path, IF_CONN, "GetSettings")
        p = _plain(s)
        flat = {
            "connection.autoconnect": "yes" if p.get("connection", {}).get("autoconnect", True) else "no",
            "ipv4.never-default": "yes" if p.get("ipv4", {}).get("never-default") else "no",
            "ipv6.method": p.get("ipv6", {}).get("method", "auto"),
            "ipv4.dns-search": ",".join(p.get("ipv4", {}).get("dns-search", [])),
            "ipv6.dns-search": ",".join(p.get("ipv6", {}).get("dns-search", [])),
            "connection.interface-name": p.get("connection", {}).get("interface-name", ""),
        }
        return check_settings(flat, profile)

    def remove(self, profile: Profile) -> None:
        if profile.backend_id and (path := self._conn_path(profile.backend_id)):
            self.bus.call(path, IF_CONN, "Delete")

    def up(self, profile: Profile) -> None:
        path = self._conn_path(profile.backend_id or "")
        if not path:
            raise BackendError(f"'{profile.name}' has no NetworkManager connection; "
                               f"run 'catflap apply {profile.name}'")
        self.bus.call(NM_PATH, NM, "ActivateConnection", "ooo", (path, "/", "/"))

    def down(self, profile: Profile) -> None:
        if profile.backend_id and (ap := self._active_path(profile.backend_id)):
            self.bus.call(NM_PATH, NM, "DeactivateConnection", "o", (ap,))

    # ---------------------------------------------------------------- status
    def status(self, profile: Profile) -> TunnelStatus:
        if not profile.backend_id:
            return TunnelStatus(active=False, detail="not installed")
        ap = self._active_path(profile.backend_id)
        if not ap or self.bus.get(ap, IF_ACTIVE, "State") != ACTIVATED:
            return TunnelStatus(active=False)
        st = TunnelStatus(active=True)
        base = Path(f"/sys/class/net/{profile.ifname}/statistics")
        try:
            st.rx_bytes = int((base / "rx_bytes").read_text())
            st.tx_bytes = int((base / "tx_bytes").read_text())
        except (OSError, ValueError):
            pass
        return st

    def local_network(self) -> LocalNetwork:
        ln = LocalNetwork()
        (devices,) = self.bus.call(NM_PATH, NM, "GetDevices")
        for d in devices:
            try:
                ifname = self.bus.get(d, IF_DEVICE, "Interface")
                ip4 = self.bus.get(d, IF_DEVICE, "Ip4Config")
                if not ip4 or ip4 == "/":
                    continue
                for a in self.bus.get(ip4, IF_IP4, "AddressData"):
                    a = {k: v[1] for k, v in a.items()}
                    ln.addresses.append(LocalAddress(cidr=f"{a['address']}/{a['prefix']}", dev=ifname))
                for r in self.bus.get(ip4, IF_IP4, "RouteData"):
                    r = {k: v[1] for k, v in r.items()}
                    if r.get("table", 254) not in (0, 254):   # main table only
                        continue
                    dest = str(netutil.net(f"{r['dest']}/{r['prefix']}"))
                    ln.routes.append(LocalRoute(dest=dest, dev=ifname, gateway=r.get("next-hop")))
            except BackendError:
                continue
        return ln
