"""Update checking and self-update, aware of how catflap was installed.

Release tags are ``vMAJOR.MINOR`` (see :mod:`catflap.version`). The latest
GitHub release is compared to the running version; how to update depends on
the install method, which is detected and shown in the app:

=============  ==================================================  ==========
method         update                                              automatic
=============  ==================================================  ==========
flatpak        download the release .flatpak bundle, then          if allowed (*)
               ``flatpak install --user -y <bundle>``
rpm            ``pkexec dnf install <release .rpm URL>``           yes
deb            download .deb, ``pkexec apt install ./file.deb``    yes
pipx           ``pipx install --force <release wheel URL>``        yes
pip            ``python -m pip install --upgrade <wheel URL>``     yes
windows        download and run the release installer             yes
source         ``git fetch --tags && git checkout vX.Y``          no
=============  ==================================================  ==========

(*) The sandbox can't run host ``flatpak`` commands unless the user grants it
(``flatpak override --user --talk-name=org.freedesktop.Flatpak <app-id>``).
Without that, catflap downloads the bundle to ~/Downloads and shows the one
command to run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import version as _v

REPO = "samschultzponsys/vpn-catflap"
FLATPAK_ID = "io.github.samschultzponsys.CatFlap"
CHECK_INTERVAL = 24 * 3600


@dataclass
class InstallInfo:
    method: str
    detail: str = ""
    automatic: bool = False


@dataclass
class ReleaseInfo:
    version: str
    url: str
    assets: dict[str, str] = field(default_factory=dict)   # name -> download URL
    notes: str = ""


@dataclass
class UpdateStatus:
    current: str
    install: InstallInfo
    latest: ReleaseInfo | None = None
    available: bool = False
    instructions: list[str] = field(default_factory=list)
    error: str | None = None


def _module_file() -> Path:
    return Path(__file__).resolve()


def _owned_by(cmd: list[str]) -> bool:
    if not shutil.which(cmd[0]):
        return False
    try:
        return subprocess.run(cmd, capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def detect_install(env: dict[str, str] | None = None) -> InstallInfo:
    env = dict(os.environ if env is None else env)
    forced = env.get("CATFLAP_INSTALL_METHOD")
    if forced:
        return InstallInfo(forced, "set by CATFLAP_INSTALL_METHOD", forced not in ("source", "flatpak"))
    if env.get("FLATPAK_ID") or Path("/.flatpak-info").exists():
        return InstallInfo("flatpak", env.get("FLATPAK_ID", FLATPAK_ID), automatic=True)
    if sys.platform == "win32":
        if getattr(sys, "frozen", False):
            return InstallInfo("windows", sys.executable, automatic=True)
        return InstallInfo("pip", sys.prefix, automatic=True)
    mod = str(_module_file())
    if _owned_by(["rpm", "-qf", mod]):
        return InstallInfo("rpm", "installed from an RPM package", automatic=True)
    if _owned_by(["dpkg", "-S", mod]):
        return InstallInfo("deb", "installed from a .deb package", automatic=True)
    if "pipx" in sys.prefix and "venvs" in sys.prefix:
        return InstallInfo("pipx", sys.prefix, automatic=True)
    for parent in _module_file().parents:
        if (parent / ".git").exists():
            return InstallInfo("source", f"git checkout at {parent}")
    return InstallInfo("pip", sys.prefix, automatic=True)


def fetch_latest(repo: str = REPO, timeout: float = 10,
                 opener: Callable[..., Any] = urllib.request.urlopen) -> ReleaseInfo:
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": "catflap-update-check"})
    with opener(req, timeout=timeout) as r:
        data = json.loads(r.read())
    tag = data.get("tag_name", "")
    _v.parse(tag)  # validates vMAJOR.MINOR
    return ReleaseInfo(version=tag.lstrip("v"), url=data.get("html_url", ""),
                       assets={a["name"]: a["browser_download_url"] for a in data.get("assets", [])},
                       notes=data.get("body") or "")


def _asset(rel: ReleaseInfo, suffix: str) -> str | None:
    for name, url in rel.assets.items():
        if name.endswith(suffix):
            return url
    return None


def instructions(info: InstallInfo, rel: ReleaseInfo) -> list[str]:
    v = rel.version
    m = info.method
    if m == "flatpak":
        url = _asset(rel, ".flatpak")
        if not url:
            return [f"Download catflap-{v}.flatpak from {rel.url}",
                    f"flatpak install --user -y ~/Downloads/catflap-{v}.flatpak"]
        name = url.rsplit("/", 1)[-1]
        return [f"curl -L -o ~/Downloads/{name} {url}",
                f"flatpak install --user -y ~/Downloads/{name}"]
    if m == "rpm":
        url = _asset(rel, ".noarch.rpm") or rel.url
        return [f"sudo dnf install {url}"]
    if m == "deb":
        url = _asset(rel, "_all.deb") or rel.url
        return [f"curl -LO {url}", f"sudo apt install ./{url.rsplit('/', 1)[-1]}"]
    if m == "pipx":
        return [f"pipx install --force {_asset(rel, '.whl') or f'git+https://github.com/{REPO}@v{v}'}"]
    if m == "pip":
        return [f"{sys.executable} -m pip install --upgrade "
                f"{_asset(rel, '.whl') or f'git+https://github.com/{REPO}@v{v}'}"]
    if m == "windows":
        return [f"Download and run {_asset(rel, '.exe') or rel.url}"]
    return ["git fetch --tags", f"git checkout v{v}"]


def check(settings: dict[str, Any] | None = None, force: bool = True,
          fetch: Callable[[], ReleaseInfo] | None = None,
          install: InstallInfo | None = None) -> UpdateStatus:
    settings = settings if settings is not None else {}
    st = UpdateStatus(current=_v.__version__, install=install or detect_install())
    now = time.time()
    if not force and now - settings.get("last_update_check", 0) < CHECK_INTERVAL:
        cached = settings.get("last_update_result")
        if cached:
            st.latest = ReleaseInfo(**cached)
    if st.latest is None:
        try:
            st.latest = (fetch or (lambda: fetch_latest(settings.get("update_repo", REPO))))()
        except Exception as e:  # offline, rate-limited, no releases yet...
            st.error = f"update check failed: {e}"
            return st
        settings["last_update_check"] = now
        settings["last_update_result"] = st.latest.__dict__
    st.available = _v.is_newer(st.latest.version, st.current)
    if st.available:
        st.instructions = instructions(st.install, st.latest)
    return st


def host_commands_allowed(run: Callable[[list[str]], int]) -> bool:
    """True if this Flatpak may run host commands (org.freedesktop.Flatpak granted)."""
    if not shutil.which("flatpak-spawn"):
        return False
    try:
        return run(["flatpak-spawn", "--host", "true"]) == 0
    except OSError:
        return False


def _download(url: str, dest_dir: Path) -> Path:
    dest = dest_dir / url.rsplit("/", 1)[-1]
    req = urllib.request.Request(url, headers={"User-Agent": "catflap-updater"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:  # noqa: S310
        shutil.copyfileobj(r, f)
    return dest


@dataclass
class UpdateResult:
    done: bool                       # the new version is installed
    message: str
    restart: bool = False            # restart catflap to run the new version
    downloaded: Path | None = None   # a file the user still has to install (Flatpak)
    commands: list[str] = field(default_factory=list)   # what's left to run, if anything

    def __iter__(self):              # (done, message), for older callers
        return iter((self.done, self.message))


def downloads_dir() -> Path:
    d = Path(os.environ.get("XDG_DOWNLOAD_DIR", Path.home() / "Downloads"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def flatpak_commands(st: UpdateStatus, bundle: Path | None = None) -> list[str]:
    """The full, copy-pasteable commands to update a Flatpak install by hand."""
    rel = st.latest
    url = _asset(rel, ".flatpak") if rel else None
    name = f"catflap-{rel.version}.flatpak" if rel else "catflap.flatpak"
    path = str(bundle) if bundle else f"~/Downloads/{name}"
    cmds = [] if bundle else [f"curl -L -o {path} {url or rel.url}"]
    return cmds + [f"flatpak install --user -y {path}"]


def restart_command(info: InstallInfo | None = None) -> list[str]:
    """How to start a fresh catflap that runs the newly installed version."""
    info = info or detect_install()
    if info.method == "flatpak":
        # The Flatpak portal starts the *latest installed* version of this app
        # in a new sandbox; no extra permissions needed.
        return ["flatpak-spawn", "--latest-version", "catflap-tray"]
    if info.method == "windows":
        return [sys.executable, "tray"]
    exe = shutil.which("catflap-tray")
    return [exe] if exe else [sys.executable, "-m", "catflap", "tray"]


def installed_version(info: InstallInfo | None = None,
                      run: Callable[[list[str]], "subprocess.CompletedProcess"] | None = None) -> str | None:
    """The version that a fresh start would run (i.e. what's installed on disk now),
    or None if it can't be told. For a Flatpak this asks the portal to run the
    latest installed version, so it notices an update installed from a terminal."""
    info = info or detect_install()
    if info.method == "flatpak":
        cmd = ["flatpak-spawn", "--latest-version", "catflap", "--json", "version"]
    elif info.method == "windows":
        return None
    else:
        exe = shutil.which("catflap")
        cmd = ([exe] if exe else [sys.executable, "-m", "catflap"]) + ["--json", "version"]
    run = run or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=20))
    try:
        res = run(cmd)
        if res.returncode != 0:
            return None
        v = json.loads(res.stdout).get("version")
        _v.parse(v)
        return v
    except Exception:  # noqa: BLE001 - only a hint
        return None


def restart(info: InstallInfo | None = None) -> None:
    """Start the new catflap; the caller then quits this one."""
    subprocess.Popen(restart_command(info), start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def apply_update(st: UpdateStatus, run: Callable[[list[str]], int] | None = None) -> UpdateResult:
    """Update automatically where possible; otherwise say exactly what's left to do."""
    if not st.available or not st.latest:
        return UpdateResult(False, "Already up to date.")
    run = run or (lambda c: subprocess.call(c))
    m, rel = st.install.method, st.latest
    manual = instructions(st.install, rel)
    if m == "flatpak":
        url = _asset(rel, ".flatpak")
        if not url:
            return UpdateResult(False, "This release has no Flatpak file; download it from "
                                + rel.url, commands=manual)
        bundle = _download(url, downloads_dir())
        cmds = flatpak_commands(st, bundle)
        if not host_commands_allowed(run):
            return UpdateResult(False, f"Downloaded catflap {rel.version} to {bundle}. Install it "
                                "with the command below, then restart catflap.",
                                restart=True, downloaded=bundle, commands=cmds)
        rc = run(["flatpak-spawn", "--host", "flatpak", "install", "--user", "-y", str(bundle)])
        if rc != 0:
            return UpdateResult(False, f"Installing the update failed (exit {rc}). Run it yourself:",
                                downloaded=bundle, commands=cmds)
        return UpdateResult(True, f"catflap {rel.version} is installed.", restart=True)
    if m == "rpm":
        url = _asset(rel, ".noarch.rpm")
        if not url:
            return UpdateResult(False, "This release has no RPM; see " + rel.url, commands=manual)
        rc = run(["pkexec", "dnf", "install", "-y", url])
    elif m == "deb":
        url = _asset(rel, "_all.deb")
        if not url:
            return UpdateResult(False, "This release has no .deb; see " + rel.url, commands=manual)
        with tempfile.TemporaryDirectory() as d:
            rc = run(["pkexec", "apt-get", "install", "-y", str(_download(url, Path(d)))])
    elif m in ("pip", "pipx"):
        target = _asset(rel, ".whl") or f"git+https://github.com/{REPO}@v{rel.version}"
        cmd = (["pipx", "install", "--force", target] if m == "pipx"
               else [sys.executable, "-m", "pip", "install", "--upgrade", target])
        rc = run(cmd)
    elif m == "windows":
        url = _asset(rel, ".exe")
        if not url:
            return UpdateResult(False, "This release has no Windows installer; see " + rel.url)
        exe = _download(url, Path(tempfile.mkdtemp(prefix="catflap-update-")))
        subprocess.Popen([str(exe)])  # the installer replaces us and restarts catflap
        return UpdateResult(True, "The installer has been started; catflap will close.")
    else:
        return UpdateResult(False, "Installed from source. Update with:", commands=manual)
    if rc == 0:
        return UpdateResult(True, f"catflap {rel.version} is installed.", restart=True)
    return UpdateResult(False, f"The update command failed (exit {rc}). Run it yourself:",
                        commands=manual)
