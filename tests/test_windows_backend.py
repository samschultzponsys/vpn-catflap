"""The Windows backend, driven by a fake that records commands (no Windows needed)."""

from catflap import wgconf
from catflap.model import Profile
from catflap.platform import windows
from catflap.platform.base import CmdResult


class FakeWindows:
    def __init__(self):
        self.cmds = []
        self.services = {}            # name -> state
        self.elevated = []

    def __call__(self, cmd):
        self.cmds.append(list(cmd))
        if cmd[0] == "sc":
            name = cmd[2]
            if cmd[1] == "query":
                if name not in self.services:
                    return CmdResult(1060, "service does not exist", "")
                return CmdResult(0, f"        STATE              : 4  {self.services[name]}\n", "")
            if cmd[1] == "start":
                self.services[name] = "RUNNING"
                return CmdResult(0, "", "")
            if cmd[1] == "stop":
                self.services[name] = "STOPPED"
                return CmdResult(0, "", "")
        if cmd[0] == "powershell":
            script = cmd[-1]
            if "WindowsIdentity" in script:
                return CmdResult(0, "S-1-5-21-1-2-3-1001\n", "")
            if "Start-Process" in script:
                self.elevated.append(script)
                # the elevated install creates a stopped service, uninstall removes it
                if "'-Conf'" in script:
                    self.services["WireGuardTunnel$cf-home"] = "STOPPED"
                else:
                    self.services.pop("WireGuardTunnel$cf-home", None)
                return CmdResult(0, "", "")
            return CmdResult(0, "", "")
        return CmdResult(0, "", "")


def profile():
    return Profile(name="home", label="Home", ifname="cf-home", vendor="unifi",
                   addresses=["10.250.1.2/32"], peer_public_key="P", endpoint_host="h",
                   endpoint_port=51820, routes=["192.168.1.0/24"])


def cfg():
    return wgconf.parse_text("[Interface]\nPrivateKey = K\nAddress = 10.250.1.2/32\n[Peer]\n"
                             "PublicKey = P\nAllowedIPs = 192.168.1.0/24\nEndpoint = h:51820\n")


def test_one_admin_prompt_per_site_then_no_prompts(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    exe = tmp_path / "wireguard.exe"
    exe.write_text("")
    monkeypatch.setenv("CATFLAP_WIREGUARD_EXE", str(exe))
    w = FakeWindows()
    b = windows.WindowsBackend(w)
    p = profile()
    b.install(p, cfg())
    assert len(w.elevated) == 1                                   # setup: one UAC prompt
    conf = tmp_path / "catflap" / "tunnels" / "cf-home.conf"
    assert "AllowedIPs = 192.168.1.0/24" in conf.read_text()
    b.install(p, cfg())                                           # route change: rewrite only
    assert len(w.elevated) == 1
    b.up(p)
    assert b.status(p).active and ["sc", "start", "WireGuardTunnel$cf-home"] in w.cmds
    b.down(p)
    assert not b.status(p).active
    assert len(w.elevated) == 1                                   # connect/disconnect: no prompts
    b.remove(p)
    assert len(w.elevated) == 2 and not conf.exists()


def test_missing_wireguard_is_explained(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFLAP_WIREGUARD_EXE", str(tmp_path / "nope.exe"))
    import pytest
    from catflap.platform.base import BackendError
    with pytest.raises(BackendError, match="wireguard.com"):
        windows.WindowsBackend(FakeWindows()).install(profile(), cfg())


def test_install_script_grants_the_user_start_and_stop():
    assert "(A;;CCLCRPWPRC;;;$Sid)" in windows._INSTALL_PS
    assert "start= demand" in windows._INSTALL_PS


def test_install_wireguard_downloads_official_installer(tmp_path, monkeypatch):
    exe = tmp_path / "wireguard.exe"
    monkeypatch.setenv("CATFLAP_WIREGUARD_EXE", str(exe))
    fetched = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"MZ"

    def fake_urlopen(req, timeout=0):
        fetched.append(req.full_url)
        return Resp()
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    w = FakeWindows()
    real = w.__call__

    def run(cmd):
        if cmd[0] == "powershell" and "wireguard-installer.exe" in cmd[-1]:
            exe.write_text("")                       # the installer "installs" WireGuard
            return CmdResult(0, "", "")
        return real(cmd)
    b = windows.WindowsBackend(run)
    assert not b.wireguard_installed()
    b.install_wireguard()
    assert b.wireguard_installed()
    assert fetched == ["https://download.wireguard.com/windows-client/wireguard-installer.exe"]


def test_missing_wireguard_raises_the_offerable_error(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setenv("CATFLAP_WIREGUARD_EXE", str(tmp_path / "nope.exe"))
    with pytest.raises(windows.WireGuardMissing):
        windows.WindowsBackend(FakeWindows()).install(profile(), cfg())


class FakeWinreg:
    HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER = "HKLM", "HKCU"

    def __init__(self, entries):
        self.entries = entries          # {(hive, path): {subkey: {value: data}}}

    class _K:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(self, parent, path):  # noqa: N802
        if isinstance(parent, self._K):
            return self._K(parent.data[path])
        if (parent, path) not in self.entries:
            raise OSError("missing")
        return self._K(self.entries[(parent, path)])

    def EnumKey(self, key, i):  # noqa: N802
        keys = list(key.data)
        if i >= len(keys):
            raise OSError("done")
        return keys[i]

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in key.data:
            raise OSError("no value")
        return key.data[name], 1


def test_find_wifiman_and_uninstall():
    path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    reg = FakeWinreg({("HKLM", path): {
        "Zoom": {"DisplayName": "Zoom"},
        "WiFiman Desktop": {"DisplayName": "WiFiman Desktop", "DisplayVersion": "1.2.8",
                            "UninstallString": r'"C:\Program Files\WiFiman Desktop\Uninstall WiFiman Desktop.exe" /allusers'}}})
    info = windows.find_wifiman(reg)
    assert info["name"] == "WiFiman Desktop" and info["version"] == "1.2.8" and not info["per_user"]
    assert windows.find_installed("Nothing", reg) is None
    ran = []
    windows.uninstall_program(info, lambda c: ran.append(c) or CmdResult(0, "", ""))
    assert "-Verb RunAs" in ran[0][-1] and "Uninstall WiFiman Desktop.exe" in ran[0][-1]
