"""The Windows backend, driven by a fake that records commands (no Windows needed)."""

from catflap import wgconf
from catflap.model import Profile
from catflap.platform import windows
from catflap.platform.base import CmdResult


class FakeWindows:
    def __init__(self):
        self.cmds = []
        self.services = {}            # name -> state
        self.programs = {}            # name -> program the service runs
        self.elevated = []

    def __call__(self, cmd):
        self.cmds.append(list(cmd))
        if cmd[0] == "sc":
            name = cmd[2]
            if cmd[1] == "query":
                if name not in self.services:
                    return CmdResult(1060, "service does not exist", "")
                return CmdResult(0, f"        STATE              : 4  {self.services[name]}\n", "")
            if cmd[1] == "qc":
                if name not in self.services:
                    return CmdResult(1060, "service does not exist", "")
                prog = self.programs.get(name)
                if prog is None:
                    return CmdResult(0, "", "")
                return CmdResult(0, f'        BINARY_PATH_NAME   : "{prog}" /tunnelservice "c.conf"\n', "")
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
                if '-Conf"' in script:
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
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    import pytest
    from catflap.platform.base import BackendError
    with pytest.raises(BackendError, match="wireguard.com"):
        windows.WindowsBackend(FakeWindows()).install(profile(), cfg())


def test_install_script_grants_the_user_start_and_stop():
    assert "(A;;CCLCRPWPRC;;;$Sid)" in windows._INSTALL_PS
    assert "start= demand" in windows._INSTALL_PS


LISTING = ("untrusted comment: verify with wireguard-windows-release.pub\nRWQ...\n"
           "f64b  wireguard-amd64-1.1.1.msi\n1390  wireguard-arm64-1.1.1.msi\n"
           "b769  wireguard-x86-1.1.1.msi\n")


def test_latest_msi_name():
    assert windows.latest_msi_name(LISTING, "amd64") == "wireguard-amd64-1.1.1.msi"
    assert windows.latest_msi_name(LISTING, "arm64") == "wireguard-arm64-1.1.1.msi"
    assert windows.latest_msi_name("nothing", "amd64") is None


def _engine_env(tmp_path, monkeypatch):
    monkeypatch.delenv("CATFLAP_WIREGUARD_EXE", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramW6432", str(tmp_path / "pf"))
    monkeypatch.setattr(windows, "DEFAULT_WIREGUARD", str(tmp_path / "pf" / "WireGuard" / "wireguard.exe"))
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "AMD64")
    monkeypatch.delenv("PROCESSOR_ARCHITEW6432", raising=False)


def test_install_wireguard_downloads_the_msi_without_installing_it(tmp_path, monkeypatch):
    _engine_env(tmp_path, monkeypatch)
    fetched = []

    class Resp:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.data

    def fake_urlopen(req, timeout=0):
        fetched.append(req.full_url)
        return Resp(LISTING.encode() if req.full_url.endswith(".sig") else b"MSI")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    w = FakeWindows()
    b = windows.WindowsBackend(w)
    assert not b.wireguard_installed()
    b.install_wireguard()
    assert b.wireguard_installed() and not w.cmds                  # nothing run, nothing installed
    assert fetched == ["https://download.wireguard.com/windows-client/latest.sig",
                       "https://download.wireguard.com/windows-client/wireguard-amd64-1.1.1.msi"]
    assert b.downloaded_msi() == tmp_path / "local" / "catflap" / "wireguard" / "wireguard-amd64-1.1.1.msi"


def test_first_site_extracts_the_engine_in_the_same_prompt(tmp_path, monkeypatch):
    _engine_env(tmp_path, monkeypatch)
    msi_dir = tmp_path / "local" / "catflap" / "wireguard"
    msi_dir.mkdir(parents=True)
    (msi_dir / "wireguard-amd64-1.1.1.msi").write_bytes(b"MSI")
    w = FakeWindows()
    b = windows.WindowsBackend(w)
    assert b.exe == str(tmp_path / "pf" / "CatFlap" / "WireGuard" / "wireguard.exe")
    b.install(profile(), cfg())
    assert len(w.elevated) == 1
    script = w.elevated[0]
    assert '"-Msi"' in script and "wireguard-amd64-1.1.1.msi" in script
    assert str(tmp_path / "pf" / "CatFlap" / "WireGuard") in script
    assert not (msi_dir / "wireguard-amd64-1.1.1.msi").exists()   # used up


def test_engine_preferred_over_wireguard_app_and_app_used_without_engine(tmp_path, monkeypatch):
    _engine_env(tmp_path, monkeypatch)
    app = tmp_path / "pf" / "WireGuard" / "wireguard.exe"
    app.parent.mkdir(parents=True)
    app.write_text("")
    b = windows.WindowsBackend(FakeWindows())
    assert b.exe == str(app) and b.wireguard_installed()           # opted out: use the app
    b.engine.parent.mkdir(parents=True)
    b.engine.write_text("")
    assert b.exe == str(b.engine)                                  # engine: no second UI


def test_install_script_checks_signature_and_extracts_not_installs():
    ps = windows._INSTALL_PS
    assert "Get-AuthenticodeSignature" in ps and "WireGuard LLC" in ps
    assert "'/a'" in ps and "/i" not in ps.replace("/installtunnelservice", "")


def test_connect_repairs_a_site_whose_program_is_gone(tmp_path, monkeypatch):
    _engine_env(tmp_path, monkeypatch)
    w = FakeWindows()
    b = windows.WindowsBackend(w)
    b.engine.parent.mkdir(parents=True)
    b.engine.write_text("")
    p = profile()
    b.install(p, cfg())
    assert len(w.elevated) == 1
    # WireGuard for Windows was uninstalled; the service still points at it
    w.programs["WireGuardTunnel$cf-home"] = str(tmp_path / "pf" / "WireGuard" / "wireguard.exe")
    b.up(p)
    assert len(w.elevated) == 2 and b.status(p).active


def test_remove_deletes_service_and_maybe_engine(tmp_path, monkeypatch):
    _engine_env(tmp_path, monkeypatch)
    w = FakeWindows()
    b = windows.WindowsBackend(w)
    w.services["WireGuardTunnel$cf-home"] = "STOPPED"
    b.remove(profile())
    assert '"-Names"' in w.elevated[0] and '"cf-home"' in w.elevated[0]
    assert "Remove-Item" in windows._UNINSTALL_PS and "sc.exe delete" in windows._UNINSTALL_PS


def test_elevated_arguments_with_spaces_are_quoted(tmp_path, monkeypatch):
    _engine_env(tmp_path, monkeypatch)
    w = FakeWindows()
    b = windows.WindowsBackend(w)
    b._elevated_script("x", {"Wg": r"C:\Program Files\CatFlap\WireGuard\wireguard.exe"}, "t")
    assert "'\"C:\\Program Files\\CatFlap\\WireGuard\\wireguard.exe\"'" in w.elevated[0]


def test_find_wireguard_app_is_exact():
    path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    reg = FakeWinreg({("HKLM", path): {
        "a": {"DisplayName": "WireGuard Something Else"},
        "b": {"DisplayName": "WireGuard", "DisplayVersion": "1.1.1", "UninstallString": "MsiExec.exe /X{X}"}}})
    info = windows.find_wireguard_app(reg)
    assert info["name"] == "WireGuard" and info["version"] == "1.1.1"


def test_missing_wireguard_raises_the_offerable_error(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setenv("CATFLAP_WIREGUARD_EXE", str(tmp_path / "nope.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
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
