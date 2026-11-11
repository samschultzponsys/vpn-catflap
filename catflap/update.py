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


def apply_update(st: UpdateStatus, run: Callable[[list[str]], int] | None = None) -> tuple[bool, str]:
    """Try to update automatically. Returns (done, message)."""
    if not st.available or not st.latest:
        return False, "Already up to date."
    run = run or (lambda c: subprocess.call(c))
    m, rel = st.install.method, st.latest
    if m == "flatpak":
        url = _asset(rel, ".flatpak")
        if not url:
            return False, "This release has no Flatpak bundle; see " + rel.url
        dl = Path(os.environ.get("XDG_DOWNLOAD_DIR", Path.home() / "Downloads"))
        dl.mkdir(parents=True, exist_ok=True)
        bundle = _download(url, dl)
        cmd = ["flatpak", "install", "--user", "-y", str(bundle)]
        if not host_commands_allowed(run):
            return False, ("Downloaded the update to " + str(bundle) + ". Install it with:\n  "
                           + " ".join(cmd) + "\n(To let catflap do this itself next time: "
                           f"flatpak override --user --talk-name=org.freedesktop.Flatpak {st.install.detail or FLATPAK_ID})")
        rc = run(["flatpak-spawn", "--host", *cmd])
    elif m == "rpm":
        url = _asset(rel, ".noarch.rpm")
        if not url:
            return False, "This release has no RPM asset; see " + rel.url
        rc = run(["pkexec", "dnf", "install", "-y", url])
    elif m == "deb":
        url = _asset(rel, "_all.deb")
        if not url:
            return False, "This release has no .deb asset; see " + rel.url
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
            return False, "This release has no Windows installer; see " + rel.url
        exe = _download(url, Path(tempfile.mkdtemp(prefix="catflap-update-")))
        subprocess.Popen([str(exe)])  # installer replaces us; caller should exit
        return True, "The installer has been started; catflap will close."
    else:
        return False, "Installed from source. Update with:\n  " + "\n  ".join(instructions(st.install, rel))
    if rc == 0:
        return True, f"Updated to {rel.version}. Restart catflap to use it."
    return False, f"Update command failed (exit {rc}). Manual steps:\n  " + "\n  ".join(instructions(st.install, rel))
