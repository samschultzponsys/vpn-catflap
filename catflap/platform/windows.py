r"""Windows backend: WireGuard's own tunnel service, without its app.

CatFlap uses ``wireguard.exe`` only as a command-line engine (WireGuard's
documented "tunnel service" mode), so there's no second window or tray icon.
The engine comes from the official, signed WireGuard MSI on
download.wireguard.com: the CatFlap installer (or the app, when it's missing)
downloads the MSI, and the first site setup extracts ``wireguard.exe`` and
``wg.exe`` into ``Program Files\CatFlap\WireGuard`` (a folder only
administrators can change, because the tunnel service runs as SYSTEM). The MSI
is never installed, so WireGuard's manager service and tray UI never start. If
WireGuard for Windows is already installed and CatFlap's engine isn't, CatFlap
uses that instead.

How a site works on Windows:

* **Setup (once per site, one UAC prompt):** ``wireguard.exe
  /installtunnelservice <file>`` creates the service ``WireGuardTunnel$<name>``.
  In the same elevated step catflap sets the service to start on demand and
  grants *this Windows user* permission to start and stop it. The first setup
  also puts the engine in place (after checking WireGuard's signature).
* **Connect / disconnect (no prompt):** ``sc start`` / ``sc stop`` on that
  service, as the normal user.
* **Changing routes etc.:** the tunnel service reads its .conf file each time it
  starts, so catflap just rewrites the file (owned by the user, readable by
  SYSTEM). No prompt.
* **Removing a site:** stop and delete the service (one UAC prompt); with the
  last site gone, the engine folder is removed too.
* **Repairs:** if a site's service or the program it runs is gone (for example
  after uninstalling WireGuard for Windows), connecting sets it up again.

Status: preview. Written against the WireGuard for Windows docs; needs testing on
real Windows machines.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

from .. import wgconf
from ..model import PeerConfig, Profile
from .base import Backend, BackendError, LocalAddress, LocalNetwork, LocalRoute, TunnelStatus

DEFAULT_WIREGUARD = r"C:\Program Files\WireGuard\wireguard.exe"     # WireGuard for Windows (the app)
WIREGUARD_DOWNLOAD = "https://www.wireguard.com/install/"
WIREGUARD_LISTING = "https://download.wireguard.com/windows-client/latest.sig"
WIREGUARD_MSI_BASE = "https://download.wireguard.com/windows-client/"
WIREGUARD_SIGNER = "WireGuard LLC"


LONG_TIMEOUT = 900      # seconds: waits on a permission prompt or an uninstaller


def run_long(run, cmd):
    """Run with a long timeout (someone may take a while to answer UAC)."""
    try:
        return run(cmd, timeout=LONG_TIMEOUT)
    except TypeError:                     # simple runners (tests) take only the command
        return run(cmd)


def _with_log(script: str) -> str:
    """Add a -Log parameter and record the script's output and any error in it."""
    head, rest = script.lstrip().split("\n", 1) if script.lstrip().startswith("param(") else ("param()", script)
    params = head[len("param("):-1].strip()
    head = "param([string]$Log = ''" + (", " + params if params else "") + ")"
    return (head + "\n"
            # Windows PowerShell's own modules first (PowerShell 7's path can leak in and break them)
            "$env:PSModulePath = \"$PSHOME\\Modules;\" + $env:PSModulePath\n"
            "Import-Module \"$PSHOME\\Modules\\Microsoft.PowerShell.Security\" -ErrorAction SilentlyContinue\n"
            "Import-Module \"$PSHOME\\Modules\\Microsoft.PowerShell.Management\" -ErrorAction SilentlyContinue\n"
            "trap { if ($Log) { ('ERROR: ' + ($_ | Out-String)) | Out-File -LiteralPath $Log -Append -Encoding utf8 }; exit 1 }\n"
            "& {\n" + rest + "\n} | ForEach-Object { if ($Log) { \"$_\" | Out-File -LiteralPath $Log -Append -Encoding utf8 } }\n"
            "exit 0\n")


class WireGuardMissing(BackendError):
    """No WireGuard engine yet; the app offers to download it."""


def _local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local"))


def _tunnel_dir() -> Path:
    return _local_app_data() / "catflap" / "tunnels"


def engine_dir() -> Path:
    """Where CatFlap keeps its WireGuard engine (admin-only, like any program)."""
    pf = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files"
    return Path(pf) / "CatFlap" / "WireGuard"


def msi_dirs() -> list[Path]:
    """Where a downloaded WireGuard MSI may be: next to CatFlap.exe (the installer puts
    it there) and in CatFlap's data folder (the app downloads it there)."""
    dirs = [_local_app_data() / "catflap" / "wireguard"]
    if getattr(sys, "frozen", False):
        dirs.insert(0, Path(sys.executable).parent / "wireguard")
    if os.environ.get("CATFLAP_WIREGUARD_MSI_DIR"):
        dirs.insert(0, Path(os.environ["CATFLAP_WIREGUARD_MSI_DIR"]))
    return dirs


def windows_arch() -> str:
    arch = (os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or "").upper()
    return "arm64" if arch == "ARM64" else "amd64"


def latest_msi_name(listing: str, arch: str) -> str | None:
    """Pick ``wireguard-<arch>-<version>.msi`` out of download.wireguard.com's latest.sig."""
    m = re.search(rf"(wireguard-{arch}-[0-9.]+\.msi)\s*$", listing, re.M)
    return m.group(1) if m else None


def service_name(profile: Profile) -> str:
    return f"WireGuardTunnel${profile.ifname}"


# Elevated once per site: install the tunnel service, make it on-demand and let the
# current user start/stop it (SDDL rights: CC query config, LC query status,
# RP start, WP stop, RC read control).
_INSTALL_PS = r"""
param([string]$Wg, [string]$Conf, [string]$Name, [string]$Sid, [string]$Msi = '', [string]$Dest = '')
$ErrorActionPreference = 'Stop'
$svc = "WireGuardTunnel`$$Name"
function Test-WireGuardSigned([string]$Path) {
  $sig = Get-AuthenticodeSignature -FilePath $Path
  if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notlike '*O=WireGuard LLC*') {
    throw "$Path is not signed by WireGuard LLC ($($sig.Status)); not using it"
  }
}
if ($Msi -and -not (Test-Path $Wg)) {
  # Extract (not install) the official MSI into an admin-only folder: no WireGuard app,
  # manager service or tray icon, just wireguard.exe and wg.exe.
  $work = Join-Path $env:SystemRoot ("Temp\catflap-wg-" + [guid]::NewGuid())
  New-Item -ItemType Directory -Path $work | Out-Null
  try {
    $copy = Join-Path $work 'wireguard.msi'
    Copy-Item -LiteralPath $Msi -Destination $copy
    Test-WireGuardSigned $copy
    $p = Start-Process -FilePath msiexec.exe -ArgumentList '/a', "`"$copy`"", '/qn', "TARGETDIR=`"$work\x`"" -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "extracting WireGuard failed (msiexec $($p.ExitCode))" }
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    foreach ($f in 'wireguard.exe', 'wg.exe') {
      $src = Get-ChildItem -Path "$work\x" -Recurse -Filter $f | Select-Object -First 1
      if (-not $src) { throw "$f wasn't in the WireGuard download" }
      Test-WireGuardSigned $src.FullName
      Copy-Item -LiteralPath $src.FullName -Destination (Join-Path $Dest $f) -Force
    }
  } finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}
if (-not (Test-Path $Wg)) { throw "WireGuard engine not found at $Wg" }
# via cmd so WireGuard's messages (on stderr) are captured without PowerShell treating them as errors
$out = cmd.exe /c "`"$Wg`" /installtunnelservice `"$Conf`" 2>&1"
for ($i = 0; $i -lt 40 -and -not (Get-Service -Name $svc -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 250 }
if (-not (Get-Service -Name $svc -ErrorAction SilentlyContinue)) { throw "the WireGuard tunnel service was not created: $out" }
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
param([string]$Names, [string]$Dest = '')
foreach ($n in ($Names -split ',')) {
  if (-not $n) { continue }
  $svc = "WireGuardTunnel`$$n"
  & sc.exe stop $svc | Out-Null
  & sc.exe delete $svc | Out-Null
  for ($i = 0; $i -lt 40 -and (Get-Service -Name $svc -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 250 }
}
# Last CatFlap tunnel gone: remove the engine too.
if ($Dest -and (Test-Path $Dest)) {
  $using = Get-CimInstance Win32_Service | Where-Object { $_.PathName -like "*$Dest*" }
  if (-not $using) {
    Remove-Item -LiteralPath $Dest -Recurse -Force -ErrorAction SilentlyContinue
    $parent = Split-Path $Dest
    if ((Test-Path $parent) -and -not (Get-ChildItem $parent)) { Remove-Item -LiteralPath $parent -Force }
  }
}
exit 0
"""


class WindowsBackend(Backend):
    name = "wireguard-windows"

    @property
    def engine(self) -> Path:
        return engine_dir() / "wireguard.exe"

    @property
    def exe(self) -> str:
        """The wireguard.exe tunnel services run: CatFlap's engine, else WireGuard for
        Windows if that's installed, else CatFlap's engine (set up from the MSI)."""
        if os.environ.get("CATFLAP_WIREGUARD_EXE"):
            return os.environ["CATFLAP_WIREGUARD_EXE"]
        if self.engine.exists() or not Path(DEFAULT_WIREGUARD).exists():
            return str(self.engine)
        return DEFAULT_WIREGUARD

    # ---------------------------------------------------------------- helpers
    def _ps(self, script: str):
        return self.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])

    def downloaded_msi(self) -> Path | None:
        for d in msi_dirs():
            found = sorted(d.glob("wireguard-*.msi")) if d.is_dir() else []
            if found:
                return found[-1]
        return None

    def wireguard_installed(self) -> bool:
        """True when a site can be set up: an engine is in place, or its download is."""
        return Path(self.exe).exists() or self.downloaded_msi() is not None

    def _require_wireguard(self) -> None:
        if not self.wireguard_installed():
            raise WireGuardMissing(
                "CatFlap needs the WireGuard engine to connect, and it isn't downloaded yet. "
                "CatFlap can download it from wireguard.com (about 3 MB, no extra app), or you "
                f"can install WireGuard for Windows yourself from {WIREGUARD_DOWNLOAD}.")

    def install_wireguard(self) -> None:
        """Download the official, signed WireGuard MSI from download.wireguard.com.
        Nothing is installed here: the next site setup extracts the engine from it
        (after checking its signature) in the same permission prompt."""
        import urllib.request

        def get(url: str) -> bytes:
            req = urllib.request.Request(url, headers={"User-Agent": "catflap"})
            with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 - fixed https URL
                return r.read()
        name = latest_msi_name(get(WIREGUARD_LISTING).decode("utf-8", "replace"), windows_arch())
        if not name:
            raise BackendError("couldn't find the WireGuard download on download.wireguard.com; "
                               f"install WireGuard for Windows from {WIREGUARD_DOWNLOAD} instead")
        data = get(WIREGUARD_MSI_BASE + name)
        d = msi_dirs()[-1]
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("wireguard-*.msi"):
            old.unlink(missing_ok=True)
        (d / name).write_bytes(data)

    def _elevated_script(self, script: str, args: dict[str, str], what: str) -> None:
        """Run a PowerShell script as administrator (one UAC prompt). Its window is
        hidden, so errors and output go to a log file that's shown if it fails."""
        d = Path(tempfile.mkdtemp(prefix="catflap-"))
        ps1, log = d / "step.ps1", d / "step.log"
        ps1.write_text(_with_log(script), encoding="utf-8")
        argv = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), "-Log", str(log)]
        for k, v in args.items():
            argv += [f"-{k}", v]
        # Start-Process joins the list with spaces and doesn't quote, so quote each one
        # ourselves (paths like "C:\\Program Files\\..." have spaces).
        quoted = ",".join("'\"" + a.replace("'", "''") + "\"'" for a in argv)
        launcher = (f"$p = Start-Process -FilePath 'powershell.exe' -ArgumentList {quoted} "
                    "-Verb RunAs -Wait -PassThru -WindowStyle Hidden; exit $p.ExitCode")
        try:
            res = run_long(self.run, ["powershell", "-NoProfile", "-NonInteractive", "-Command", launcher])
            detail = log.read_text(encoding="utf-8-sig", errors="replace").strip() if log.exists() else ""
        finally:
            for f in (ps1, log):
                f.unlink(missing_ok=True)
            try:
                d.rmdir()
            except OSError:
                pass
        if res.returncode != 0:
            if detail:
                raise BackendError(f"{what} didn't finish: {detail[-1500:]}")
            raise BackendError(f"{what} didn't finish. Was the Windows permission prompt (UAC) "
                               f"declined? {(res.stderr or res.stdout).strip()}")

    def _user_sid(self) -> str:
        res = self._ps("[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value")
        sid = (res.stdout or "").strip()
        if res.returncode != 0 or not sid.startswith("S-"):
            raise BackendError("couldn't read this Windows user's ID")
        return sid

    def _service_program(self, profile: Profile) -> str | None:
        """The program a site's service runs (None if unknown)."""
        res = self.run(["sc", "qc", service_name(profile)])
        for line in (res.stdout or "").splitlines() if res.returncode == 0 else []:
            if "BINARY_PATH_NAME" in line:
                cmd = line.split(":", 1)[1].strip()
                return cmd[1:].split('"', 1)[0] if cmd.startswith('"') else cmd.split(" /", 1)[0]
        return None

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
    def _needs_setup(self, profile: Profile) -> bool:
        """The site's service is missing, or the program it runs is gone."""
        if self._service_state(profile) is None:
            return True
        prog = self._service_program(profile)
        return bool(prog) and not Path(prog).exists()

    def _setup_service(self, profile: Profile, conf: Path) -> None:
        self._require_wireguard()
        exe = self.exe
        msi = self.downloaded_msi() if not Path(exe).exists() else None
        args = {"Wg": exe, "Conf": str(conf), "Name": profile.ifname, "Sid": self._user_sid()}
        if msi:
            args.update(Msi=str(msi), Dest=str(engine_dir()))
        self._elevated_script(_INSTALL_PS, args, f"Setting up {profile.display}")
        if msi and msi.parent == msi_dirs()[-1]:     # the app's own download isn't needed any more
            msi.unlink(missing_ok=True)

    def install(self, profile: Profile, cfg: PeerConfig) -> str:
        self._require_wireguard()
        path = self._write_conf(profile, cfg)
        if self._needs_setup(profile):
            self._setup_service(profile, path)
        return profile.ifname

    def remove(self, profile: Profile) -> None:
        if self._service_state(profile) is not None:
            self.remove_services([profile.ifname], f"Removing {profile.display}")
        self._conf_path(profile).unlink(missing_ok=True)

    def remove_services(self, ifnames: list[str], what: str = "Removing CatFlap's tunnels") -> None:
        """Delete tunnel services (one UAC prompt); drops the engine when none use it."""
        self._elevated_script(_UNINSTALL_PS, {"Names": ",".join(ifnames), "Dest": str(engine_dir())}, what)

    def hide_wireguard_app_tray(self) -> None:
        """Remove the WireGuard app's manager service (its tray icon and start at sign-in),
        keeping the app installed. One UAC prompt."""
        self._elevated_script('param([string]$Wg)\n& $Wg /uninstallmanagerservice\nexit 0\n',
                              {"Wg": DEFAULT_WIREGUARD}, "Hiding WireGuard's tray icon")
        self.run(["taskkill", "/IM", "wireguard.exe", "/FI", "SESSION ne 0", "/F"])

    def up(self, profile: Profile) -> None:
        if self._needs_setup(profile):
            conf = self._conf_path(profile)
            if not conf.exists():
                raise BackendError(f"{profile.display} isn't set up yet; use Re-apply settings "
                                   "(Advanced view) to set it up")
            self._setup_service(profile, conf)       # repair (one permission prompt)
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


def installed_programs(winreg_mod=None) -> list[dict]:
    """Every program in Windows' "Apps" list: {name, version, uninstall,
    quiet_uninstall, per_user}."""
    if winreg_mod is None:
        import winreg as winreg_mod  # noqa: N813 - Windows only
    hives = {"HKLM": winreg_mod.HKEY_LOCAL_MACHINE, "HKCU": winreg_mod.HKEY_CURRENT_USER}
    found = []
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
                        name = (val("DisplayName") or "").strip()
                        if name:
                            found.append({"name": name, "version": val("DisplayVersion"),
                                          "uninstall": val("UninstallString"),
                                          "quiet_uninstall": val("QuietUninstallString"),
                                          "per_user": hive_name == "HKCU"})
                except OSError:
                    continue
    return found


def find_installed(prefix: str, winreg_mod=None, exact: bool = False) -> dict | None:
    """The first installed program whose name starts with (or is) ``prefix``."""
    want = prefix.lower()
    for info in installed_programs(winreg_mod):
        n = info["name"].lower()
        if n == want if exact else n.startswith(want):
            return info
    return None


def find_wifiman(winreg_mod=None) -> dict | None:
    """Ubiquiti's WiFiman Desktop (Teleport VPN), which can clash with CatFlap."""
    return find_installed("WiFiman", winreg_mod)


def find_wireguard_app(winreg_mod=None) -> dict | None:
    """WireGuard for Windows (the app with its own window and tray icon)."""
    return find_installed("WireGuard", winreg_mod, exact=True)


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
    res = run_long(run, ["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    if res.returncode != 0:
        raise BackendError(f"{info.get('name')} wasn't uninstalled (was the prompt declined?). "
                           "You can remove it from Settings > Apps.")
