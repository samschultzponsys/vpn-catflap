"""Windows backend: the official WireGuard for Windows tunnel service.

``wireguard.exe /installtunnelservice <file>`` installs and starts a service
named ``WireGuardTunnel$<name>``; ``/uninstalltunnelservice <name>`` stops and
removes it. Both need administrator rights, so catflap asks for elevation
(UAC) only for those two calls and otherwise runs as the normal user.

Status: under development. The commands follow the WireGuard for Windows
documentation, but this backend has not yet been tested on real Windows machines.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .. import wgconf
from ..model import PeerConfig, Profile
from .base import Backend, BackendError, LocalAddress, LocalNetwork, LocalRoute, TunnelStatus

DEFAULT_WIREGUARD = r"C:\Program Files\WireGuard\wireguard.exe"


def _tunnel_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "catflap" / "tunnels"


class WindowsBackend(Backend):
    name = "wireguard-windows"

    @property
    def exe(self) -> str:
        return os.environ.get("CATFLAP_WIREGUARD_EXE", DEFAULT_WIREGUARD)

    def _ps(self, script: str):
        return self.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])

    def _elevated(self, args: list[str]) -> None:
        quoted = ",".join("'" + a.replace("'", "''") + "'" for a in args)
        script = (f"$p = Start-Process -FilePath '{self.exe}' -ArgumentList {quoted} "
                  "-Verb RunAs -Wait -PassThru -WindowStyle Hidden; exit $p.ExitCode")
        res = self._ps(script)
        if res.returncode != 0:
            raise BackendError(f"wireguard.exe {args[0]} failed (was the UAC prompt declined?): "
                               f"{(res.stderr or res.stdout).strip()}")

    def _conf_path(self, profile: Profile) -> Path:
        return _tunnel_dir() / f"{profile.ifname}.conf"

    def install(self, profile: Profile, cfg: PeerConfig) -> str:
        if not Path(self.exe).exists():
            raise BackendError("WireGuard for Windows is not installed: get it from "
                               "https://www.wireguard.com/install/")
        path = self._conf_path(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(wgconf.render(cfg), encoding="utf-8")
        # Restrict the file to the current user + SYSTEM (the tunnel service).
        self.run(["icacls", str(path), "/inheritance:r", "/grant:r",
                  f"{os.environ.get('USERNAME', '')}:F", "SYSTEM:R"])
        return profile.ifname

    def remove(self, profile: Profile) -> None:
        self.down(profile)
        self._conf_path(profile).unlink(missing_ok=True)

    def up(self, profile: Profile) -> None:
        path = self._conf_path(profile)
        if not path.exists():
            raise BackendError(f"tunnel file missing; run 'catflap apply {profile.name}'")
        self._elevated(["/installtunnelservice", str(path)])

    def down(self, profile: Profile) -> None:
        if self.status(profile).active:
            self._elevated(["/uninstalltunnelservice", profile.ifname])

    def status(self, profile: Profile) -> TunnelStatus:
        res = self.run(["sc", "query", f"WireGuardTunnel${profile.ifname}"])
        active = res.returncode == 0 and "RUNNING" in res.stdout
        st = TunnelStatus(active=active)
        if active:
            ps = self._ps(f"Get-NetAdapterStatistics -Name '{profile.ifname}' | "
                          "Select-Object ReceivedBytes,SentBytes | ConvertTo-Json")
            if ps.returncode == 0 and ps.stdout.strip():
                d = json.loads(ps.stdout)
                st.rx_bytes, st.tx_bytes = d.get("ReceivedBytes"), d.get("SentBytes")
        return st

    def local_network(self) -> LocalNetwork:
        ln = LocalNetwork()
        r = self._ps("Get-NetRoute -AddressFamily IPv4 | Select-Object DestinationPrefix,"
                     "InterfaceAlias,NextHop | ConvertTo-Json")
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            for e in data if isinstance(data, list) else [data]:
                gw = e.get("NextHop")
                ln.routes.append(LocalRoute(dest=e["DestinationPrefix"], dev=e.get("InterfaceAlias", ""),
                                            gateway=None if gw in (None, "0.0.0.0") else gw))
        a = self._ps("Get-NetIPAddress -AddressFamily IPv4 | Select-Object IPAddress,"
                     "PrefixLength,InterfaceAlias | ConvertTo-Json")
        if a.returncode == 0 and a.stdout.strip():
            data = json.loads(a.stdout)
            for e in data if isinstance(data, list) else [data]:
                ln.addresses.append(LocalAddress(cidr=f"{e['IPAddress']}/{e['PrefixLength']}",
                                                 dev=e.get("InterfaceAlias", "")))
        return ln
