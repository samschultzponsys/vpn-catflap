"""Start-at-login for the tray app (XDG autostart on Linux, HKCU Run on Windows)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=CatFlap
Comment=WireGuard remote access to your sites
Exec={exec} tray --minimized
Icon={icon}
Terminal=false
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after=panel
"""
RUN_KEY = r"Software\\Microsoft\\Windows\\CurrentVersion\\Run"


def _autostart_file() -> Path:
    if os.environ.get("FLATPAK_ID"):
        # Inside the sandbox XDG_CONFIG_HOME is the app's private dir; the session
        # reads ~/.config/autostart (granted with --filesystem=xdg-config/autostart:create).
        base = Path.home() / ".config"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "autostart" / "catflap.desktop"


def _exec_cmd() -> str:
    if os.environ.get("FLATPAK_ID"):
        return f"flatpak run {os.environ['FLATPAK_ID']}"
    exe = shutil.which("catflap")
    return exe or f"{sys.executable} -m catflap"


def is_enabled() -> bool:
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, "catflap")
                return True
        except OSError:
            return False
    return _autostart_file().exists()


def set_enabled(enabled: bool) -> None:
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                if getattr(sys, "frozen", False):        # installed CatFlap.exe
                    cmd = f'"{sys.executable}" --minimized'
                else:
                    cmd = f'"{sys.executable}" -m catflap tray --minimized'
                winreg.SetValueEx(k, "catflap", 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, "catflap")
                except OSError:
                    pass
        return
    f = _autostart_file()
    if enabled:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(DESKTOP_ENTRY.format(exec=_exec_cmd(),
                                          icon=os.environ.get("FLATPAK_ID", "catflap")),
                     encoding="utf-8")
    else:
        f.unlink(missing_ok=True)
