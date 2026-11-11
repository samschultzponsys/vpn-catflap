"""Linux backend: NetworkManager via nmcli.

Runs as the normal user; NetworkManager's polkit rules authorize the
connection changes (problem #13). The private key only touches disk inside a
0700 temp directory for the duration of the import.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .. import wgconf
from ..model import PeerConfig, Profile
from .base import (Backend, BackendError, LocalAddress, LocalNetwork, LocalRoute,
                   TunnelStatus)

_UUID_RE = re.compile(r"\(([0-9a-f]{8}-[0-9a-f-]{27})\)")

# Settings applied to every catflap connection. Order matters for readability only.
SAFE_SETTINGS: list[tuple[str, str]] = [
    ("connection.autoconnect", "no"),            # #7 manual / on-demand
    ("ipv4.never-default", "yes"),               # #3 never the default route
    ("wireguard.ip4-auto-default-route", "0"),   # #3
    ("wireguard.ip6-auto-default-route", "0"),   # #3
    ("wireguard.peer-routes", "yes"),            # split-tunnel routes from AllowedIPs
    ("ipv4.ignore-auto-dns", "yes"),             # #4
    ("ipv4.dns", ""),                            # #4
    ("ipv4.dns-search", ""),                     # #4 no "~"
    ("ipv6.method", "disabled"),                 # #5 (also kills the IPv6 "~")
]


def check_settings(s: dict[str, str], profile: Profile) -> list[str]:
    """Check NetworkManager settings (nmcli-style keys) against the safe defaults."""
    problems = []
    if s.get("connection.autoconnect") not in ("no", "false"):
        problems.append("autoconnect is on")
    if s.get("ipv4.never-default") not in ("yes", "true"):
        problems.append("IPv4 may become the default route")
    if s.get("ipv6.method") != "disabled":
        problems.append(f"IPv6 method is {s.get('ipv6.method')!r}, not disabled")
    for k in ("ipv4.dns-search", "ipv6.dns-search"):
        domains = [d for d in s.get(k, "").split(",") if d]
        if "~" in domains or "~." in domains:
            problems.append(f"{k} contains a catch-all '~' domain")
    ifname = s.get("connection.interface-name")
    if ifname and ifname != profile.ifname:
        problems.append(f"interface name is {ifname!r}, expected {profile.ifname!r}")
    return problems


class NetworkManagerBackend(Backend):
    name = "networkmanager"

    def _nmcli(self, *args: str, check: bool = True) -> str:
        res = self.run(["nmcli", *args])
        if res.returncode == 127:
            raise BackendError("nmcli not found: install NetworkManager "
                               "(Fedora: dnf install NetworkManager; Debian: apt install network-manager)")
        if check and res.returncode != 0:
            msg = (res.stderr or res.stdout).strip()
            if "not authorized" in msg.lower() or "insufficient privileges" in msg.lower():
                msg += ("\nPolkit denied the change. catflap must run as your normal desktop user "
                        "in an active session; do not run it with sudo.")
            raise BackendError(f"nmcli {' '.join(args[:3])}: {msg}")
        return res.stdout

    # ---------------------------------------------------------------- lifecycle
    def settings_for(self, profile: Profile) -> list[tuple[str, str]]:
        s = list(SAFE_SETTINGS)
        s.insert(0, ("connection.id", f"catflap {profile.name}"))
        if profile.dns_domain and profile.dns_servers:
            # Split DNS: only <domain> is resolved through the tunnel. A routing
            # domain "~office.lan" is fine; a bare "~" is what hijacks all DNS.
            s = [x for x in s if x[0] not in ("ipv4.dns", "ipv4.dns-search")]
            s += [("ipv4.dns", ",".join(profile.dns_servers)),
                  ("ipv4.dns-search", f"~{profile.dns_domain}"),
                  ("ipv4.dns-priority", "100")]
        if profile.mtu:
            s.append(("wireguard.mtu", str(profile.mtu)))
        return s

    def install(self, profile: Profile, cfg: PeerConfig) -> str:
        if profile.backend_id:
            self._nmcli("connection", "delete", "uuid", profile.backend_id, check=False)
        else:  # stale connection with the same interface name (e.g. a manual import)
            self._delete_by_ifname(profile.ifname)
        with tempfile.TemporaryDirectory(prefix="catflap-") as d:
            os.chmod(d, 0o700)
            # NetworkManager names the interface after the file (problem #2).
            path = Path(d) / f"{profile.ifname}.conf"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(wgconf.render(cfg))
            out = self._nmcli("connection", "import", "type", "wireguard", "file", str(path))
        m = _UUID_RE.search(out)
        if not m:
            raise BackendError(f"could not parse the new connection UUID from: {out.strip()!r}")
        uuid = m.group(1)
        args: list[str] = []
        for k, v in self.settings_for(profile):
            args += [k, v]
        try:
            self._nmcli("connection", "modify", "uuid", uuid, *args)
        except BackendError:
            self._nmcli("connection", "delete", "uuid", uuid, check=False)
            raise
        problems = self.verify(uuid, profile)
        if problems:
            self._nmcli("connection", "delete", "uuid", uuid, check=False)
            raise BackendError("NetworkManager did not keep the safe settings: " + "; ".join(problems))
        return uuid

    def _delete_by_ifname(self, ifname: str) -> None:
        out = self._nmcli("-t", "-f", "UUID,TYPE,DEVICE,NAME", "connection", "show", check=False)
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) >= 4 and parts[1] == "wireguard" and (parts[3] == ifname or parts[2] == ifname):
                self._nmcli("connection", "delete", "uuid", parts[0], check=False)

    def read_settings(self, uuid: str) -> dict[str, str]:
        fields = "connection.autoconnect,ipv4.never-default,ipv4.dns,ipv4.dns-search,ipv6.method,ipv6.dns-search,connection.interface-name"
        out = self._nmcli("-t", "-f", fields, "connection", "show", "uuid", uuid)
        res = {}
        for line in out.splitlines():
            k, _, v = line.partition(":")
            res[k] = v
        return res

    def verify(self, uuid: str, profile: Profile) -> list[str]:
        """Read the connection back and check the safety settings stuck (#4, #5, #7)."""
        return check_settings(self.read_settings(uuid), profile)

    def remove(self, profile: Profile) -> None:
        if profile.backend_id:
            self._nmcli("connection", "delete", "uuid", profile.backend_id, check=False)

    def _require(self, profile: Profile) -> str:
        if not profile.backend_id:
            raise BackendError(f"'{profile.name}' has no NetworkManager connection; run 'catflap apply {profile.name}'")
        return profile.backend_id

    def up(self, profile: Profile) -> None:
        self._nmcli("connection", "up", "uuid", self._require(profile))

    def down(self, profile: Profile) -> None:
        self._nmcli("connection", "down", "uuid", self._require(profile), check=False)

    # ---------------------------------------------------------------- status
    def _sys_stat(self, ifname: str, what: str) -> int | None:
        try:
            return int(Path(f"/sys/class/net/{ifname}/statistics/{what}").read_text())
        except (OSError, ValueError):
            return None

    def status(self, profile: Profile) -> TunnelStatus:
        if not profile.backend_id:
            return TunnelStatus(active=False, detail="not installed")
        out = self._nmcli("-t", "-f", "GENERAL.STATE", "connection", "show", "uuid",
                          profile.backend_id, check=False)
        active = "activated" in out
        st = TunnelStatus(active=active)
        if active:
            st.rx_bytes = self._sys_stat(profile.ifname, "rx_bytes")
            st.tx_bytes = self._sys_stat(profile.ifname, "tx_bytes")
            # `wg show` needs CAP_NET_ADMIN; it only works when run privileged.
            res = self.run(["wg", "show", profile.ifname, "latest-handshakes"])
            if res.returncode == 0 and res.stdout.strip():
                try:
                    ts = int(res.stdout.split()[-1])
                    st.last_handshake = ts or None
                except ValueError:
                    pass
        return st

    def local_network(self) -> LocalNetwork:
        ln = LocalNetwork()
        r = self.run(["ip", "-j", "-4", "route", "show", "table", "main"])
        if r.returncode == 0 and r.stdout.strip():
            for e in json.loads(r.stdout):
                dst = e.get("dst", "")
                if dst == "default":
                    dst = "0.0.0.0/0"
                elif "/" not in dst:
                    dst += "/32"
                ln.routes.append(LocalRoute(dest=dst, dev=e.get("dev", ""), gateway=e.get("gateway")))
        a = self.run(["ip", "-j", "-4", "addr", "show"])
        if a.returncode == 0 and a.stdout.strip():
            for iface in json.loads(a.stdout):
                for ai in iface.get("addr_info", []):
                    if ai.get("family") == "inet":
                        ln.addresses.append(LocalAddress(
                            cidr=f"{ai['local']}/{ai['prefixlen']}", dev=iface.get("ifname", "")))
        return ln
