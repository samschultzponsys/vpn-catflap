"""Windows backend: the official WireGuard for Windows tunnel service.

How a site works on Windows:

* **Setup (once per site, one UAC prompt):** ``wireguard.exe
  /installtunnelservice <file>`` creates the service ``WireGuardTunnel$<name>``.
  In the same elevated step catflap sets the service to start on demand and
  grants *this Windows user* permission to start and stop it.
* **Connect / disconnect (no prompt):** ``sc start`` / ``sc stop`` on that
  service, as the normal user.
* **Changing routes etc.:** the tunnel service reads its .conf file each time it
  starts, so catflap just rewrites the file (owned by the user, readable by
  SYSTEM). No prompt.
* **Removing a site:** ``/uninstalltunnelservice`` (one UAC prompt).

Status: preview. Written against the WireGuard for Windows docs; needs testing on
real Windows machines.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .. import wgconf
from ..model import PeerConfig, Profile
from .base import Backend, BackendError, LocalAddress, LocalNetwork, LocalRoute, TunnelStatus

DEFAULT_WIREGUARD = r"C:\Program Files\WireGuard\wireguard.exe"
WIREGUARD_DOWNLOAD = "https://www.wireguard.com/install/"
WIREGUARD_INSTALLER = "https://download.wireguard.com/windows-client/wireguard-installer.exe"


class WireGuardMissing(BackendError):
    """WireGuard for Windows isn't installed; the app offers to install it."""


def _tunnel_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "catflap" / "tunnels"


def service_name(profile: Profile) -> str:
    return f"WireGuardTunnel${profile.ifname}"


# Elevated once per site: install the tunnel service, make it on-demand and let the
# current user start/stop it (SDDL rights: CC query config, LC query status,
# RP start, WP stop, RC read control).
_INSTALL_PS = r"""
param([string]$Wg, [string]$Conf, [string]$Name, [string]$Sid)
$ErrorActionPreference = 'Stop'
$svc = "WireGuardTunnel`$$Name"
& $Wg /installtunnelservice $Conf
for ($i = 0; $i -lt 40 -and -not (Get-Service -Name $svc -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 250 }
if (-not (Get-Service -Name $svc -ErrorAction SilentlyContinue)) { throw "the WireGuard tunnel service was not created" }
& sc.exe stop $svc | Out-Null
& sc.exe config $svc start= demand | Out-Null
$sd = ((& sc.exe sdshow $svc) -join '').Trim()
$ace = "(A;;CCLCRPWPRC;;;$Sid)"
if ($sd -notlike "*$ace*") {
  if ($sd -match 'S:') { $sd = $sd -replace 'S:', "${ace}S:" } else { $sd = $sd + $ace }
  & sc.exe sdset $svc $sd | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "could not allow this user to start the tunnel (sdset $LASTEXITCODE)" }
}
"""

_UNINSTALL_PS = r"""
param([string]$Wg, [string]$Name)
& $Wg /uninstalltunnelservice $Name
"""


class WindowsBackend(Backend):
    name = "wireguard-windows"

    @property
    def exe(self) -> str:
        return os.environ.get("CATFLAP_WIREGUARD_EXE", DEFAULT_WIREGUARD)

    # ---------------------------------------------------------------- helpers
    def _ps(self, script: str):
        return self.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])

    def wireguard_installed(self) -> bool:
        return Path(self.exe).exists()

    def _require_wireguard(self) -> None:
        if not self.wireguard_installed():
            raise WireGuardMissing("WireGuard for Windows isn't installed. CatFlap can install it "
                                   f"for you, or get it from {WIREGUARD_DOWNLOAD} (one time).")

    def install_wireguard(self) -> None:
        """Download the official WireGuard installer and run it (it asks for UAC itself)."""
        import urllib.request
        d = Path(tempfile.mkdtemp(prefix="catflap-wg-"))
        exe = d / "wireguard-installer.exe"
        req = urllib.request.Request(WIREGUARD_INSTALLER, headers={"User-Agent": "catflap"})
        with urllib.request.urlopen(req, timeout=120) as r, open(exe, "wb") as f:  # noqa: S310
            f.write(r.read())
        res = self._ps(f"$p = Start-Process -FilePath '{exe}' -Wait -PassThru; exit $p.ExitCode")
        if not self.wireguard_installed():
            raise BackendError("WireGuard didn't finish installing"
                               + (f" ({(res.stderr or res.stdout).strip()})" if res.returncode else "")
                               + f". You can install it from {WIREGUARD_DOWNLOAD}.")

    def _elevated_script(self, script: str, args: dict[str, str], what: str) -> None:
        """Run a PowerShell script as administrator (one UAC prompt)."""
        d = Path(tempfile.mkdtemp(prefix="catflap-"))
        ps1 = d / "step.ps1"
        ps1.write_text(script, encoding="utf-8")
        argv = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)]
        for k, v in args.items():
            argv += [f"-{k}", v]
        quoted = ",".join("'" + a.replace("'", "''") + "'" for a in argv)
        launcher = (f"$p = Start-Process -FilePath 'powershell.exe' -ArgumentList {quoted} "
                    "-Verb RunAs -Wait -PassThru -WindowStyle Hidden; exit $p.ExitCode")
        try:
            res = self._ps(launcher)
        finally:
            ps1.unlink(missing_ok=True)
            try:
                d.rmdir()
            except OSError:
                pass
        if res.returncode != 0:
            raise BackendError(f"{what} didn't finish. Was the Windows permission prompt (UAC) "
                               f"declined? {(res.stderr or res.stdout).strip()}")

    def _user_sid(self) -> str:
        res = self._ps("[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value")
        sid = (res.stdout or "").strip()
        if res.returncode != 0 or not sid.startswith("S-"):
            raise BackendError("couldn't read this Windows user's ID")
        return sid

    def _service_state(self, profile: Profile) -> str | None:
        res = self.run(["sc", "query", service_name(profile)])
        if res.returncode != 0:
            return None                      # not installed
        for line in res.stdout.splitlines():
            if "STATE" in line:
                return line.split()[-1]      # RUNNING / STOPPED / START_PENDING …
        return "UNKNOWN"

    def _conf_path(self, profile: Profile) -> Path:
        return _tunnel_dir() / f"{profile.ifname}.conf"

    def _write_conf(self, profile: Profile, cfg: PeerConfig) -> Path:
        path = self._conf_path(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(wgconf.render(cfg), encoding="utf-8")
        # Only this user (who updates it) and SYSTEM (the tunnel service) can read it.
        self.run(["icacls", str(path), "/inheritance:r", "/grant:r",
                  f"{os.environ.get('USERNAME', '')}:F", "*S-1-5-18:R"])
        return path

    # ---------------------------------------------------------------- lifecycle
    def install(self, profile: Profile, cfg: PeerConfig) -> str:
        self._require_wireguard()
        path = self._write_conf(profile, cfg)
        if self._service_state(profile) is None:
            self._elevated_script(_INSTALL_PS, {"Wg": self.exe, "Conf": str(path),
                                                "Name": profile.ifname, "Sid": self._user_sid()},
                                  f"Setting up {profile.display} in WireGuard")
        return profile.ifname

    def remove(self, profile: Profile) -> None:
        if self._service_state(profile) is not None:
            self._elevated_script(_UNINSTALL_PS, {"Wg": self.exe, "Name": profile.ifname},
                                  f"Removing {profile.display} from WireGuard")
        self._conf_path(profile).unlink(missing_ok=True)

    def up(self, profile: Profile) -> None:
        if self._service_state(profile) is None:
            raise BackendError(f"{profile.display} isn't set up in WireGuard yet; use "
                               "Re-apply settings (Advanced view) to set it up")
        res = self.run(["sc", "start", service_name(profile)])
        if res.returncode != 0 and "1056" not in (res.stdout or ""):     # 1056: already running
            raise BackendError(f"Windows couldn't start the tunnel: {(res.stdout or res.stderr).strip()}")

    def down(self, profile: Profile) -> None:
        if self._service_state(profile) in ("RUNNING", "START_PENDING"):
            self.run(["sc", "stop", service_name(profile)])

    # ---------------------------------------------------------------- status
    def status(self, profile: Profile) -> TunnelStatus:
        active = self._service_state(profile) == "RUNNING"
        st = TunnelStatus(active=active)
        if active:
            ps = self._ps(f"Get-NetAdapterStatistics -Name '{profile.ifname}' -ErrorAction "
                          "SilentlyContinue | Select-Object ReceivedBytes,SentBytes | ConvertTo-Json")
            if ps.returncode == 0 and ps.stdout.strip():
                try:
                    d = json.loads(ps.stdout)
                    st.rx_bytes, st.tx_bytes = d.get("ReceivedBytes"), d.get("SentBytes")
                except ValueError:
                    pass
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


# ---------------------------------------------------------------- other VPN apps
UNINSTALL_KEYS = (
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKCU", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
)


def find_installed(prefix: str, winreg_mod=None) -> dict | None:
    """Find an installed program whose display name starts with ``prefix`` (e.g.
    "WiFiman"). Returns {name, version, uninstall, quiet_uninstall, per_user} or None."""
    if winreg_mod is None:
        import winreg as winreg_mod  # noqa: N813 - Windows only
    hives = {"HKLM": winreg_mod.HKEY_LOCAL_MACHINE, "HKCU": winreg_mod.HKEY_CURRENT_USER}
    for hive_name, path in UNINSTALL_KEYS:
        try:
            root = winreg_mod.OpenKey(hives[hive_name], path)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    sub = winreg_mod.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg_mod.OpenKey(root, sub) as k:
                        def val(n):
                            try:
                                return winreg_mod.QueryValueEx(k, n)[0]
                            except OSError:
                                return ""
                        name = val("DisplayName")
                        if name and name.lower().startswith(prefix.lower()):
                            return {"name": name, "version": val("DisplayVersion"),
                                    "uninstall": val("UninstallString"),
                                    "quiet_uninstall": val("QuietUninstallString"),
                                    "per_user": hive_name == "HKCU"}
                except OSError:
                    continue
    return None


def find_wifiman(winreg_mod=None) -> dict | None:
    """Ubiquiti's WiFiman Desktop (Teleport VPN), which can clash with CatFlap."""
    return find_installed("WiFiman", winreg_mod)


def uninstall_program(info: dict, run) -> None:
    """Run a program's own uninstaller and wait for it (UAC if installed for all users)."""
    cmdline = info.get("uninstall") or info.get("quiet_uninstall")
    if not cmdline:
        raise BackendError(f"{info.get('name', 'The program')} has no uninstaller registered; "
                           "remove it from Settings > Apps.")
    verb = "" if info.get("per_user") else "-Verb RunAs "
    escaped = cmdline.replace("'", "''")
    script = (f"$p = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', '\"{escaped}\"' "
              f"{verb}-Wait -PassThru; exit $p.ExitCode")
    res = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    if res.returncode != 0:
        raise BackendError(f"{info.get('name')} wasn't uninstalled (was the prompt declined?). "
                           "You can remove it from Settings > Apps.")
